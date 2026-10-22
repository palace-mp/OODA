from datetime import timedelta

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Invite, Membership, User, Workspace

from .models import ACT, DECIDE, OBSERVE, ORIENT, Comment, Entry, Field, FieldOption, Item, PhaseSpan


class Base(TestCase):
    def setUp(self):
        self.ws = Workspace.objects.create(name="Ops")
        self.anna = User.objects.create_user("anna@example.com", "pw-anna-123!", name="Anna Berg")
        self.bo = User.objects.create_user("bo@example.com", "pw-bo-123!", name="Bo Ek")
        Membership.objects.create(workspace=self.ws, user=self.anna, role=Membership.OWNER)
        Membership.objects.create(workspace=self.ws, user=self.bo)
        self.client.force_login(self.anna)

    def item(self, title="Fix the gate", **kw):
        return Item.objects.create(workspace=self.ws, title=title, created_by=self.anna, **kw)


@override_settings(SETUP_KEY="")
class SetupAndInviteTests(TestCase):
    @override_settings(SETUP_KEY="k")
    def test_setup_key_guards_first_run(self):
        self.assertEqual(self.client.get(reverse("start")).status_code, 404)
        self.assertEqual(self.client.get(reverse("start") + "?key=k").status_code, 200)

    def test_first_visit_creates_owner_and_board(self):
        self.assertRedirects(self.client.get("/"), reverse("start"))
        r = self.client.post(reverse("start"), {
            "workspace": "Ops", "name": "Anna", "email": "Anna@Example.com", "password": "a-long-secret-1",
        })
        self.assertRedirects(r, reverse("board:index"))
        user = User.objects.get()
        self.assertEqual(user.email, "anna@example.com")
        self.assertTrue(user.memberships.get().is_owner)
        # Nobody else can use the setup page afterwards.
        self.client.logout()
        self.assertEqual(self.client.get(reverse("start")).status_code, 404)

    def test_invite_link_creates_member(self):
        ws = Workspace.objects.create(name="Ops")
        invite = Invite.objects.create(workspace=ws)
        r = self.client.post(reverse("join", args=[invite.token]), {
            "name": "Bo", "email": "bo@example.com", "password": "a-long-secret-1",
        })
        self.assertRedirects(r, reverse("board:index"))
        self.assertEqual(Membership.objects.get().role, Membership.MEMBER)

    def test_expired_invite_is_refused(self):
        ws = Workspace.objects.create(name="Ops")
        invite = Invite.objects.create(workspace=ws, expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.client.get(reverse("join", args=[invite.token])).status_code, 410)


class LoopTests(Base):
    def test_numbers_are_per_workspace(self):
        self.assertEqual(self.item().number, 1)
        self.assertEqual(self.item().number, 2)
        other = Workspace.objects.create(name="Other")
        self.assertEqual(Item.objects.create(workspace=other, title="x").number, 1)

    def test_full_loop_counts_a_new_cycle(self):
        item = self.item()
        for expected in (ORIENT, DECIDE, ACT, OBSERVE):
            self.client.post(reverse("board:phase", args=[item.number]), {"phase": "next"})
            item.refresh_from_db()
            self.assertEqual(item.phase, expected)
        self.assertEqual(item.cycle, 2)
        self.assertEqual(item.spans.count(), 5)
        self.assertEqual(item.spans.filter(ended_at=None).count(), 1)

    def test_entry_can_move_item_along(self):
        item = self.item()
        self.client.post(reverse("board:entry", args=[item.number]),
                         {"kind": "observation", "body": "Gate is stuck", "advance": "1"})
        item.refresh_from_db()
        self.assertEqual(item.phase, ORIENT)
        self.assertEqual(item.entries.get().cycle, 1)

    def test_done_closes_open_span(self):
        item = self.item()
        self.client.post(reverse("board:done", args=[item.number]))
        item.refresh_from_db()
        self.assertTrue(item.is_done)
        self.assertFalse(item.spans.filter(ended_at=None).exists())


class InboxTests(Base):
    def test_loose_entry_becomes_item(self):
        self.client.post(reverse("board:inbox_entry"), {"kind": "orientation", "body": "Customers churn after onboarding\nmore"})
        entry = Entry.objects.get()
        self.assertIsNone(entry.item)
        self.client.post(reverse("board:file_entry", args=[entry.pk]), {"into": "new", "title": ""})
        entry.refresh_from_db()
        self.assertEqual(entry.item.title, "Customers churn after onboarding")
        self.assertEqual(entry.item.phase, ORIENT)

    def test_loose_entry_filed_under_item(self):
        item = self.item()
        entry = Entry.objects.create(workspace=self.ws, kind="observation", body="x", author=self.bo)
        self.client.post(reverse("board:file_entry", args=[entry.pk]), {"into": f"#{item.number}"})
        entry.refresh_from_db()
        self.assertEqual(entry.item, item)


class StructureTests(Base):
    def test_merge_moves_everything(self):
        a, b, c = self.item("A"), self.item("B", body="details"), self.item("C")
        Entry.objects.create(workspace=self.ws, item=b, body="seen", author=self.bo)
        Comment.objects.create(item=b, body="hm", author=self.bo)
        b.assignees.add(self.bo)
        c.depends_on.add(b)
        r = self.client.post(reverse("board:merge", args=[b.number]), {"into": str(a.number)})
        self.assertRedirects(r, a.get_absolute_url())
        b.refresh_from_db()
        self.assertEqual(b.merged_into, a)
        self.assertTrue(b.is_done)
        self.assertEqual(a.entries.count(), 2)  # the moved entry and a note with b's description
        self.assertEqual(a.comments.count(), 1)
        self.assertIn(self.bo, a.assignees.all())
        self.assertIn(a, c.depends_on.all())
        self.assertRedirects(self.client.get(b.get_absolute_url()), a.get_absolute_url())

    def test_dependency_loops_are_refused(self):
        a, b = self.item("A"), self.item("B")
        self.client.post(reverse("board:depend", args=[a.number]), {"other": b.number})
        self.client.post(reverse("board:depend", args=[b.number]), {"other": a.number})
        self.assertEqual(list(a.depends_on.all()), [b])
        self.assertFalse(b.depends_on.exists())
        self.assertTrue(a.is_blocked)

    def test_finishing_dependency_tells_assignees(self):
        a, b = self.item("A"), self.item("B")
        a.depends_on.add(b)
        a.assignees.add(self.bo)
        self.client.post(reverse("board:done", args=[b.number]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["bo@example.com"])

    def test_parent_cannot_be_own_child(self):
        a = self.item("A")
        b = self.item("B", parent=a)
        r = self.client.post(reverse("board:edit", args=[a.number]), {"title": "A", "parent": b.pk})
        a.refresh_from_db()
        self.assertIsNone(a.parent)
        self.assertEqual(r.status_code, 302)


class NotificationTests(Base):
    def test_assigning_and_mentioning_sends_mail(self):
        item = self.item()
        self.client.post(reverse("board:edit", args=[item.number]),
                         {"title": item.title, "assignees": [self.bo.pk], "body": "ask @bo"})
        subjects = sorted(m.subject for m in mail.outbox)
        self.assertEqual(len(subjects), 2)
        self.assertTrue(any("assigned" in s for s in subjects))
        self.assertTrue(any("mentioned" in s for s in subjects))

    def test_comment_mention(self):
        item = self.item(body="The gate opens slowly")
        self.client.post(reverse("board:comment", args=[item.number]),
                         {"target": "body", "quote": "opens slowly", "body": "@bo how slow?"})
        self.assertEqual(Comment.objects.get().quote, "opens slowly")
        self.assertEqual(mail.outbox[0].to, ["bo@example.com"])

    def test_deadline_reminder_once_a_day(self):
        item = self.item(deadline=timezone.localdate())
        item.assignees.add(self.bo)
        call_command("send_reminders", stdout=open("/dev/null", "w"))
        call_command("send_reminders", stdout=open("/dev/null", "w"))
        self.assertEqual(len(mail.outbox), 1)


class PageTests(Base):
    def test_pages_render(self):
        a = self.item("A", body="see https://example.com and #2")
        self.item("B", parent=a)
        field = Field.objects.create(workspace=self.ws, name="Area")
        opt = FieldOption.objects.create(field=field, label="North")
        a.options.add(opt)
        Comment.objects.create(item=a, body="x", quote="see", author=self.bo)
        for url in [reverse("board:index"), reverse("board:index") + f"?view=board&group={field.pk}",
                    reverse("board:index") + f"?q=A&opt={opt.pk}&who=me&due=week&prio=1&state=all",
                    a.get_absolute_url(), reverse("board:inbox"), reverse("board:tempo"),
                    reverse("settings"), reverse("profile"), reverse("board:new")]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_filter_by_option(self):
        a, b = self.item("A"), self.item("B")
        field = Field.objects.create(workspace=self.ws, name="Area")
        north = FieldOption.objects.create(field=field, label="North")
        a.options.add(north)
        r = self.client.get(reverse("board:index"), {"opt": north.pk})
        self.assertEqual([i.pk for i in r.context["items"]], [a.pk])

    def test_drag_to_field_column(self):
        item = self.item()
        field = Field.objects.create(workspace=self.ws, name="Area")
        north, south = (FieldOption.objects.create(field=field, label=n) for n in ("N", "S"))
        item.options.add(north)
        self.client.post(reverse("board:move", args=[item.number]), {"group": field.pk, "value": south.pk})
        self.assertEqual(list(item.options.all()), [south])
        self.client.post(reverse("board:move", args=[item.number]), {"group": "phase", "value": "decide"})
        item.refresh_from_db()
        self.assertEqual(item.phase, DECIDE)

    def test_public_page(self):
        item = self.item("Visible")
        url = reverse("public_index", args=[self.ws.public_token])
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.ws.is_public = True
        self.ws.save()
        r = self.client.get(url)
        self.assertContains(r, "Visible")
        self.assertNotContains(r, "csrfmiddlewaretoken")
        r = self.client.get(reverse("public_item", args=[self.ws.public_token, item.number]))
        self.assertContains(r, "Visible")

    def test_members_cannot_change_settings(self):
        self.client.force_login(self.bo)
        self.assertEqual(self.client.post(reverse("create_invite")).status_code, 403)
        self.assertEqual(self.client.post(reverse("publish"), {"action": "publish"}).status_code, 403)

    def test_other_workspace_is_invisible(self):
        other = Workspace.objects.create(name="Other")
        stranger = Item.objects.create(workspace=other, title="secret")
        self.assertEqual(self.client.get(reverse("board:item", args=[stranger.number])).status_code, 404)

    def test_last_owner_stays(self):
        m = self.anna.memberships.get()
        self.client.force_login(self.anna)
        self.client.post(reverse("change_member", args=[m.pk]), {"action": "member"})
        m.refresh_from_db()
        self.assertTrue(m.is_owner)

    def test_tempo_counts_loops(self):
        item = self.item()
        for _ in range(4):
            item.advance()
        now = timezone.now()
        first, second = item.spans.filter(phase=OBSERVE).order_by("cycle")
        first.started_at = now - timedelta(days=3)
        first.save()
        second.started_at = now - timedelta(days=1)
        second.save()
        r = self.client.get(reverse("board:tempo"))
        self.assertEqual(r.context["loops_closed"], 1)
        self.assertEqual(r.context["loop_median"], timedelta(days=2))
