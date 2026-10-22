from datetime import timedelta
from statistics import median

from django.contrib import messages
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import member_required
from accounts.models import Workspace

from . import notify
from .forms import EntryForm, ItemForm, LinkForm, workspace_users
from .models import OBSERVE, PHASE_ORDER, PHASES, Comment, Entry, Field, FieldOption, Item, Link, PhaseSpan

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
            entry_count=Count("entries", distinct=True),
            open_comments=Count("comments", filter=Q(comments__resolved=False), distinct=True),
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
    }
    items = item_queryset(workspace)
    if f["q"]:
        q = f["q"]
        cond = Q(title__icontains=q) | Q(body__icontains=q) | Q(entries__body__icontains=q)
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
    f["active"] = any([f["q"], f["phase"], f["who"], f["prio"], f["due"], f["opt"], f["state"] != "open"])
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
        notify.mentioned(item, notify.mentioned_users(item.body, request.workspace), request.user, item.body)
        return redirect(item)
    return render(request, "board/new.html", {"form": form})


# --- One item --------------------------------------------------------------


def get_item(request, number):
    item = get_object_or_404(Item, workspace=request.workspace, number=number)
    if item.merged_into_id:
        return None, redirect(item.merged_into)
    return item, None


def item_page_context(item, public=False):
    entries = list(item.entries.select_related("author"))
    loops = {}
    for entry in entries:
        loops.setdefault(entry.cycle or 1, []).append(entry)
    spans = list(item.spans.all())
    loop_times = loop_durations(spans)
    current = next((s for s in reversed(spans) if s.ended_at is None), None)
    return {
        "item": item,
        "loops": sorted(loops.items(), reverse=True),
        "comments": item.comments.select_related("author").order_by("resolved", "created_at"),
        "children": item.children.filter(merged_into=None).order_by("is_done", "number"),
        "merged": item.merged.all(),
        "blocks": item.blocks.filter(is_done=False),
        "phases": PHASES,
        "fields": Field.objects.filter(workspace=item.workspace).prefetch_related("choices"),
        "in_phase_for": current.duration if current else None,
        "avg_loop": sum(loop_times, timedelta()) / len(loop_times) if loop_times else None,
        "public": public,
    }


@member_required
def item(request, number):
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    form = ItemForm(instance=item, workspace=request.workspace)
    context = item_page_context(item)
    context.update({
        "form": form,
        "entry_form": EntryForm(initial={"kind": Entry.KIND_FOR_PHASE[item.phase]}),
        "link_form": LinkForm(),
        "people": workspace_users(request.workspace),
        "candidates": Item.objects.filter(workspace=request.workspace, merged_into=None, is_done=False)
        .exclude(pk=item.pk).order_by("number"),
    })
    return render(request, "board/item.html", context)


@member_required
@require_POST
def edit(request, number):
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    before = set(item.assignees.all())
    old_body = item.body
    form = ItemForm(request.POST, instance=item, workspace=request.workspace)
    if form.is_valid():
        form.save()
        if item.deadline != form.initial.get("deadline"):
            item.deadline_reminded_on = None
            item.save(update_fields=["deadline_reminded_on"])
        notify.assigned(item, set(item.assignees.all()) - before, request.user)
        if item.body != old_body:
            already = set(notify.mentioned_users(old_body, request.workspace))
            fresh = set(notify.mentioned_users(item.body, request.workspace)) - already
            notify.mentioned(item, fresh, request.user, item.body)
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect(item)


@member_required
@require_POST
def phase(request, number):
    item, redirect_to = get_item(request, number)
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
    item, redirect_to = get_item(request, number)
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
    item, redirect_to = get_item(request, number)
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
def add_entry(request, number=None):
    item = None
    if number is not None:
        item, redirect_to = get_item(request, number)
        if redirect_to:
            return redirect_to
    form = EntryForm(request.POST)
    if form.is_valid():
        entry = form.save(commit=False)
        entry.workspace = request.workspace
        entry.item = item
        entry.author = request.user
        entry.save()
        if item:
            notify.mentioned(item, notify.mentioned_users(entry.body, request.workspace), request.user, entry.body)
            if request.POST.get("advance") and Entry.KIND_FOR_PHASE[item.phase] == entry.kind:
                item.advance(request.user)
    if item:
        return redirect(item.get_absolute_url() + "#entries")
    return redirect("board:inbox")


@member_required
@require_POST
def delete_entry(request, pk):
    entry = get_object_or_404(Entry, workspace=request.workspace, pk=pk, author=request.user)
    item = entry.item
    entry.delete()
    return redirect(item.get_absolute_url() + "#entries" if item else "board:inbox")


@member_required
@require_POST
def add_link(request, number):
    item, redirect_to = get_item(request, number)
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
    item, redirect_to = get_item(request, number)
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
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    item.depends_on.remove(*item.depends_on.filter(number=other))
    return redirect(item)


@member_required
@require_POST
def merge(request, number):
    item, redirect_to = get_item(request, number)
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
    item, redirect_to = get_item(request, number)
    if redirect_to:
        return redirect_to
    body = request.POST.get("body", "").strip()
    target = request.POST.get("target", "body")
    if not (target == "body" or (target.startswith("entry-") and target[6:].isdigit())):
        target = "body"
    if body:
        Comment.objects.create(
            item=item, target=target, quote=request.POST.get("quote", "")[:2000], body=body, author=request.user
        )
        to = set(notify.mentioned_users(body, request.workspace))
        notify.mentioned(item, to, request.user, body)
    return redirect(item.get_absolute_url() + "#comments")


@member_required
@require_POST
def resolve(request, pk):
    c = get_object_or_404(Comment, item__workspace=request.workspace, pk=pk)
    c.resolved = not c.resolved
    c.resolved_by = request.user if c.resolved else None
    c.save(update_fields=["resolved", "resolved_by"])
    return redirect(c.item.get_absolute_url() + "#comments")


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
    """Turn a loose entry into a new item, or file it under an existing one."""
    entry = get_object_or_404(Entry, workspace=request.workspace, pk=pk, item=None)
    if request.POST.get("into") == "new":
        title = request.POST.get("title", "").strip() or entry.body.splitlines()[0][:120]
        phase = {v: k for k, v in Entry.KIND_FOR_PHASE.items()}.get(entry.kind, OBSERVE)
        target = Item.objects.create(
            workspace=request.workspace, title=title, created_by=request.user, phase=phase
        )
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

    return render(request, "board/tempo.html", {
        "days": days,
        "phase_rows": phase_rows,
        "loop_median": median(loops_now) if loops_now else None,
        "loop_previous": median(loops_prev) if loops_prev else None,
        "loops_closed": len(loops_now),
        "loops_closed_previous": len(loops_prev),
        "weeks": weeks,
        "stuck": stuck,
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


def public_item(request, token, number):
    workspace = public_workspace(token)
    item = get_object_or_404(Item, workspace=workspace, number=number)
    if item.merged_into_id:
        return redirect("public_item", token=token, number=item.merged_into.number)
    context = item_page_context(item, public=True)
    context.update({"workspace": workspace, "token": token})
    return render(request, "board/item.html", context)
