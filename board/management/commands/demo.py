from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Membership, User, Workspace
from board.models import Comment, Entry, Field, FieldOption, Item, Link, PhaseSpan


class Command(BaseCommand):
    help = "Fill an empty database with a small example board (demo@example.com / demo-password)."

    def handle(self, *args, **options):
        if User.objects.exists():
            self.stdout.write("Database is not empty, leaving it alone.")
            return
        ws = Workspace.objects.create(name="Obrydd")
        people = [
            User.objects.create_user("demo@example.com", "demo-password", name="Martin Palacios"),
            User.objects.create_user("sara@example.com", "demo-password", name="Sara Lind"),
            User.objects.create_user("jonas@example.com", "demo-password", name="Jonas Ek"),
        ]
        for i, u in enumerate(people):
            Membership.objects.create(workspace=ws, user=u, role="owner" if i == 0 else "member")
        m, s, j = people
        area = Field.objects.create(workspace=ws, name="Area", position=0)
        opts = {}
        for i, (label, color) in enumerate([("Product", "blue"), ("Customers", "green"), ("Operations", "orange")]):
            opts[label] = FieldOption.objects.create(field=area, label=label, color=color, position=i)

        now = timezone.now()
        today = timezone.localdate()

        def make(title, phase, who, area_label, days_ago, body="", prio=False, deadline=None, cycle=1, parent=None):
            item = Item.objects.create(workspace=ws, title=title, body=body, created_by=m, is_priority=prio,
                                       deadline=deadline, parent=parent)
            item.options.add(opts[area_label])
            item.assignees.add(*who)
            order = ["observe", "orient", "decide", "act"]
            steps = (cycle - 1) * 4 + order.index(phase)
            for _ in range(steps):
                item.advance(m)
            spans = list(item.spans.order_by("pk"))
            start = now - timedelta(days=days_ago)
            gap = timedelta(days=days_ago) / (len(spans) + 1)
            for i, sp in enumerate(spans):
                sp.started_at = start + gap * i
                sp.ended_at = start + gap * (i + 1) if i < len(spans) - 1 else None
                sp.save()
            return item

        onboarding = make(
            "Customers drop off during onboarding", "orient", [m, s], "Customers", 9,
            body="Since the new sign-up flow, fewer customers finish setting up their first board. "
                 "We need to understand where they stop and why before we change anything.\n\n"
                 "Done when the drop-off is back under 20%.",
            prio=True, deadline=today + timedelta(days=5), cycle=2,
        )
        Entry.objects.create(workspace=ws, item=onboarding, kind="observation", author=s, cycle=1,
                             body="Funnel shows 41% stop at the 'invite your team' step.")
        Entry.objects.create(workspace=ws, item=onboarding, kind="orientation", author=m, cycle=1,
                             body="Most of them are solo users. Asking for a team too early feels like a wall.")
        Entry.objects.create(workspace=ws, item=onboarding, kind="decision", author=m, cycle=1,
                             body="Make the invite step skippable and move it to after the first item.")
        Entry.objects.create(workspace=ws, item=onboarding, kind="action", author=j, cycle=1,
                             body="Shipped the skippable step on Tuesday. @sara can you watch the numbers?")
        Entry.objects.create(workspace=ws, item=onboarding, kind="observation", author=s, cycle=2,
                             body="Drop-off at that step is down to 18%, but more people now stop at 'create first item'.")
        Comment.objects.create(item=onboarding, target="body", quote="fewer customers finish setting up",
                               body="How many fewer? Let's put the number here.", author=j)
        Link.objects.create(item=onboarding, url="https://example.com/funnel", label="Funnel dashboard", added_by=s)

        server = make("Move hosting to a server in the EU", "decide", [j], "Operations", 6,
                      body="Compare two or three providers on price, location and backups.",
                      deadline=today + timedelta(days=12))
        backups = make("Set up nightly backups", "observe", [j], "Operations", 1, parent=server)
        dns = make("Point ooda.obrydd.com to the new server", "observe", [], "Operations", 1, parent=server)
        dns.depends_on.add(server)
        make("Write the pricing page", "act", [m], "Product", 4, deadline=today - timedelta(days=1), prio=True)
        make("Interview five people who left", "observe", [s], "Customers", 2)
        make("Weekly summary e-mail", "orient", [], "Product", 3)
        make("Clean up the support inbox", "act", [s, j], "Operations", 11, cycle=3)
        Entry.objects.create(workspace=ws, kind="observation", author=s,
                             body="Two customers asked whether the board can be shown on a TV in the office.")
        Entry.objects.create(workspace=ws, kind="orientation", author=j,
                             body="Support questions spike every Monday morning — maybe people plan their week then?")
        self.stdout.write("Demo board created. Sign in as demo@example.com / demo-password")
