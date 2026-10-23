from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Membership, User, Workspace
from board.models import Comment, Entry, Field, FieldOption, Item, Link, Reply, Verdict


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
            item = Item.objects.create(workspace=ws, title=title, created_by=m, is_priority=prio,
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
            if body:
                doc = item.document
                doc.body = body
                doc.save()
            return item

        onboarding = make(
            "Customers drop off during onboarding", "orient", [m, s], "Customers", 9,
            body=(
                "<p>Since the new sign-up flow, fewer customers finish setting up their first board. "
                "We need to understand where they stop and why before we change anything.</p>"
                "<h2>What we see</h2>"
                "<ul><li>The funnel shows 41% stop at the step where they invite their team.</li>"
                "<li>Most of those who stop work alone.</li></ul>"
                "<h2>What we think it means</h2>"
                "<p>Asking for a team too early feels like a wall. Solo users have nobody to invite, "
                "so the step reads as \u201cthis isn\u2019t for you\u201d.</p>"
                "<h2>What we do</h2>"
                "<p>Make the invite step skippable and move it to after the first item. "
                "Then watch the numbers for two weeks.</p>"
                "<p>Done when the drop-off is back under 20%.</p>"
            ),
            prio=True, deadline=today + timedelta(days=5), cycle=2,
        )
        history = {
            ("observe", 1): "<h3>What happened?</h3><p>Fewer new customers finish setting up their first board "
                            "since the sign-up flow changed on 1 September.</p><h3>How do we know?</h3>"
                            "<p>The funnel shows 41% stop at the step where they invite their team.</p>",
            ("orient", 1): "<p>Most who stop work alone. Asking for a team before they have seen any value "
                           "feels like a wall.</p>",
            ("decide", 1): (
                "<h3>Assumptions from Orient to validate</h3><ul><li>Solo users see no reason to invite anyone.</li></ul>"
                "<h3>Experiment 1</h3><ul>"
                "<li><strong>Hypothesis:</strong> Solo users stop because the invite step comes too early.</li>"
                "<li><strong>What we do:</strong> Make the invite step skippable for two weeks.</li>"
                "<li><strong>How we measure:</strong> Share who stop at the invite step, from the funnel.</li>"
                "<li><strong>It holds if:</strong> Drop-off at that step falls below 20%.</li>"
                "<li><strong>It fails if:</strong> It stays above 35%.</li>"
                "<li><strong>If it fails, we will:</strong> Interview five who stopped before changing anything else.</li>"
                "<li><strong>Time and cost:</strong> Two weeks, half a day of work.</li></ul>"
            ),
            ("act", 1): (
                "<h3>Journal</h3><p><strong>7 Oct · Jonas Ek</strong> — Shipped the skippable step.</p>"
                "<h3>Results as observations</h3><ul><li><strong>Experiment 1 — Solo users stop because the invite "
                "step comes too early:</strong> Drop-off at the invite step went from 41% to 18% in two weeks.</li></ul>"
            ),
            ("observe", 2): "<p>Drop-off at the invite step is down to 18%, but more people now stop at "
                            "\u201ccreate first item\u201d.</p>",
        }
        for (phase, cycle), html in history.items():
            onboarding.documents.filter(phase=phase, cycle=cycle).update(body=html, status="done")
        doc = onboarding.document
        doc.status = "review"
        doc.submitted_by = m
        doc.submitted_at = now
        doc.save()
        c1 = Comment.objects.create(item=onboarding, document=doc, quote="fewer customers finish setting up", prefix="Since the new sign-up flow, ",
                                    body="How many fewer? Let's put the number here.", author=j)
        Reply.objects.create(comment=c1, body="About a third fewer than in August. I'll add the chart.", author=s)
        Comment.objects.create(item=onboarding, document=doc, kind="suggestion", quote="two weeks", prefix="Then watch the numbers for ",
                               replacement="three weeks, so we see a full billing cycle", body="Two weeks misses the monthly payers.",
                               author=j)
        Comment.objects.create(item=onboarding, document=doc, quote="Most of those who stop work alone.",
                               body="@martin do we know this from the data or from interviews?", author=s)
        Link.objects.create(item=onboarding, url="https://example.com/funnel", label="Funnel dashboard", added_by=s)

        Verdict.objects.create(item=onboarding, cycle=1, experiment="Experiment 1", outcome="held", decided_by=m)
        Entry.objects.create(workspace=ws, item=onboarding, kind="observation", author=j,
                             body="Three trial users asked support this week how to skip the team step entirely.")
        server = make("Move hosting to a server in the EU", "decide", [j], "Operations", 6,
                      body="<p>Compare two or three providers on price, location and backups.</p>",
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
