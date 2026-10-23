import json
from datetime import timedelta

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Invite, Membership, User, Workspace

from .models import ACT, DECIDE, OBSERVE, ORIENT, Comment, Document, Entry, Field, FieldOption, Item, PhaseSpan, Reply, Verdict


class Base(TestCase):
    def setUp(self):
        self.ws = Workspace.objects.create(name="Ops")
        self.anna = User.objects.create_user("anna@example.com", "pw-anna-123!", name="Anna Berg")
        self.bo = User.objects.create_user("bo@example.com", "pw-bo-123!", name="Bo Ek")
        Membership.objects.create(workspace=self.ws, user=self.anna, role=Membership.OWNER)
        Membership.objects.create(workspace=self.ws, user=self.bo)
        self.client.force_login(self.anna)

    def item(self, title="Fix the gate", body=None, **kw):
        item = Item.objects.create(workspace=self.ws, title=title, created_by=self.anna, **kw)
        if body is not None:
            Document.objects.filter(pk=item.document.pk).update(body=body)
        return item


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
        item = Item.objects.get()
        self.assertEqual(item.title, "Customers churn after onboarding")
        self.assertEqual(item.phase, ORIENT)
        self.assertTrue(item.document.body.startswith("<p>Customers churn after onboarding<br>more</p><h3>"))
        self.assertFalse(Entry.objects.exists())

    def test_filed_note_waits_on_the_item_as_an_observation(self):
        item = self.item(body="<p>Start</p>")
        self.client.force_login(self.bo)
        entry = Entry.objects.create(workspace=self.ws, kind="observation", body="Gate <stuck>", author=self.bo)
        self.client.post(reverse("board:file_entry", args=[entry.pk]), {"into": f"#{item.number}"})
        self.assertEqual(list(item.pending_observations()), [entry])
        self.assertEqual(item.document.body, "<p>Start</p>")
        self.assertContains(self.client.get(item.get_absolute_url()), "Gate &lt;stuck&gt;")


class StructureTests(Base):
    def test_merge_moves_everything(self):
        a, b, c = self.item("A", body="<p>a</p>"), self.item("B", body="<p>details</p>"), self.item("C")
        Comment.objects.create(item=b, document=b.document, body="hm", author=self.bo)
        b.assignees.add(self.bo)
        c.depends_on.add(b)
        r = self.client.post(reverse("board:merge", args=[b.number]), {"into": str(a.number)})
        self.assertRedirects(r, a.get_absolute_url())
        b.refresh_from_db()
        self.assertEqual(b.merged_into, a)
        self.assertTrue(b.is_done)
        self.assertEqual(a.document.body, "<p>a</p><h3>#2 B</h3><p>details</p>")
        self.assertEqual(a.document.comments.count(), 1)
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
        r = self.client.post(reverse("board:edit", args=[a.number]), {"parent": b.pk})
        a.refresh_from_db()
        self.assertIsNone(a.parent)
        self.assertEqual(r.status_code, 302)


class NotificationTests(Base):
    def test_assigning_sends_mail(self):
        item = self.item()
        self.client.post(reverse("board:edit", args=[item.number]), {"assignees": [self.bo.pk]})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("assigned", mail.outbox[0].subject)

    def test_new_mention_in_document_sends_mail_once(self):
        item = self.item()
        save = reverse("board:save", args=[item.number])
        self.client.post(save, {"body": "<p>ask @bo.</p>", "version": 0})
        self.client.post(save, {"body": "<p>ask @bo again</p>", "version": 1})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("mentioned", mail.outbox[0].subject)

    def test_comment_mention(self):
        item = self.item(body="<p>The gate opens slowly</p>")
        self.client.post(reverse("board:comment", args=[item.number]),
                         {"quote": "opens slowly", "prefix": "The gate ", "body": "@bo how slow?"})
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
        a = self.item("A", body="<p>see https://example.com and #2</p>")
        self.item("B", parent=a)
        field = Field.objects.create(workspace=self.ws, name="Area")
        opt = FieldOption.objects.create(field=field, label="North")
        a.options.add(opt)
        c = Comment.objects.create(item=a, document=a.document, body="x", quote="see", author=self.bo)
        Reply.objects.create(comment=c, body="y", author=self.anna)
        Comment.objects.create(item=a, document=a.document, kind="suggestion", quote="see", replacement="look", author=self.bo)
        Comment.objects.create(item=a, document=a.document, body="old", status="resolved", author=self.bo)
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


class DocumentTests(Base):
    """Owners, assignees and the creator edit; other members comment and suggest."""

    def setUp(self):
        super().setUp()
        self.carl = User.objects.create_user("carl@example.com", "pw-carl-123!", name="Carl Ny")
        Membership.objects.create(workspace=self.ws, user=self.carl)
        self.doc = self.item("Gate", body="<p>The gate opens slowly.</p>")
        self.save = reverse("board:save", args=[self.doc.number])

    def test_editor_saves_and_html_is_cleaned(self):
        r = self.client.post(self.save, {
            "title": "Gate, again", "version": 0,
            "body": '<h2 onclick="x()">Now</h2><p>ok <script>alert(1)</script><a href="javascript:x">l</a></p>',
        })
        self.assertEqual(r.json(), {"version": 1})
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.title, "Gate, again")
        self.assertEqual(self.doc.document.body, '<h2>Now</h2><p>ok <a rel="noopener noreferrer">l</a></p>')

    def test_stale_version_is_refused(self):
        self.client.post(self.save, {"body": "<p>one</p>", "version": 0})
        r = self.client.post(self.save, {"body": "<p>two</p>", "version": 0})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.doc.document.body, "<p>one</p>")

    def test_who_may_edit(self):
        self.client.force_login(self.carl)
        self.assertEqual(self.client.post(self.save, {"body": "<p>x</p>", "version": 0}).status_code, 403)
        self.assertEqual(self.client.post(reverse("board:phase", args=[self.doc.number]),
                                          {"phase": "next"}).status_code, 403)
        self.assertNotContains(self.client.get(self.doc.get_absolute_url()), 'contenteditable="true"')
        self.doc.assignees.add(self.carl)
        self.assertEqual(self.client.post(self.save, {"body": "<p>x</p>", "version": 0}).status_code, 200)
        self.assertContains(self.client.get(self.doc.get_absolute_url()), 'contenteditable="true"')

    def suggest(self, as_user):
        self.client.force_login(as_user)
        self.client.post(reverse("board:comment", args=[self.doc.number]), {
            "kind": "suggestion", "quote": "slowly", "prefix": "The gate opens ", "replacement": "too slowly",
        })
        return Comment.objects.get(kind="suggestion")

    def test_member_suggests_and_editor_accepts(self):
        c = self.suggest(self.carl)
        self.assertEqual(c.replacement, "too slowly")
        self.assertEqual([m.to for m in mail.outbox], [["anna@example.com"]])  # no assignees: the owners
        status = reverse("board:comment_status", args=[c.pk])
        self.assertEqual(self.client.post(status, {"action": "accept"}).status_code, 403)
        self.client.force_login(self.anna)
        self.client.post(status, {"action": "accept", "version": 0, "body": "<p>The gate opens too slowly.</p>"})
        c.refresh_from_db()
        self.doc.refresh_from_db()
        self.assertEqual(c.status, Comment.ACCEPTED)
        self.assertEqual(self.doc.document.body, "<p>The gate opens too slowly.</p>")
        self.assertEqual(mail.outbox[-1].to, ["carl@example.com"])

    def test_editor_rejects_and_author_may_withdraw(self):
        c = self.suggest(self.carl)
        status = reverse("board:comment_status", args=[c.pk])
        self.client.post(status, {"action": "resolve"})  # carl withdraws his own
        c.refresh_from_db()
        self.assertEqual(c.status, Comment.RESOLVED)
        c.close(Comment.OPEN, None)
        self.client.force_login(self.anna)
        self.client.post(status, {"action": "reject"})
        c.refresh_from_db()
        self.assertEqual(c.status, Comment.REJECTED)

    def test_members_cannot_close_others_comments(self):
        c = Comment.objects.create(item=self.doc, document=self.doc.document, quote="gate", body="?", author=self.anna)
        self.client.force_login(self.carl)
        r = self.client.post(reverse("board:comment_status", args=[c.pk]), {"action": "resolve"})
        self.assertEqual(r.status_code, 403)

    def test_reply_notifies_the_thread(self):
        c = Comment.objects.create(item=self.doc, document=self.doc.document, quote="gate", body="?", author=self.carl)
        Reply.objects.create(comment=c, body="first", author=self.bo)
        self.client.post(reverse("board:reply", args=[c.pk]), {"body": "Agreed"})
        self.assertEqual(c.replies.count(), 2)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["bo@example.com", "carl@example.com"])

    def test_public_page_shows_threads_without_forms(self):
        Comment.objects.create(item=self.doc, document=self.doc.document, quote="gate", body="Visible remark", author=self.carl)
        self.ws.is_public = True
        self.ws.save()
        self.client.logout()
        r = self.client.get(reverse("public_item", args=[self.ws.public_token, self.doc.number]))
        self.assertContains(r, "Visible remark")
        self.assertNotContains(r, "<form")
        self.assertNotContains(r, "contenteditable")


class PhaseDocumentTests(Base):
    def test_each_phase_gets_its_own_document(self):
        item = self.item(body="<p>We saw it.</p>")
        first = item.document
        self.assertEqual(first.phase, OBSERVE)
        item.advance(self.anna)
        second = item.document
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.phase, ORIENT)
        self.assertIn("<h3>What do our own data say? Numbers, logs, sales, support</h3>", second.body)
        self.assertEqual(second.previous(), first)
        first.refresh_from_db()
        self.assertEqual(first.status, Document.DONE)
        self.assertEqual(first.body, "<p>We saw it.</p>")
        for _ in range(3):
            item.advance(self.anna)
        self.assertEqual((item.document.phase, item.document.cycle), (OBSERVE, 2))
        self.assertEqual(item.documents.count(), 5)

    def test_going_back_reopens_the_earlier_document(self):
        item = self.item()
        first = item.document
        item.advance(self.anna)
        item.set_phase(OBSERVE, self.anna)
        self.assertEqual(item.document.pk, first.pk)
        self.assertEqual(item.document.status, Document.DRAFT)

    def test_review_asks_everyone_but_the_creator(self):
        item = self.item()
        carl = User.objects.create_user("carl@example.com", "pw-carl-123!", name="Carl Ny")
        Membership.objects.create(workspace=self.ws, user=carl)
        item.assignees.add(self.bo)
        self.client.force_login(self.bo)
        self.client.post(reverse("board:review", args=[item.number]))
        self.assertEqual(item.document.status, Document.REVIEW)
        self.assertEqual([m.to for m in mail.outbox], [["carl@example.com"]])  # not anna (creator), not bo (sender)

    def test_next_phase_warns_but_does_not_stop(self):
        item = self.item(body="<p>Written</p>")
        r = self.client.get(item.get_absolute_url())
        self.assertIn("submitted for review", r.context["next_warning"])
        Comment.objects.create(item=item, document=item.document, kind="suggestion", quote="Written",
                               replacement="Typed", author=self.bo)
        r = self.client.get(item.get_absolute_url())
        self.assertIn("1 suggestion is still open", r.context["next_warning"])
        self.client.post(reverse("board:phase", args=[item.number]), {"phase": "next"})
        item.refresh_from_db()
        self.assertEqual(item.phase, ORIENT)

    def test_earlier_documents_are_read_only(self):
        item = self.item(body="<p>Old observation</p>")
        old = item.document
        item.advance(self.anna)
        r = self.client.get(old.get_absolute_url())
        self.assertContains(r, "Old observation")
        self.assertNotContains(r, 'contenteditable="true"')
        self.assertNotContains(r, 'id="composer"')
        r = self.client.get(item.get_absolute_url())
        self.assertContains(r, "Input: the Observe document")
        self.client.post(reverse("board:comment", args=[item.number]), {"quote": "x", "body": "hi"})
        self.assertEqual(Comment.objects.get().document, item.document)

    def test_search_looks_in_all_documents(self):
        item = self.item(body="<p>needle</p>")
        item.advance(self.anna)
        r = self.client.get(reverse("board:index"), {"q": "needle"})
        self.assertEqual([i.pk for i in r.context["items"]], [item.pk])

    def test_tidy_drops_unanswered_questions(self):
        from .document import template, tidy

        self.assertEqual(tidy("<h3>A?</h3><p>yes</p>" + template("observe")), "<h3>A?</h3><p>yes</p>")


class AgentTests(Base):
    def setUp(self):
        super().setUp()
        from accounts.models import Membership as M

        self.ada = User.objects.create_user("ada@agents.ooda.local", None, name="Ada", is_agent=True)
        self.ada_m = M.objects.create(workspace=self.ws, user=self.ada)
        self.doc_item = self.item("Printer", body=(
            "<h3>What happened?</h3><p>The printer jams often. We should buy a new one.</p>"
            "<h3>How often, how many?</h3><p><br></p>"
        ))
        self.doc_item.assignees.add(self.bo)
        doc = self.doc_item.document
        doc.status = Document.REVIEW
        doc.submitted_at = timezone.now() - timedelta(minutes=1)
        doc.save()

    def agent(self, use_claude=False):
        from .agent import Agent

        return Agent(self.ada, self.ada_m, use_claude, log=lambda m: None)

    def test_rules_review_once_and_anchor_in_the_text(self):
        self.agent().run_once()
        notes = list(Comment.objects.filter(author=self.ada).order_by("pk"))
        self.assertEqual([(c.kind, c.quote) for c in notes], [
            ("comment", "often"), ("suggestion", "We should buy a new one."),
            ("comment", "The printer jams often. We should buy a new one."),
        ])
        self.assertEqual(notes[1].prefix, "The printer jams often. ")
        self.assertEqual([m.to for m in mail.outbox], [["bo@example.com"]])  # the suggestion, to the assignee
        self.agent().run_once()
        self.assertEqual(Comment.objects.filter(author=self.ada).count(), 3)  # not twice

    def test_agent_cannot_edit(self):
        self.assertFalse(self.doc_item.can_edit(self.ada, self.ada_m))

    def test_answers_when_someone_replies_or_mentions_it(self):
        self.agent().run_once()
        thread = Comment.objects.filter(author=self.ada).first()
        Reply.objects.create(comment=thread, body="About twice a week.", author=self.bo)
        other = Comment.objects.create(item=self.doc_item, document=self.doc_item.document, quote="jams",
                                       body="@ada what do you think?", author=self.bo)
        self.agent().run_once()
        self.assertEqual(thread.replies.last().author, self.ada)
        self.assertEqual(other.replies.get().author, self.ada)
        self.agent().run_once()
        self.assertEqual(thread.replies.count(), 2)  # waits for the next human message

    def test_claude_notes_are_checked_against_the_text(self):
        from unittest import mock

        from . import agent

        reply = {"notes": [
            {"kind": "suggestion", "quote": "We should buy a new one.", "replacement": "", "comment": "A proposal."},
            {"kind": "comment", "quote": "not in the document", "replacement": "", "comment": "Dropped."},
        ]}
        fake = mock.MagicMock()
        fake.return_value.beta.messages.create.return_value = mock.Mock(
            stop_reason="end_turn", content=[mock.Mock(type="text", text=json.dumps(reply))])
        with mock.patch("anthropic.Anthropic", fake):
            self.agent(use_claude=True).run_once()
        kwargs = fake.return_value.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], agent.MODEL)
        self.assertIn("Observe phase", kwargs["system"])
        self.assertEqual(list(Comment.objects.filter(author=self.ada).values_list("quote", flat=True)),
                         ["We should buy a new one."])

    def test_command_creates_the_agent(self):
        call_command("agent", "--once", "--rules", "--name", "Bert", stdout=open("/dev/null", "w"))
        bert = User.objects.get(name="Bert")
        self.assertTrue(bert.is_agent)
        self.assertFalse(bert.has_usable_password())
        self.assertTrue(Comment.objects.filter(author=bert).exists())


class OrientReviewTests(Base):
    def orient_doc(self, body):
        from .document import template

        item = self.item("Churn")
        item.advance(self.anna)
        doc = item.document
        doc.body = body + template("orient")
        doc.save()
        return doc

    def quotes(self, doc):
        from .agent import rule_review

        return {n.quote: n.comment for n in rule_review(doc, None)}

    def test_asks_for_data_source_and_hypotheses(self):
        notes = self.quotes(self.orient_doc("<p>Customers seem unhappy with the new flow.</p>"))
        text = notes["Customers seem unhappy with the new flow."]
        self.assertIn("backed up", text)
        self.assertIn("Hypotheses: what could explain it, and how could we test it?", notes)

    def test_asks_for_comparison_when_a_number_stands_alone(self):
        notes = self.quotes(self.orient_doc(
            "<p>Churn is 7% according to the billing report.</p>"))
        self.assertIn("Compared to what?", notes["Churn is 7% according to the billing report."])

    def test_backed_up_and_comparable_passes(self):
        from .document import template

        doc = self.orient_doc("")
        doc.body = template("orient").replace(
            "<h3>Hypotheses: what could explain it, and how could we test it?</h3><p><br></p>",
            "<h3>Hypotheses: what could explain it, and how could we test it?</h3><p>Solo users skip it.</p>",
        ).replace("<p><br></p>", "<p>Churn is 7%, compared to 4% last year, according to the billing report.</p>", 1)
        doc.body = doc.body.replace("<p><br></p>", "<p>Answered.</p>")
        doc.save()
        self.assertEqual(self.quotes(doc), {})

    def test_asks_to_take_the_old_picture_apart(self):
        notes = self.quotes(self.orient_doc("<p>Churn is 7% according to the billing report, compared to 4%.</p>"))
        self.assertIn("What do we assume today, and which parts still hold?", notes)
        self.assertIn("How would someone from a completely different field explain this?", notes)


class DecideTests(Base):
    def decide_doc(self, orient_body):
        item = self.item("Churn")
        item.advance(self.anna)
        Document.objects.filter(pk=item.document.pk).update(body=orient_body)
        item.advance(self.anna)
        return item.document

    def test_decide_starts_from_orients_hypotheses(self):
        doc = self.decide_doc(
            "<h3>Hypotheses: what could explain it, and how could we test it?</h3>"
            "<ul><li>Solo users see no reason to invite.</li></ul><h3>What don't we know yet?</h3><p><br></p>")
        self.assertEqual(doc.phase, "decide")
        self.assertIn("<h3>Assumptions from Orient to validate</h3><ul><li>Solo users see no reason to invite.</li></ul>",
                      doc.body)
        self.assertIn("<h3>Experiment 1</h3>", doc.body)
        self.assertIn("<li><strong>How we measure:</strong> </li>", doc.body)

    def test_empty_experiments_are_hidden_and_not_content(self):
        from .document import decide_template, has_content, tidy

        self.assertEqual(tidy(decide_template()), "")
        self.assertFalse(has_content(decide_template()))

    def test_agent_asks_for_measure_and_thresholds(self):
        from .agent import rule_review

        doc = self.decide_doc("")
        doc.body = doc.body.replace(
            "<li><strong>Hypothesis:</strong> </li>", "<li><strong>Hypothesis:</strong> Solo users skip invites.</li>"
        ).replace("<li><strong>It holds if:</strong> </li>", "<li><strong>It holds if:</strong> more people finish</li>", 1)
        doc.save()
        notes = {n.quote: n.comment for n in rule_review(doc, None)}
        self.assertIn("How will we know?", notes["How we measure:"])
        self.assertIn("be a number", notes["It holds if: more people finish"])
        self.assertIn("wrong", notes["It fails if:"])

    def test_agent_nudges_an_untouched_decide_document(self):
        from .agent import rule_review

        notes = rule_review(self.decide_doc(""), None)
        self.assertEqual([n.quote for n in notes], ["Experiment 1"])


class ActTests(Base):
    def run_to_act(self, decide_fill=True):
        item = self.item("Printer")
        item.advance(self.anna)
        item.advance(self.anna)
        decide = item.document
        if decide_fill:
            decide.body = decide.body.replace(
                "<li><strong>Hypothesis:</strong> </li>",
                "<li><strong>Hypothesis:</strong> The Monday batch overloads the tray.</li>", 1)
            decide.save()
        item.advance(self.anna)
        return item

    def test_act_is_a_journal_with_one_result_line_per_experiment(self):
        doc = self.run_to_act().document
        self.assertEqual(doc.phase, "act")
        self.assertIn("<h3>Journal</h3>", doc.body)
        self.assertIn("<h3>Results as observations</h3><ul>"
                      "<li><strong>Experiment 1 — The Monday batch overloads the tray:</strong> </li></ul>", doc.body)

    def test_next_observe_starts_from_the_results(self):
        item = self.run_to_act()
        act = item.document
        act.body = act.body.replace(
            ":</strong> </li>", ":</strong> 1 jam on Monday 3 Nov, compared with 4 the week before.</li>")
        act.save()
        item.advance(self.anna)
        observe = item.document
        self.assertEqual((observe.phase, observe.cycle), ("observe", 2))
        self.assertTrue(observe.body.startswith(
            "<h3>What happened?</h3><ul><li><strong>Experiment 1 — The Monday batch overloads the tray:</strong> "
            "1 jam on Monday 3 Nov, compared with 4 the week before.</li></ul><h3>Where and when?</h3>"))

    def test_agent_keeps_results_observational(self):
        from .agent import rule_review

        item = self.run_to_act()
        act = item.document
        act.body = act.body.replace(":</strong> </li>", ":</strong> It worked, so we should keep it.</li>")
        act.save()
        notes = {n.quote: n.comment for n in rule_review(act, None)}
        self.assertIn("reads as a conclusion", notes["It worked, so we should keep it."])
        self.assertIn("dated lines", notes["Journal"])
        act.body = act.body.replace("It worked, so we should keep it.", "").replace(
            "<h3>Journal</h3><p><br></p>", "<h3>Journal</h3><p>3 Nov: split the batch.</p>")
        act.save()
        notes = {n.quote: n.comment for n in rule_review(act, None)}
        self.assertEqual(list(notes), ["Experiment 1 — The Monday batch overloads the tray:"])


class TidyLabelTests(TestCase):
    def test_answers_written_inside_a_label_are_kept(self):
        from .document import tidy

        self.assertEqual(tidy("<ul><li><strong>Result:</strong> </li></ul>"), "")
        self.assertEqual(tidy("<ul><li><strong>Result:1 jam</strong></li></ul>"), "<ul><li><strong>Result:1 jam</strong></li></ul>")


class LoopFeedbackTests(Base):
    """Observations at any time, re-orienting, outcomes per experiment and the hypotheses portfolio."""

    def setUp(self):
        super().setUp()
        self.it = self.item("Printer")
        self.it.assignees.add(self.anna)

    def to_act(self, then="Buy a new tray"):
        it = self.it
        it.advance(self.anna)
        it.advance(self.anna)
        decide = it.document
        decide.body = decide.body.replace(
            "<li><strong>Hypothesis:</strong> </li>", "<li><strong>Hypothesis:</strong> The batch overloads the tray.</li>", 1
        ).replace("<li><strong>If it fails, we will:</strong> </li>", f"<li><strong>If it fails, we will:</strong> {then}</li>", 1)
        decide.save()
        it.advance(self.anna)
        return it

    def test_anyone_notes_an_observation_and_the_editors_hear(self):
        self.client.force_login(self.bo)
        self.client.post(reverse("board:observe_now", args=[self.it.number]), {"body": "Jammed again at 9:12"})
        self.assertEqual(self.it.pending_observations().get().body, "Jammed again at 9:12")
        self.assertEqual([m.to for m in mail.outbox], [["anna@example.com"]])

    def test_only_editors_take_observations_in(self):
        e = Entry.objects.create(workspace=self.ws, item=self.it, body="Seen", author=self.bo)
        self.client.force_login(self.bo)
        self.client.post(reverse("board:observation_noted", args=[e.pk]), {"action": "noted"})
        e.refresh_from_db()
        self.assertIsNone(e.handled_at)
        self.client.force_login(self.anna)
        self.client.post(reverse("board:observation_noted", args=[e.pk]), {"action": "add"})
        e.refresh_from_db()
        self.assertEqual(e.handled_how, "added")
        self.assertIn("Seen", self.it.document.body)

    def test_reorient_from_act_goes_back_with_a_reason(self):
        it = self.to_act()
        e = Entry.objects.create(workspace=self.ws, item=it, body="The paper was damp all week", author=self.bo)
        self.client.post(reverse("board:reorient", args=[it.number]), {"observation": e.pk})
        it.refresh_from_db()
        self.assertEqual(it.phase, ORIENT)
        self.assertEqual(it.cycle, 1)
        self.assertEqual(it.spans.last().reason, "The paper was damp all week")
        self.assertIn("<h3>Re-oriented: what no longer fits</h3><p>The paper was damp all week</p>", it.document.body)
        e.refresh_from_db()
        self.assertEqual(e.handled_how, "reoriented")
        self.assertContains(self.client.get(it.get_absolute_url()), "Re-oriented by")

    def test_reorient_needs_decide_or_act_and_an_editor(self):
        self.client.post(reverse("board:reorient", args=[self.it.number]), {"reason": "x"})
        self.it.refresh_from_db()
        self.assertEqual(self.it.phase, OBSERVE)
        it = self.to_act()
        self.client.force_login(self.bo)
        self.assertEqual(self.client.post(reverse("board:reorient", args=[it.number]), {"reason": "x"}).status_code, 403)

    def test_next_loop_starts_with_waiting_observations(self):
        it = self.to_act()
        e = Entry.objects.create(workspace=self.ws, item=it, body="Two jams on Tuesday", author=self.bo)
        it.advance(self.anna)
        self.assertIn("<li>Two jams on Tuesday <em>— Bo Ek</em></li>", it.document.body.split("<h3>Where and when?")[0])
        e.refresh_from_db()
        self.assertEqual(e.handled_how, "carried")

    def test_verdicts_and_the_pre_decided_response(self):
        it = self.to_act()
        url = reverse("board:verdict", args=[it.number])
        self.client.post(url, {"experiment": "Experiment 1", "outcome": "failed"})
        self.client.post(url, {"experiment": "Not an experiment", "outcome": "held"})
        self.assertEqual(list(Verdict.objects.values_list("experiment", "outcome")), [("Experiment 1", "failed")])
        r = self.client.get(it.get_absolute_url())
        self.assertContains(r, "Buy a new tray")
        self.client.post(url, {"experiment": "Experiment 1", "outcome": ""})
        self.assertFalse(Verdict.objects.exists())

    def test_hypotheses_page_shows_the_portfolio(self):
        it = self.to_act()
        r = self.client.get(reverse("board:hypotheses"))
        self.assertEqual([(h.text, h.status) for h in r.context["hypotheses"]], [("The batch overloads the tray.", "testing")])
        Verdict.objects.create(item=it, cycle=1, experiment="Experiment 1", outcome="held")
        r = self.client.get(reverse("board:hypotheses") + "?status=held")
        self.assertEqual(len(r.context["hypotheses"]), 1)

    def test_tempo_measures_noticing_to_acting(self):
        Entry.objects.create(workspace=self.ws, item=self.it, body="Seen", author=self.bo)
        PhaseSpan.objects.filter(item=self.it).update(started_at=timezone.now() - timedelta(hours=5))
        Entry.objects.filter(item=self.it).update(created_at=timezone.now() - timedelta(hours=4))
        self.to_act()
        Verdict.objects.create(item=self.it, cycle=1, experiment="Experiment 1", outcome="failed")
        r = self.client.get(reverse("board:tempo"))
        self.assertGreater(r.context["notice_to_act"], timedelta(hours=3))
        self.assertEqual(r.context["tested"]["failed"], 1)
        self.assertEqual(r.context["stale"][0]["item"], self.it)


class FeltSignalTests(Base):
    def test_feelings_are_not_asked_for_numbers(self):
        from .agent import rule_review

        item = self.item("Mood", body="<h3>What happened?</h3><p>The team feels uneasy about the new rota.</p>")
        quotes = [n.quote for n in rule_review(item.document, None)]
        self.assertNotIn("The team feels uneasy about the new rota.", quotes)

    def test_orient_is_asked_to_articulate_a_feeling_from_observe(self):
        from .agent import rule_review
        from .document import felt_heading

        item = self.item("Mood", body=f"<h3>{felt_heading()}</h3><p>Something is off on Mondays.</p>")
        item.advance(self.anna)
        doc = item.document
        doc.body = "<p>Absence was 4% compared to 3% last year according to HR.</p>" + doc.body
        doc.save()
        notes = rule_review(doc, doc.previous())
        self.assertIn("Something is off on Mondays.", notes[0].comment)

    def test_a_felt_observation_is_labelled_when_carried_into_the_next_loop(self):
        item = self.item("Mood")
        self.client.post(reverse("board:observe_now", args=[item.number]), {"body": "Heavy mood", "signal": "felt"})
        self.assertEqual(item.pending_observations().get().signal, "felt")
        for _ in range(4):
            item.advance(self.anna)
        self.assertIn("<li><strong>Felt:</strong> Heavy mood", item.document.body)
        self.assertIn("<h3>What if it is the other way around?</h3>", item.documents.get(phase="orient", cycle=1).body)


class PerspectiveTests(Base):
    def setUp(self):
        super().setUp()
        self.it = self.item("Churn", body="<p>Solo users leave in week one.</p>")

    def test_a_reading_stays_until_its_author_withdraws_it(self):
        self.client.force_login(self.bo)
        self.client.post(reverse("board:comment", args=[self.it.number]), {
            "kind": "reading", "quote": "Solo users leave", "body": "Maybe they leave because they succeed fast."})
        c = Comment.objects.get()
        self.assertEqual(c.kind, "reading")
        status = reverse("board:comment_status", args=[c.pk])
        self.client.force_login(self.anna)
        self.assertEqual(self.client.post(status, {"action": "resolve"}).status_code, 403)
        self.client.force_login(self.bo)
        self.client.post(status, {"action": "resolve"})
        c.refresh_from_db()
        self.assertEqual(c.status, Comment.RESOLVED)

    def test_open_readings_go_along_to_the_next_phase(self):
        Comment.objects.create(item=self.it, document=self.it.document, kind="reading", quote="Solo users leave",
                               body="They succeed fast and don't need us.", author=self.bo)
        self.it.advance(self.anna)
        r = self.client.get(self.it.get_absolute_url())
        self.assertContains(r, "Other readings kept from Observe")
        self.assertContains(r, "They succeed fast and don")

    def lens_agent(self, lens):
        from accounts.models import Membership as M

        from .agent import Agent

        agent = User.objects.create_user(f"{lens}@agents.ooda.local", None, name=lens.title(), is_agent=True,
                                         agent_lens=lens)
        return Agent(agent, M.objects.create(workspace=self.ws, user=agent), False, log=lambda m: None)

    def to_decide_in_review(self):
        self.it.advance(self.anna)
        self.it.advance(self.anna)
        doc = self.it.document
        doc.body = doc.body.replace("<li><strong>Hypothesis:</strong> </li>",
                                    "<li><strong>Hypothesis:</strong> Solo users don't need a team.</li>", 1).replace(
            "<li><strong>What we do:</strong> </li>", "<li><strong>What we do:</strong> Hide the invite step.</li>", 1)
        doc.status = Document.REVIEW
        doc.save()
        return doc

    def test_inversion_agent_offers_readings_of_hypotheses(self):
        doc = self.to_decide_in_review()
        self.lens_agent("inversion").run_once()
        c = Comment.objects.get(document=doc)
        self.assertEqual((c.kind, c.quote), ("reading", "Hypothesis: Solo users don't need a team."))

    def test_human_agent_asks_rather_than_tells(self):
        doc = self.to_decide_in_review()
        self.lens_agent("human").run_once()
        bodies = list(Comment.objects.filter(document=doc).values_list("body", flat=True))
        self.assertTrue(any("Have they been asked?" in b for b in bodies))
        self.assertTrue(all(c.kind == "comment" for c in Comment.objects.filter(document=doc)))


class NestedLoopTests(Base):
    def setUp(self):
        super().setUp()
        self.parent = self.item("Move hosting")
        self.parent.assignees.add(self.bo)
        self.child = self.item("Nightly backups", parent=self.parent)

    def child_to_act(self):
        c = self.child
        c.advance(self.anna)
        c.advance(self.anna)
        d = c.document
        d.body = d.body.replace("<li><strong>Hypothesis:</strong> </li>",
                                "<li><strong>Hypothesis:</strong> Restores take under an hour.</li>", 1)
        d.save()
        c.advance(self.anna)
        act = c.document
        act.body = act.body.replace(":</strong> </li>", ":</strong> A full restore took 41 minutes.</li>")
        act.save()
        Verdict.objects.create(item=c, cycle=1, experiment="Experiment 1", outcome="held")
        return c

    def test_finished_loop_reports_up_as_an_observation(self):
        c = self.child_to_act()
        c.advance(self.anna)
        obs = self.parent.pending_observations().get()
        self.assertTrue(obs.body.startswith("From #2 Nightly backups, loop 1:"))
        self.assertIn("A full restore took 41 minutes.", obs.body)
        self.assertIn("Experiment 1: held", obs.body)
        self.assertEqual(obs.signal, "measured")
        self.assertEqual([m.to for m in mail.outbox], [["bo@example.com"]])

    def test_marking_done_in_act_also_reports_up(self):
        c = self.child_to_act()
        c.set_done(True, self.anna)
        self.assertEqual(self.parent.pending_observations().count(), 1)

    def test_child_shows_the_intent_from_above(self):
        self.parent.advance(self.anna)
        self.parent.advance(self.anna)
        d = self.parent.document
        d.body = d.body.replace("<li><strong>Hypothesis:</strong> </li>",
                                "<li><strong>Hypothesis:</strong> An EU host halves latency.</li>", 1)
        d.save()
        r = self.client.get(self.child.get_absolute_url())
        self.assertContains(r, "Intent from the loop above: #1 Move hosting")
        self.assertContains(r, "An EU host halves latency.")

    def test_board_filters_by_level(self):
        r = self.client.get(reverse("board:index"), {"level": "sub"})
        self.assertEqual([i.pk for i in r.context["items"]], [self.child.pk])
        r = self.client.get(reverse("board:index"), {"level": "top"})
        self.assertEqual([i.pk for i in r.context["items"]], [self.parent.pk])


class RiskTests(Base):
    def decide_with_risk(self, signal=""):
        it = self.item("Pricing")
        it.advance(self.anna)
        it.advance(self.anna)
        d = it.document
        d.body = d.body.replace("<li><strong>Hypothesis:</strong> </li>",
                                "<li><strong>Hypothesis:</strong> Annual plans sell.</li>", 1).replace(
            "<li><strong>What could go wrong:</strong> </li>",
            "<li><strong>What could go wrong:</strong> Monthly customers churn.</li>").replace(
            "<li><strong>Early signal:</strong> </li>", f"<li><strong>Early signal:</strong> {signal}</li>")
        d.save()
        return it, d

    def test_decide_has_a_risk_block_and_act_a_line_for_it(self):
        it, d = self.decide_with_risk("Cancellations above 5 a week")
        self.assertIn("<h3>Risk 1</h3>", d.body)
        it.advance(self.anna)
        self.assertIn("<li><strong>Risk 1 — Monthly customers churn:</strong> </li>", it.document.body)

    def test_risks_are_watched_and_judged_in_their_own_words(self):
        it, d = self.decide_with_risk("Cancellations above 5 a week")
        r = self.client.get(reverse("board:hypotheses"))
        self.assertEqual(sorted((h.kind, h.status) for h in r.context["hypotheses"]),
                         [("experiment", "planned"), ("risk", "watching")])
        it.advance(self.anna)
        r = self.client.get(it.get_absolute_url())
        self.assertContains(r, "Didn&#x27;t happen")
        self.client.post(reverse("board:verdict", args=[it.number]), {"experiment": "Risk 1", "outcome": "failed"})
        r = self.client.get(reverse("board:hypotheses"))
        risk = next(h for h in r.context["hypotheses"] if h.kind == "risk")
        self.assertEqual(str(risk.status_label), "Happened")

    def test_agent_asks_for_the_early_signal(self):
        from .agent import rule_review

        it, d = self.decide_with_risk("")
        for label, value in (("What we do", "Offer annual"), ("How we measure", "Share of annual"),
                             ("It holds if", "Over 20%"), ("It fails if", "Under 5%"),
                             ("If it fails, we will", "Drop it")):
            d.body = d.body.replace(f"<li><strong>{label}:</strong> </li>", f"<li><strong>{label}:</strong> {value}</li>", 1)
        notes = {n.quote: n.comment for n in rule_review(d, None)}
        self.assertIn("What would we see first", notes["Early signal:"])
