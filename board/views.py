from datetime import timedelta
from statistics import median

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_POST

from accounts.decorators import member_required
from accounts.models import Workspace

from . import document, library, notify
from .document import experiment_labels, experiments
from .forms import EntryForm, ItemForm, LinkForm, workspace_users
from .models import (
    ACT, DECIDE, OBSERVE, ORIENT, PHASE_ORDER, PHASES, Comment, Document, Entry, Field, FieldOption, Item, Lens,
    Link, PhaseSpan, Reply, Verdict,
)

# --- Board -----------------------------------------------------------------


def item_queryset(workspace):
    return (
        Item.objects.filter(workspace=workspace, merged_into=None)
        .select_related("parent")
        .prefetch_related(
            "assignees",
            Prefetch("options", queryset=FieldOption.objects.select_related("field")),
            Prefetch("depends_on", queryset=Item.objects.only("pk", "number", "title", "is_done")),
        )
        .annotate(
            open_comments=Count("comments", filter=Q(comments__status="open"), distinct=True),
        )
    )


def filter_items(request, workspace):
    """Apply the filters in the query string. Returns (items, filters)."""
    g = request.GET
    f = {
        "q": g.get("q", "").strip(),
        "phase": g.get("phase", ""),
        "who": g.get("who", ""),
        "prio": g.get("prio", ""),
        "due": g.get("due", ""),
        "state": g.get("state", "open"),
        "opt": [int(o) for o in g.getlist("opt") if o.isdigit()],
        "level": g.get("level", ""),
    }
    items = item_queryset(workspace)
    if f["q"]:
        q = f["q"]
        cond = Q(title__icontains=q) | Q(documents__body__icontains=q)
        if q.lstrip("#").isdigit():
            cond |= Q(number=int(q.lstrip("#")))
        items = items.filter(cond)
    if f["phase"] in PHASE_ORDER:
        items = items.filter(phase=f["phase"])
    if f["who"] == "me":
        items = items.filter(assignees=request.user)
    elif f["who"] == "none":
        items = items.filter(assignees=None)
    elif f["who"].isdigit():
        items = items.filter(assignees__pk=int(f["who"]))
    if f["prio"] == "1":
        items = items.filter(is_priority=True)
    today = timezone.localdate()
    if f["due"] == "overdue":
        items = items.filter(deadline__lt=today)
    elif f["due"] == "week":
        items = items.filter(deadline__lte=today + timedelta(days=7))
    elif f["due"] == "none":
        items = items.filter(deadline=None)
    if f["level"] == "top":
        items = items.filter(parent__isnull=True)
    elif f["level"] == "sub":
        items = items.filter(parent__isnull=False)
    if f["state"] == "open":
        items = items.filter(is_done=False)
    elif f["state"] == "done":
        items = items.filter(is_done=True)
    if f["opt"]:
        # Options within a field widen the match, different fields narrow it.
        by_field = {}
        for opt in FieldOption.objects.filter(pk__in=f["opt"], field__workspace=workspace):
            by_field.setdefault(opt.field_id, []).append(opt.pk)
        for pks in by_field.values():
            items = items.filter(options__pk__in=pks)
    f["active"] = any([f["q"], f["phase"], f["who"], f["prio"], f["due"], f["opt"], f["level"], f["state"] != "open"])
    return items.distinct(), f


@member_required
def index(request):
    workspace = request.workspace
    items, filters = filter_items(request, workspace)
    return render(request, "board/index.html", board_context(request, workspace, items, filters))


def nest(items):
    """Order items so sub-items follow their parent, and note how deep each is."""
    present = {i.pk for i in items}
    by_parent = {}
    for i in items:
        by_parent.setdefault(i.parent_id if i.parent_id in present else None, []).append(i)
    ordered = []

    def walk(parent_id, depth):
        for i in by_parent.get(parent_id, []):
            i.depth = depth
            ordered.append(i)
            walk(i.pk, depth + 1)

    walk(None, 0)
    seen = {i.pk for i in ordered}
    for i in items:  # anything caught in a parent loop still gets shown
        if i.pk not in seen:
            i.depth = 0
            ordered.append(i)
    return ordered


def board_context(request, workspace, items, filters, public=False):
    view = request.GET.get("view", "table")
    group = request.GET.get("group", "phase")
    fields = list(Field.objects.filter(workspace=workspace).prefetch_related("choices"))
    items = nest(list(items))
    columns = []
    if view == "board":
        group_field = next((fl for fl in fields if str(fl.pk) == group), None)
        if group_field:
            for opt in group_field.choices.all():
                columns.append({"key": opt.pk, "label": opt.label, "color": opt.color,
                                "items": [i for i in items if opt in i.options.all()]})
            columns.append({"key": "", "label": _("No %(field)s") % {"field": group_field.name.lower()},
                            "items": [i for i in items if not i.options_for(group_field)]})
        else:
            group = "phase"
            for key, label in PHASES:
                columns.append({"key": key, "label": label, "items": [i for i in items if i.phase == key]})
    return {
        "items": items,
        "filters": filters,
        "fields": fields,
        "people": workspace_users(workspace),
        "phases": PHASES,
        "view": view,
        "group": group,
        "columns": columns,
        "public": public,
    }


@member_required
@require_POST
def create(request):
    """Quick add from the board: just a title. Everything else comes later."""
    title = request.POST.get("title", "").strip()
    if not title:
        return redirect("board:index")
    item = Item.objects.create(
        workspace=request.workspace, title=title[:200], created_by=request.user,
        phase=request.POST.get("phase") if request.POST.get("phase") in PHASE_ORDER else OBSERVE,
    )
    if request.POST.get("open"):
        return redirect(item)
    messages.success(request, _("Added #%(n)s.") % {"n": item.number})
    return redirect(request.POST.get("next") or "board:index")


@member_required
def new(request):
    form = ItemForm(request.POST or None, workspace=request.workspace)
    if request.method == "POST" and form.is_valid():
        form.instance.workspace = request.workspace
        form.instance.created_by = request.user
        item = form.save()
        notify.assigned(item, item.assignees.all(), request.user)
        return redirect(item)
    return render(request, "board/new.html", {"form": form})


# --- One item --------------------------------------------------------------


def get_item(request, number, edit=False):
    """The item, or a redirect to where it was merged. With `edit`, only for its editors."""
    item = get_object_or_404(Item, workspace=request.workspace, number=number)
    if item.merged_into_id:
        return None, redirect(item.merged_into)
    if edit and not item.can_edit(request.user, request.membership):
        raise PermissionDenied
    return item, None


def item_page_context(item, doc=None, public=False):
    current = item.document
    doc = doc or current
    spans = list(item.spans.all())
    loop_times = loop_durations(spans)
    span = next((s for s in reversed(spans) if s.ended_at is None), None)
    threads = list(doc.comments.select_related("author", "closed_by").prefetch_related("replies__author"))
    ancestors, node = [], item.parent
    while node is not None and node not in ancestors and node != item:
        ancestors.insert(0, node)
        node = node.parent
    loops = {}
    for d in item.documents_in_order():
        loops.setdefault(d.cycle, []).append(d)
    open_suggestions = current.comments.filter(kind=Comment.SUGGESTION, status=Comment.OPEN).count()
    # Said quietly, never in the way: moving on is always allowed.
    if open_suggestions:
        warning = ngettext(
            "%(n)s suggestion is still open. Move on anyway?",
            "%(n)s suggestions are still open. Move on anyway?", open_suggestions) % {"n": open_suggestions}
        note = ngettext("%(n)s open suggestion", "%(n)s open suggestions", open_suggestions) % {"n": open_suggestions}
    elif current.status == Document.DRAFT and document.has_content(current.body):
        warning = _("This document hasn't been submitted for review. Move on anyway?")
        note = _("not reviewed")
    else:
        warning = note = ""
    ai_accepted = doc.comments.filter(kind=Comment.SUGGESTION, status=Comment.ACCEPTED, author__is_agent=True).count()
    own_accepted = doc.comments.filter(kind=Comment.SUGGESTION, status=Comment.ACCEPTED, author__is_agent=False).count()
    return {
        "item": item,
        "doc": doc,
        "is_current": doc.pk == current.pk,
        "input_doc": doc.previous(),
        "loops": sorted(loops.items()),
        "guide": document.GUIDES.get(doc.phase),
        "ancestors": ancestors,
        "threads": [c for c in threads if c.is_open],
        "closed_threads": [c for c in threads if not c.is_open],
        "thread_total": len(threads),
        "outcomes": outcomes_for(doc),
        "voices": voices(item),
        "kept_readings": readings_before(doc),
        "intent": intent_from(item.parent),
        "lenses": library.suggestions(doc) if doc.phase == ORIENT else [],
        "verdict_choices": Verdict.OUTCOMES,
        "pending": list(item.pending_observations().select_related("author")),
        "reoriented": next((s for s in reversed(spans) if s.reason and s.phase == doc.phase
                            and s.cycle == doc.cycle), None),
        "next_warning": warning,
        "next_note": note,
        "ai_share": ai_accepted if ai_accepted >= 2 and ai_accepted >= own_accepted else 0,
        "children": item.children.filter(merged_into=None).order_by("is_done", "number"),
        "merged": item.merged.all(),
        "blocks": item.blocks.filter(is_done=False),
        "phases": PHASES,
        "fields": Field.objects.filter(workspace=item.workspace).prefetch_related("choices"),
        "in_phase_for": span.duration if span else None,
        "avg_loop": sum(loop_times, timedelta()) / len(loop_times) if loop_times else None,
        "public": public,
    }


def intent_from(parent):
    """What the loop above is set on: its latest experiments, or else its hypotheses."""
    if parent is None:
        return None
    for d in parent.documents.filter(phase=DECIDE).order_by("-cycle"):
        if document.has_content(d.body):
            return {"item": parent, "doc": d}
    for d in parent.documents.filter(phase=ORIENT).order_by("-cycle"):
        found = document.hypotheses(d.body)
        if found:
            return {"item": parent, "doc": d, "html": found}
    return {"item": parent, "doc": None}


def voices(item):
    """How many people, besides agents, have taken part: a closed loop with one voice decays."""
    people = {item.created_by_id}
    people |= set(item.comments.filter(author__is_agent=False).values_list("author_id", flat=True))
    people |= set(Reply.objects.filter(comment__item=item, author__is_agent=False).values_list("author_id", flat=True))
    people |= set(item.entries.filter(author__isnull=False).values_list("author_id", flat=True))
    people.discard(None)
    return len(people) + item.entries.filter(from_outside=True).count()


def readings_before(doc):
    """Other readings still standing on the document before this one: input, not noise."""
    before = doc.previous()
    if before is None:
        return []
    return list(before.comments.filter(kind=Comment.READING, status=Comment.OPEN).select_related("author"))


def outcomes_for(doc):
    """For an Act document: each experiment from the same loop's Decide, and its verdict."""
    if doc.phase != ACT:
        return []
    decide = Document.objects.filter(item=doc.item, phase=DECIDE, cycle=doc.cycle).first()
    if decide is None:
        return []
    verdicts = {v.experiment: v for v in Verdict.objects.filter(item=doc.item, cycle=doc.cycle)}
    labels = experiment_labels()
    rows = []
    for name, fields in experiments(decide.body).items():
        if any(value for _line, value in fields.values()):
            rows.append({
                "kind": "experiment",
                "name": name,
                "hypothesis": fields.get(labels[0], ("", ""))[1],
                "then": fields.get(labels[5], ("", ""))[1],
                "verdict": verdicts.get(name),
                "choices": Verdict.OUTCOMES,
            })
    for kind, labels_, found in (("risk", document.risk_labels(), document.risks(decide.body)),
                                 ("opportunity", document.opportunity_labels(), document.opportunities(decide.body))):
        rows += uncertainty_rows(kind, labels_, found, verdicts)
    return rows


def uncertainty_rows(kind, labels, found, verdicts):
    """Risks and opportunities: what could happen, its early signal, and what we will do then."""
    what, signal, then = labels
    rows = []
    for name, fields in found.items():
        if fields.get(what, ("", ""))[1]:
            rows.append({
                "kind": kind,
                "name": name,
                "hypothesis": fields[what][1],
                "signal": fields.get(signal, ("", ""))[1],
                "then": fields.get(then, ("", ""))[1],
                "verdict": verdicts.get(name),
                "choices": Verdict.RISK_OUTCOMES,
            })
    return rows


@member_required
@require_POST
def verdict(request, number):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    outcome = request.POST.get("outcome")
    name = request.POST.get("experiment", "")[:200]
    known = [row["name"] for row in outcomes_for(item.document)]
    if name in known and outcome in dict(Verdict.OUTCOMES):
        Verdict.objects.update_or_create(item=item, cycle=item.cycle, experiment=name,
                                         defaults={"outcome": outcome, "decided_by": request.user})
    elif name in known and outcome == "":
        Verdict.objects.filter(item=item, cycle=item.cycle, experiment=name).delete()
    return redirect(item.get_absolute_url() + "#outcomes")


@member_required
@require_POST
def observe_now(request, number):
    """Anyone can note an observation on an item, whatever phase it is in."""
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    body = request.POST.get("body", "").strip()
    if body:
        signal = request.POST.get("signal") if request.POST.get("signal") in dict(Entry.SIGNALS) else Entry.SEEN
        Entry.objects.create(workspace=request.workspace, item=item, kind=Entry.OBSERVATION, body=body,
                             author=request.user, cycle=item.cycle, signal=signal)
        editors = list(item.assignees.all()) or ([item.created_by] if item.created_by else [])
        notify.observed(item, editors, request.user, body)
    return redirect(item.get_absolute_url() + "#observations")


@member_required
@require_POST
def reorient(request, number):
    """Back to Orient on purpose: the picture no longer fits what we see."""
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    entry = item.pending_observations().filter(pk=request.POST.get("observation") or 0).first()
    reason = request.POST.get("reason", "").strip() or (entry.body if entry else "")
    if item.phase not in (DECIDE, ACT) or item.is_done or not reason:
        return redirect(item)
    item.reorient(request.user, reason)
    doc = item.document
    doc.append(document.heading(_("Re-oriented: what no longer fits")) + document.from_text(reason))
    handled = [entry] if entry else []
    Entry.objects.filter(pk__in=[e.pk for e in handled]).update(
        handled_at=timezone.now(), handled_by=request.user, handled_how="reoriented")
    messages.success(request, _("Back in Orient. The reason is at the end of the document."))
    return redirect(item)


@member_required
@require_POST
def observation_noted(request, pk):
    """Take an observation in without going back: note it, or add it to the document."""
    entry = get_object_or_404(Entry, workspace=request.workspace, pk=pk, item__isnull=False, handled_at=None)
    item = entry.item
    if not item.can_edit(request.user, request.membership):
        raise PermissionDenied
    how = "added" if request.POST.get("action") == "add" else "noted"
    if how == "added":
        item.document.append(document.from_entry(entry))
    entry.handled_at = timezone.now()
    entry.handled_by = request.user
    entry.handled_how = how
    entry.save(update_fields=["handled_at", "handled_by", "handled_how"])
    return redirect(item.get_absolute_url() + "#observations")


@member_required
def library_page(request):
    lenses = library.ensure(request.workspace)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:120]
        question = request.POST.get("question", "").strip()[:300]
        if name and question:
            Lens.objects.create(workspace=request.workspace, name=name, question=question,
                                source=request.POST.get("source", "").strip()[:160],
                                note=request.POST.get("note", "").strip(), added_by=request.user)
        return redirect("board:library")
    return render(request, "board/library.html", {"lenses": lenses})


@member_required
@require_POST
def delete_lens(request, pk):
    lens = get_object_or_404(Lens, workspace=request.workspace, pk=pk)
    if not (request.membership.is_owner or lens.added_by_id == request.user.pk):
        raise PermissionDenied
    lens.delete()
    return redirect("board:library")


@member_required
@require_POST
def use_lens(request, number, pk):
    """Add a lens's question to the Orient document, to answer there."""
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    lens = get_object_or_404(Lens, workspace=request.workspace, pk=pk)
    if item.phase == ORIENT:
        item.document.append(document.heading(f"{lens.question} ({lens.name})") + "<p><br></p>")
    return redirect(item)


@member_required
def story_page(request, number):
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    from . import story

    return render(request, "board/story.html", {"item": item, "episodes": story.episodes(item)})


@member_required
def hypotheses_page(request):
    from . import hypotheses

    items = Item.objects.filter(workspace=request.workspace, merged_into=None)
    found = hypotheses.for_items(items)
    status = request.GET.get("status", "")
    counts = {key: sum(1 for h in found if h.status == key) for key, _label in hypotheses.STATUSES}
    shown = [h for h in found if not status or h.status == status]
    order = [key for key, _label in hypotheses.STATUSES]
    shown.sort(key=lambda h: (order.index(h.status), -h.item.number))
    return render(request, "board/hypotheses.html", {
        "hypotheses": shown, "statuses": hypotheses.STATUSES, "counts": counts, "status": status,
        "total": len(found),
    })


@member_required
def item(request, number, doc_pk=None):
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    doc = get_object_or_404(Document, item=item, pk=doc_pk) if doc_pk else None
    context = item_page_context(item, doc)
    context.update({
        "can_edit": context["is_current"] and item.can_edit(request.user, request.membership),
        "can_comment": context["is_current"],
        "form": ItemForm(instance=item, workspace=request.workspace),
        "link_form": LinkForm(),
        "candidates": Item.objects.filter(workspace=request.workspace, merged_into=None, is_done=False)
        .exclude(pk=item.pk).order_by("number"),
    })
    context["can_change"] = item.can_edit(request.user, request.membership)
    return render(request, "board/item.html", context)


@member_required
@require_POST
def save_document(request, number):
    """Autosave from the editor. Refuses to overwrite a newer version."""
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return HttpResponse(status=410)
    if not item.can_edit(request.user, request.membership):
        return HttpResponseForbidden()
    doc = item.document
    try:
        version = int(request.POST.get("version", ""))
    except ValueError:
        return HttpResponseBadRequest()
    if version != doc.version:
        return JsonResponse({"version": doc.version}, status=409)
    old_body = doc.body
    title = request.POST.get("title", "").strip()[:200]
    if title and title != item.title:
        item.title = title
        item.save(update_fields=["title", "updated_at"])
    doc.body = document.sanitize(request.POST.get("body", ""))
    doc.version += 1
    doc.save(update_fields=["body", "version", "updated_at"])
    mention_new(request, item, old_body, doc.body)
    return JsonResponse({"version": doc.version})


@member_required
@require_POST
def submit_review(request, number):
    """Ask everyone but the item's creator to comment and suggest changes."""
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    doc = item.document
    doc.status = Document.REVIEW
    doc.submitted_by = request.user
    doc.submitted_at = timezone.now()
    doc.save(update_fields=["status", "submitted_by", "submitted_at"])
    reviewers = [m.user for m in request.workspace.memberships.select_related("user")
                 if m.user_id != item.created_by_id]
    notify.review_requested(item, doc, reviewers, request.user)
    messages.success(request, _("Sent for review."))
    return redirect(item)


def mention_new(request, item, old_html, new_html):
    """E-mail people who are @mentioned in the new text but weren't before."""
    already = set(notify.mentioned_users(document.plain_text(old_html), request.workspace))
    fresh = set(notify.mentioned_users(document.plain_text(new_html), request.workspace)) - already
    notify.mentioned(item, fresh, request.user, document.plain_text(new_html))


@member_required
@require_POST
def edit(request, number):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    before = set(item.assignees.all())
    form = ItemForm(request.POST, instance=item, workspace=request.workspace)
    if form.is_valid():
        form.save()
        if item.deadline != form.initial.get("deadline"):
            item.deadline_reminded_on = None
            item.save(update_fields=["deadline_reminded_on"])
        notify.assigned(item, set(item.assignees.all()) - before, request.user)
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect(item)


@member_required
@require_POST
def phase(request, number):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    target = request.POST.get("phase")
    if target == "next":
        item.advance(request.user)
    elif target in PHASE_ORDER:
        item.set_phase(target, request.user)
    if request.headers.get("X-Requested-With") == "fetch":
        return HttpResponse(status=204)
    return redirect(request.POST.get("next") or item.get_absolute_url())


@member_required
@require_POST
def move(request, number):
    """Drag and drop on the board: set the phase or a field option."""
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return HttpResponse(status=409)
    group, value = request.POST.get("group", "phase"), request.POST.get("value", "")
    if group == "phase":
        if value in PHASE_ORDER:
            item.set_phase(value, request.user)
    else:
        field = get_object_or_404(Field, workspace=request.workspace, pk=group)
        if not field.allow_many:
            item.options.remove(*item.options_for(field))
        if value:
            item.options.add(get_object_or_404(FieldOption, field=field, pk=value))
        item.save(update_fields=["updated_at"])
    return HttpResponse(status=204)


@member_required
@require_POST
def done(request, number):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    item.set_done(not item.is_done, request.user)
    if item.is_done:
        for waiting in item.blocks.filter(is_done=False):
            if not waiting.is_blocked:
                notify.unblocked(waiting, item, request.user)
    return redirect(request.POST.get("next") or item.get_absolute_url())


@member_required
@require_POST
def add_entry(request):
    form = EntryForm(request.POST)
    if form.is_valid():
        entry = form.save(commit=False)
        entry.workspace = request.workspace
        entry.author = request.user
        entry.save()
    return redirect("board:inbox")


@member_required
@require_POST
def delete_entry(request, pk):
    get_object_or_404(Entry, workspace=request.workspace, pk=pk, item=None, author=request.user).delete()
    return redirect("board:inbox")


@member_required
@require_POST
def add_link(request, number):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    form = LinkForm(request.POST)
    if form.is_valid():
        link = form.save(commit=False)
        link.item = item
        link.added_by = request.user
        link.save()
    else:
        messages.error(request, _("That doesn't look like a web address."))
    return redirect(item)


@member_required
@require_POST
def delete_link(request, pk):
    link = get_object_or_404(Link, item__workspace=request.workspace, pk=pk)
    if not link.item.can_edit(request.user, request.membership):
        raise PermissionDenied
    link.delete()
    return redirect(link.item)


def find_item(request, value):
    value = (value or "").strip().lstrip("#")
    if not value.isdigit():
        return None
    return Item.objects.filter(workspace=request.workspace, number=int(value), merged_into=None).first()


@member_required
@require_POST
def depend(request, number):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    other = find_item(request, request.POST.get("other"))
    if other is None or other == item:
        messages.error(request, _("No such item."))
    elif creates_cycle(item, other):
        messages.error(request, _("#%(n)s already waits for this one.") % {"n": other.number})
    else:
        item.depends_on.add(other)
    return redirect(item)


def creates_cycle(item, other):
    """Would `item` waiting for `other` make a loop of dependencies?"""
    seen, stack = set(), [other]
    while stack:
        current = stack.pop()
        if current.pk == item.pk:
            return True
        if current.pk not in seen:
            seen.add(current.pk)
            stack.extend(current.depends_on.all())
    return False


@member_required
@require_POST
def undepend(request, number, other):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    item.depends_on.remove(*item.depends_on.filter(number=other))
    return redirect(item)


@member_required
@require_POST
def merge(request, number):
    item, redirect_to = get_item(request, number, edit=True)
    if redirect_to:
        return redirect_to
    target = find_item(request, request.POST.get("into"))
    if target is None or target == item:
        messages.error(request, _("No such item."))
        return redirect(item)
    item.merge_into(target, request.user)
    messages.success(request, _("#%(a)s is now part of #%(b)s.") % {"a": item.number, "b": target.number})
    return redirect(target)


@member_required
@require_POST
def comment(request, number):
    """Start a thread: a comment, or a suggestion to replace the quoted passage."""
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    kind = request.POST.get("kind") if request.POST.get("kind") in dict(Comment.KINDS) else Comment.COMMENT
    quote = request.POST.get("quote", "")[:2000]
    body = request.POST.get("body", "").strip()
    replacement = request.POST.get("replacement", "")[:4000]
    if kind == Comment.SUGGESTION and (not quote or replacement == quote):
        return redirect(item)
    if kind in (Comment.COMMENT, Comment.READING) and not body:
        return redirect(item)
    c = Comment.objects.create(
        item=item, document=item.document, kind=kind, quote=quote, prefix=request.POST.get("prefix", "")[-60:],
        replacement=replacement if kind == Comment.SUGGESTION else "", body=body, author=request.user,
    )
    notify.mentioned(item, notify.mentioned_users(body, request.workspace), request.user, body)
    if kind == Comment.SUGGESTION:
        editors = [u for u in item.assignees.all()] or [m.user for m in request.workspace.memberships.filter(
            role="owner").select_related("user")]
        notify.suggested(item, editors, request.user, c)
    return redirect(f"{item.get_absolute_url()}#c{c.pk}")


@member_required
@require_POST
def reply(request, pk):
    c = get_object_or_404(Comment, item__workspace=request.workspace, pk=pk)
    body = request.POST.get("body", "").strip()
    if body:
        Reply.objects.create(comment=c, body=body, author=request.user)
        thread = {c.author, *[r.author for r in c.replies.select_related("author")]}
        notify.replied(c.item, thread, request.user, c, body)
        notify.mentioned(c.item, set(notify.mentioned_users(body, request.workspace)) - thread,
                         request.user, body)
    return redirect(f"{c.item.get_absolute_url()}#c{c.pk}")


@member_required
@require_POST
def comment_status(request, pk):
    """Resolve or reopen a thread; accept or reject a suggestion.

    Editors of the item may do all of it. Whoever started a thread may resolve
    or reopen it, which for a suggestion means withdrawing it.
    """
    c = get_object_or_404(Comment, item__workspace=request.workspace, pk=pk)
    item = c.item
    editor = item.can_edit(request.user, request.membership)
    action = request.POST.get("action")
    if action in ("resolve", "reopen"):
        # Another reading stays until the person who offered it withdraws it.
        allowed = c.author_id == request.user.pk or (editor and not c.is_reading)
        if not allowed:
            return HttpResponseForbidden()
        c.close(Comment.RESOLVED if action == "resolve" else Comment.OPEN, request.user)
    elif action in ("accept", "reject") and c.is_suggestion and c.is_open:
        if not editor:
            return HttpResponseForbidden()
        if action == "accept":
            # The browser applies the change to the document and sends the result.
            try:
                version = int(request.POST.get("version", ""))
            except ValueError:
                return HttpResponseBadRequest()
            doc = c.document
            if doc is None or not doc.is_current or version != doc.version:
                messages.error(request, _("The document changed meanwhile. Try again."))
                return redirect(item)
            old_body = doc.body
            doc.body = document.sanitize(request.POST.get("body", ""))
            doc.version += 1
            doc.save(update_fields=["body", "version", "updated_at"])
            mention_new(request, item, old_body, doc.body)
        c.close(Comment.ACCEPTED if action == "accept" else Comment.REJECTED, request.user)
        notify.decided(item, c, request.user)
    return redirect(f"{item.get_absolute_url()}#c{c.pk}")


# --- Inbox -----------------------------------------------------------------


@member_required
def inbox(request):
    entries = Entry.objects.filter(workspace=request.workspace, item=None).select_related("author")
    return render(request, "board/inbox.html", {
        "entries": entries,
        "entry_form": EntryForm(),
        "candidates": Item.objects.filter(workspace=request.workspace, merged_into=None, is_done=False)
        .order_by("number"),
    })


@member_required
@require_POST
def file_entry(request, pk):
    """Turn a loose entry into a new item, or file it under an existing one.

    Filed under an item, it is added to the end of the document, or, for
    someone who may not edit that document, left there as a comment.
    """
    entry = get_object_or_404(Entry, workspace=request.workspace, pk=pk, item=None)
    if request.POST.get("into") == "new":
        title = request.POST.get("title", "").strip() or entry.body.splitlines()[0][:120]
        phase = {v: k for k, v in Entry.KIND_FOR_PHASE.items()}.get(entry.kind, OBSERVE)
        target = Item.objects.create(
            workspace=request.workspace, title=title[:200], created_by=request.user, phase=phase,
        )
        doc = target.document
        doc.body = document.from_text(entry.body) + doc.body
        doc.save(update_fields=["body"])
    else:
        target = find_item(request, request.POST.get("into"))
        if target is None:
            messages.error(request, _("No such item."))
            return redirect("board:inbox")
        entry.item = target
        entry.cycle = target.cycle
        entry.save(update_fields=["item", "cycle"])
        messages.success(request, _("Filed under #%(n)s.") % {"n": target.number})
        return redirect("board:inbox")
    entry.delete()
    messages.success(request, _("Filed under #%(n)s.") % {"n": target.number})
    return redirect("board:inbox")


# --- Tempo -----------------------------------------------------------------


def loop_durations(spans):
    """Time from one Observe to the next, for each completed loop."""
    starts = {}
    for span in spans:
        if span.phase == OBSERVE:
            starts.setdefault((span.item_id, span.cycle), span.started_at)
    return [
        starts[(item_id, cycle + 1)] - start
        for (item_id, cycle), start in starts.items()
        if (item_id, cycle + 1) in starts
    ]


@member_required
def tempo(request):
    workspace = request.workspace
    days = int(request.GET.get("days", 30)) if request.GET.get("days", "30").isdigit() else 30
    now = timezone.now()
    since = now - timedelta(days=days)
    spans = list(PhaseSpan.objects.filter(item__workspace=workspace, started_at__gte=since - timedelta(days=days)))
    recent = [s for s in spans if s.ended_at and s.ended_at >= since]
    previous = [s for s in spans if s.ended_at and since - timedelta(days=days) <= s.ended_at < since]

    def per_phase(group):
        out = {}
        for key, _label in PHASES:
            durations = [s.duration for s in group if s.phase == key]
            out[key] = median(durations) if durations else None
        return out

    now_phase, before_phase = per_phase(recent), per_phase(previous)
    phase_rows = []
    longest = max([d for d in now_phase.values() if d] or [timedelta(seconds=1)])
    for key, label in PHASES:
        cur, prev = now_phase[key], before_phase[key]
        phase_rows.append({
            "key": key, "label": label, "median": cur, "previous": prev,
            "share": (cur / longest * 100) if cur else 0,
            "faster": (cur < prev) if cur and prev else None,
        })

    all_spans = list(
        PhaseSpan.objects.filter(item__workspace=workspace).select_related("item").order_by("started_at")
    )
    loops_by_end = []
    starts = {}
    for span in all_spans:
        if span.phase == OBSERVE:
            starts.setdefault((span.item_id, span.cycle), span.started_at)
    for (item_id, cycle), start in starts.items():
        end = starts.get((item_id, cycle + 1))
        if end:
            loops_by_end.append((end, end - start))
    depth = {i.pk: i.depth() for i in Item.objects.filter(workspace=workspace).select_related("parent__parent__parent")}
    levels = {}
    for (item_id, cycle), start in starts.items():
        end = starts.get((item_id, cycle + 1))
        if end and end >= since:
            levels.setdefault(min(depth.get(item_id, 1), 3), []).append(end - start)
    level_rows = [{"level": lvl, "loops": len(ds), "median": median(ds)} for lvl, ds in sorted(levels.items())]
    loops_now = [d for end, d in loops_by_end if end >= since]
    loops_prev = [d for end, d in loops_by_end if since - timedelta(days=days) <= end < since]

    weeks = []
    for w in range(7, -1, -1):
        start = now - timedelta(weeks=w + 1)
        end = now - timedelta(weeks=w)
        weeks.append({"start": start, "count": sum(1 for e, _d in loops_by_end if start <= e < end)})
    peak = max([w["count"] for w in weeks] or [0]) or 1
    for w in weeks:
        w["height"] = w["count"] / peak * 100

    stuck = sorted(
        (s for s in all_spans if s.ended_at is None and not s.item.is_done and s.item.merged_into_id is None),
        key=lambda s: s.started_at,
    )[:8]

    # From noticing something to doing something about it.
    first_act = {}
    for span in all_spans:
        if span.phase == ACT:
            first_act.setdefault(span.item_id, []).append(span.started_at)
    observations = list(Entry.objects.filter(workspace=workspace, item__isnull=False).select_related("item"))

    def notice_to_act(window_start, window_end):
        out = []
        for e in observations:
            act = next((t for t in first_act.get(e.item_id, []) if t >= e.created_at), None)
            if act and window_start <= act < window_end:
                out.append(act - e.created_at)
        return out

    def taken_in(window_start, window_end):
        return [e.handled_at - e.created_at for e in observations
                if e.handled_at and window_start <= e.handled_at < window_end]

    before = since - timedelta(days=days)
    act_now, act_prev = notice_to_act(since, now), notice_to_act(before, since)
    taken_now, taken_prev = taken_in(since, now), taken_in(before, since)
    reorients = [s for s in all_spans if s.reason and s.started_at >= since]
    reorients_prev = sum(1 for s in all_spans if s.reason and before <= s.started_at < since)
    experiment_word = _("Experiment")
    verdicts = [v for v in Verdict.objects.filter(item__workspace=workspace, decided_at__gte=before)
                if v.experiment.startswith(experiment_word)]
    tested = {key: sum(1 for v in verdicts if v.outcome == key and v.decided_at >= since) for key, _l in Verdict.OUTCOMES}
    tested_prev = sum(1 for v in verdicts if v.decided_at < since)

    # Medians hide the loops that matter most. Show the ones far from the typical.
    typical = median([d for _e, d in loops_by_end]) if loops_by_end else None
    outliers = []
    if typical:
        ends = {}
        for (item_id, cycle), start in starts.items():
            end = starts.get((item_id, cycle + 1))
            if end and (end - start >= typical * 3 or end - start <= typical / 3):
                ends[(item_id, cycle)] = end - start
        items_by_pk = {i.pk: i for i in Item.objects.filter(pk__in=[k[0] for k in ends])}
        outliers = sorted(({"item": items_by_pk[k[0]], "cycle": k[1], "took": d, "longer": d > typical}
                           for k, d in ends.items()), key=lambda r: -abs((r["took"] - typical).total_seconds()))[:6]

    # Trust grows from having handled things: what went against us, and what we did then.
    handled = list(Verdict.objects.filter(item__workspace=workspace, outcome=Verdict.FAILED)
                   .select_related("item").order_by("-decided_at")[:5])
    handled_total = Verdict.objects.filter(item__workspace=workspace, outcome=Verdict.FAILED).count() + sum(
        1 for s in all_spans if s.reason)

    # Where the picture may be out of date: observations nobody has taken in.
    waiting = {}
    for e in observations:
        if not e.handled_at and not e.item.is_done and e.item.merged_into_id is None:
            waiting.setdefault(e.item, []).append(e)
    stale = sorted(
        ({"item": item, "count": len(es), "oldest": now - min(e.created_at for e in es)} for item, es in waiting.items()),
        key=lambda r: r["oldest"], reverse=True,
    )[:8]

    return render(request, "board/tempo.html", {
        "days": days,
        "phase_rows": phase_rows,
        "loop_median": median(loops_now) if loops_now else None,
        "loop_previous": median(loops_prev) if loops_prev else None,
        "loops_closed": len(loops_now),
        "loops_closed_previous": len(loops_prev),
        "weeks": weeks,
        "stuck": stuck,
        "notice_to_act": median(act_now) if act_now else None,
        "notice_to_act_previous": median(act_prev) if act_prev else None,
        "taken_in": median(taken_now) if taken_now else None,
        "taken_in_previous": median(taken_prev) if taken_prev else None,
        "reorients": reorients,
        "reorients_previous": reorients_prev,
        "tested": tested,
        "tested_total": sum(tested.values()),
        "tested_previous": tested_prev,
        "stale": stale,
        "level_rows": level_rows,
        "typical": typical,
        "outliers": outliers,
        "handled": handled,
        "handled_total": handled_total,
    })


# --- Public, read-only -----------------------------------------------------


def public_workspace(token):
    workspace = Workspace.objects.filter(public_token=token, is_public=True).first()
    if workspace is None:
        raise Http404
    return workspace


def public_index(request, token):
    workspace = public_workspace(token)
    items, filters = filter_items(request, workspace)
    context = board_context(request, workspace, items, filters, public=True)
    context.update({"workspace": workspace, "token": token})
    return render(request, "board/index.html", context)


@require_POST
def public_observe(request, token, number):
    """A visitor to a published board notes what they see. It waits on the item like any observation."""
    workspace = public_workspace(token)
    if not workspace.open_to_observations:
        raise Http404
    item = get_object_or_404(Item, workspace=workspace, number=number, merged_into=None)
    body = request.POST.get("body", "").strip()[:4000]
    waiting = Entry.objects.filter(workspace=workspace, from_outside=True, handled_at__isnull=True).count()
    # "website" is a field people never see; only robots fill it in.
    if body and not request.POST.get("website") and waiting < 100:
        Entry.objects.create(workspace=workspace, item=item, kind=Entry.OBSERVATION, body=body,
                             cycle=item.cycle, from_outside=True,
                             outside_name=request.POST.get("name", "").strip()[:80])
        editors = list(item.assignees.all()) or ([item.created_by] if item.created_by else [])
        notify.observed(item, editors, None, body)
        messages.success(request, _("Thank you. Your observation reached the people working on this."))
    return redirect("public_item", token=token, number=item.number)


def public_story(request, token, number):
    from . import story

    workspace = public_workspace(token)
    item = get_object_or_404(Item, workspace=workspace, number=number, merged_into=None)
    return render(request, "board/story.html", {
        "item": item, "episodes": story.episodes(item), "public": True, "token": token, "workspace": workspace,
    })


def public_item(request, token, number, doc_pk=None):
    workspace = public_workspace(token)
    item = get_object_or_404(Item, workspace=workspace, number=number)
    if item.merged_into_id:
        return redirect("public_item", token=token, number=item.merged_into.number)
    doc = get_object_or_404(Document, item=item, pk=doc_pk) if doc_pk else None
    context = item_page_context(item, doc, public=True)
    context.update({"workspace": workspace, "token": token})
    return render(request, "board/item.html", context)
