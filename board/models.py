from django.conf import settings
from django.db import models, transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Workspace

from .document import has_content, heading, plain_text, template as document_template

User = settings.AUTH_USER_MODEL

OBSERVE, ORIENT, DECIDE, ACT = "observe", "orient", "decide", "act"
PHASES = [
    (OBSERVE, _("Observe")),
    (ORIENT, _("Orient")),
    (DECIDE, _("Decide")),
    (ACT, _("Act")),
]
PHASE_ORDER = [p for p, _label in PHASES]


def next_phase(phase):
    return PHASE_ORDER[(PHASE_ORDER.index(phase) + 1) % len(PHASE_ORDER)]


class Item(models.Model):
    """Something that needs doing, moved around the OODA loop."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="items")
    number = models.PositiveIntegerField(editable=False)
    title = models.CharField(_("title"), max_length=200)

    phase = models.CharField(_("phase"), max_length=10, choices=PHASES, default=OBSERVE)
    cycle = models.PositiveIntegerField(_("loop"), default=1)

    is_priority = models.BooleanField(_("prioritised"), default=False)
    is_done = models.BooleanField(_("done"), default=False)
    done_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateField(_("deadline"), null=True, blank=True)
    deadline_reminded_on = models.DateField(null=True, blank=True, editable=False)

    assignees = models.ManyToManyField(User, blank=True, related_name="assigned_items", verbose_name=_("assigned"))
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children", verbose_name=_("part of")
    )
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged")
    depends_on = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="blocks", verbose_name=_("waits for")
    )
    options = models.ManyToManyField("FieldOption", blank=True, related_name="items")

    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_priority", "deadline", "-updated_at"]
        constraints = [models.UniqueConstraint(fields=["workspace", "number"], name="item_number")]

    def __str__(self):
        return f"#{self.number} {self.title}"

    def get_absolute_url(self):
        return reverse("board:item", args=[self.number])

    def save(self, *args, **kwargs):
        if self.number is None:
            with transaction.atomic():
                last = Item.objects.filter(workspace=self.workspace).aggregate(n=Max("number"))["n"] or 0
                self.number = last + 1
                super().save(*args, **kwargs)
                PhaseSpan.objects.create(item=self, phase=self.phase, cycle=self.cycle, started_at=self.created_at)
                self.open_document()
            return
        super().save(*args, **kwargs)

    @property
    def document(self):
        """The document for the phase and loop the item is in now."""
        return self.open_document()

    def open_document(self):
        doc = Document.objects.filter(item=self, phase=self.phase, cycle=self.cycle).first()
        if doc is None:
            # The phase before, in this loop; for Observe, the Act that ended the loop before.
            i = PHASE_ORDER.index(self.phase)
            cycle = self.cycle - 1 if i == 0 else self.cycle
            before = Document.objects.filter(item=self, phase=PHASE_ORDER[i - 1], cycle=cycle).first()
            doc = Document.objects.create(item=self, phase=self.phase, cycle=self.cycle,
                                          body=document_template(self.phase, before))
        return doc

    def documents_in_order(self):
        return sorted(self.documents.all(), key=lambda d: (d.cycle, PHASE_ORDER.index(d.phase)))

    def can_edit(self, user, membership):
        """Owners, assignees and whoever created the item edit the document directly.

        Everyone else in the workspace comments and suggests changes.
        """
        if membership is None or membership.workspace_id != self.workspace_id:
            return False
        if membership.is_owner or self.created_by_id == user.pk:
            return True
        return self.assignees.filter(pk=user.pk).exists()

    @property
    def open_dependencies(self):
        return [d for d in self.depends_on.all() if not d.is_done]

    @property
    def is_blocked(self):
        return bool(self.open_dependencies)

    @property
    def is_overdue(self):
        return bool(self.deadline and not self.is_done and self.deadline < timezone.localdate())

    def options_for(self, field):
        return [o for o in self.options.all() if o.field_id == field.pk]

    def phase_index(self):
        return PHASE_ORDER.index(self.phase)

    @transaction.atomic
    def set_phase(self, phase, by=None):
        """Move to a phase. Going from Act back to Observe starts a new loop."""
        if phase == self.phase:
            return
        now = timezone.now()
        if self.phase == ACT and phase == OBSERVE:
            self.cycle += 1
        PhaseSpan.objects.filter(item=self, ended_at__isnull=True).update(ended_at=now)
        Document.objects.filter(item=self, status__in=[Document.DRAFT, Document.REVIEW]).update(status=Document.DONE)
        self.phase = phase
        self.save(update_fields=["phase", "cycle", "updated_at"])
        PhaseSpan.objects.create(item=self, phase=phase, cycle=self.cycle, started_at=now, moved_by=by)
        doc = self.open_document()
        if doc.status == Document.DONE:  # back to a phase worked on before in this loop
            doc.status = Document.DRAFT
            doc.save(update_fields=["status"])

    def advance(self, by=None):
        self.set_phase(next_phase(self.phase), by)

    @transaction.atomic
    def set_done(self, done, by=None):
        now = timezone.now()
        self.is_done = done
        self.done_at = now if done else None
        self.save(update_fields=["is_done", "done_at", "updated_at"])
        if done:
            PhaseSpan.objects.filter(item=self, ended_at__isnull=True).update(ended_at=now)
        else:
            PhaseSpan.objects.create(item=self, phase=self.phase, cycle=self.cycle, started_at=now, moved_by=by)

    @transaction.atomic
    def merge_into(self, target, by=None):
        """Fold this item into `target`. Everything said about it moves along."""
        assert target.pk != self.pk and target.workspace_id == self.workspace_id
        mine, theirs = self.document, target.document
        self.entries.update(item=target)
        self.links.update(item=target)
        self.comments.update(item=target)
        Comment.objects.filter(document=mine).update(document=theirs)
        self.children.exclude(pk=target.pk).update(parent=target)
        target.assignees.add(*self.assignees.all())
        target.options.add(*self.options.all())
        target.depends_on.add(*self.depends_on.exclude(pk=target.pk))
        for blocked in self.blocks.exclude(pk=target.pk):
            blocked.depends_on.add(target)
        self.blocks.clear()
        self.depends_on.clear()
        if has_content(mine.body):
            theirs.append(heading(f"#{self.number} {self.title}") + mine.body)
        target.is_priority = target.is_priority or self.is_priority
        if self.deadline and (not target.deadline or self.deadline < target.deadline):
            target.deadline = self.deadline
        target.save()
        self.merged_into = target
        self.parent = None
        self.save(update_fields=["merged_into", "parent", "updated_at"])
        self.set_done(True, by)


class PhaseSpan(models.Model):
    """How long an item spent in one phase of one loop. The basis for tempo."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="spans")
    phase = models.CharField(max_length=10, choices=PHASES)
    cycle = models.PositiveIntegerField()
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    moved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["started_at"]

    @property
    def duration(self):
        return (self.ended_at or timezone.now()) - self.started_at


class Entry(models.Model):
    """An observation, orientation, decision or action someone writes down.

    Entries without an item sit in the inbox until someone files them.
    """

    OBSERVATION, ORIENTATION, DECISION, ACTION, NOTE = "observation", "orientation", "decision", "action", "note"
    KINDS = [
        (OBSERVATION, _("Observation")),
        (ORIENTATION, _("Orientation")),
        (DECISION, _("Decision")),
        (ACTION, _("Action")),
        (NOTE, _("Note")),
    ]
    WRITABLE_KINDS = KINDS[:4]
    KIND_FOR_PHASE = {OBSERVE: OBSERVATION, ORIENT: ORIENTATION, DECIDE: DECISION, ACT: ACTION}

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="entries")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(_("kind"), max_length=12, choices=KINDS, default=OBSERVATION)
    body = models.TextField(_("text"))
    cycle = models.PositiveIntegerField(null=True, blank=True)
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="entries")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "entries"

    def save(self, *args, **kwargs):
        if self.item_id and self.cycle is None:
            self.cycle = self.item.cycle
        super().save(*args, **kwargs)


class Link(models.Model):
    """An attachment, kept wherever it lives and linked to here."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="links")
    url = models.URLField(_("address"), max_length=1000)
    label = models.CharField(_("label"), max_length=120, blank=True)
    added_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.label or self.url


class Field(models.Model):
    """A taxonomy dimension, e.g. "Area" or "Customer", with a fixed set of options."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="fields")
    name = models.CharField(_("name"), max_length=60)
    allow_many = models.BooleanField(_("allow several"), default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return self.name


class FieldOption(models.Model):
    COLORS = ["grey", "red", "orange", "yellow", "green", "teal", "blue", "purple"]

    field = models.ForeignKey(Field, on_delete=models.CASCADE, related_name="choices")
    label = models.CharField(_("label"), max_length=60)
    color = models.CharField(max_length=10, choices=[(c, c) for c in COLORS], default="grey")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return self.label


class Document(models.Model):
    """What was written in one phase of one loop. Each phase starts a new one;
    the one before is its input, and all of them are kept.
    """

    DRAFT, REVIEW, DONE = "draft", "review", "done"
    STATUSES = [(DRAFT, _("Draft")), (REVIEW, _("In review")), (DONE, _("Done"))]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="documents")
    phase = models.CharField(max_length=10, choices=PHASES)
    cycle = models.PositiveIntegerField()
    body = models.TextField(blank=True)  # sanitised HTML, see board/document.py
    version = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    submitted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "phase", "cycle"], name="one_document_per_phase")]

    def __str__(self):
        return f"#{self.item.number} {self.get_phase_display()} {self.cycle}"

    def get_absolute_url(self):
        return reverse("board:document", args=[self.item.number, self.pk])

    @property
    def is_current(self):
        return self.phase == self.item.phase and self.cycle == self.item.cycle

    @property
    def text(self):
        return plain_text(self.body)

    def append(self, html):
        self.body = (self.body or "") + html
        self.version += 1
        self.save(update_fields=["body", "version", "updated_at"])

    def previous(self):
        """The document this one builds on: the phase before, possibly in the loop before."""
        docs = self.item.documents_in_order()
        i = docs.index(self)
        return docs[i - 1] if i > 0 else None


class Comment(models.Model):
    """A margin comment or a suggested change, anchored to a passage of the document.

    `quote` is the passage and `prefix` the text just before it in the same
    paragraph, so the passage can be found again even if it occurs twice.
    A suggestion proposes `replacement` in place of `quote`.
    """

    COMMENT, SUGGESTION = "comment", "suggestion"
    KINDS = [(COMMENT, _("Comment")), (SUGGESTION, _("Suggestion"))]
    OPEN, RESOLVED, ACCEPTED, REJECTED = "open", "resolved", "accepted", "rejected"
    STATUSES = [(OPEN, _("Open")), (RESOLVED, _("Resolved")), (ACCEPTED, _("Accepted")), (REJECTED, _("Rejected"))]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="comments")
    document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.CASCADE, related_name="comments")
    kind = models.CharField(max_length=12, choices=KINDS, default=COMMENT)
    quote = models.TextField(blank=True)
    prefix = models.CharField(max_length=60, blank=True)
    replacement = models.TextField(blank=True)
    body = models.TextField(_("comment"), blank=True)
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="comments")
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=OPEN)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    @property
    def is_open(self):
        return self.status == self.OPEN

    @property
    def is_suggestion(self):
        return self.kind == self.SUGGESTION

    def close(self, status, by):
        self.status = status
        self.closed_by = by if status != self.OPEN else None
        self.closed_at = timezone.now() if status != self.OPEN else None
        self.save(update_fields=["status", "closed_by", "closed_at"])


class Reply(models.Model):
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="replies")
    body = models.TextField(_("reply"))
    author = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "replies"
