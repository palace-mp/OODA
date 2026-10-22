from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from board.forms import FieldForm
from board.models import Field, FieldOption

from .decorators import member_required, owner_required
from .forms import NewWorkspaceSignupForm, ProfileForm, SignupForm, WorkspaceForm
from .models import Invite, Membership, User, Workspace, make_token


def start(request):
    """First run: nobody exists yet, so the first person creates the account."""
    if User.objects.exists():
        if settings.ALLOW_SIGNUP:
            return signup(request)
        raise Http404
    if settings.SETUP_KEY and request.GET.get("key") != settings.SETUP_KEY:
        raise Http404
    return signup(request, first=True)


def signup(request, first=False):
    if not (first or settings.ALLOW_SIGNUP):
        raise Http404
    form = NewWorkspaceSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            if first:
                user.is_staff = user.is_superuser = True
                user.save()
            workspace = Workspace.objects.create(name=form.cleaned_data["workspace"])
            Membership.objects.create(workspace=workspace, user=user, role=Membership.OWNER)
        login(request, user)
        return redirect("board:index")
    return render(request, "accounts/signup.html", {"form": form, "first": first})


def join(request, token):
    invite = Invite.objects.filter(token=token).select_related("workspace").first()
    if invite is None or not invite.is_valid:
        return render(request, "accounts/invite_invalid.html", status=410)
    if request.user.is_authenticated:
        if request.method == "POST":
            Membership.objects.get_or_create(workspace=invite.workspace, user=request.user)
            invite.uses += 1
            invite.save(update_fields=["uses"])
            return redirect("board:index")
        return render(request, "accounts/join.html", {"invite": invite})
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            Membership.objects.create(workspace=invite.workspace, user=user, role=Membership.MEMBER)
            invite.uses += 1
            invite.save(update_fields=["uses"])
        login(request, user)
        messages.success(request, _("Welcome to %(w)s.") % {"w": invite.workspace})
        return redirect("board:index")
    return render(request, "accounts/join.html", {"invite": invite, "form": form})


@login_required
def no_workspace(request):
    if request.workspace:
        return redirect("board:index")
    return render(request, "accounts/no_workspace.html")


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        return redirect("profile")
    return render(request, "accounts/profile.html", {"form": form})


# --- Account settings ------------------------------------------------------


@member_required
def workspace_settings(request):
    workspace = request.workspace
    is_owner = request.membership.is_owner
    form = WorkspaceForm(request.POST or None, instance=workspace)
    if request.method == "POST" and is_owner and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        return redirect("settings")
    return render(request, "accounts/settings.html", {
        "form": form,
        "is_owner": is_owner,
        "memberships": workspace.memberships.select_related("user"),
        "invites": workspace.invites.filter(revoked=False) if is_owner else [],
        "fields": Field.objects.filter(workspace=workspace).prefetch_related("choices"),
        "field_form": FieldForm(),
        "colors": FieldOption.COLORS,
        "public_url": settings.SITE_URL + reverse("public_index", args=[workspace.public_token]),
        "site_url": settings.SITE_URL,
    })


@owner_required
@require_POST
def create_invite(request):
    Invite.objects.create(workspace=request.workspace, created_by=request.user)
    return redirect(reverse("settings") + "#people")


@owner_required
@require_POST
def revoke_invite(request, pk):
    Invite.objects.filter(workspace=request.workspace, pk=pk).update(revoked=True)
    return redirect(reverse("settings") + "#people")


@owner_required
@require_POST
def change_member(request, pk):
    membership = get_object_or_404(Membership, workspace=request.workspace, pk=pk)
    owners = request.workspace.memberships.filter(role=Membership.OWNER)
    action = request.POST.get("action")
    if membership.is_owner and owners.count() == 1 and action in ("member", "remove"):
        messages.error(request, _("An account needs at least one owner."))
    elif action == "remove":
        membership.delete()
    elif action in (Membership.OWNER, Membership.MEMBER):
        membership.role = action
        membership.save(update_fields=["role"])
    return redirect(reverse("settings") + "#people")


@owner_required
@require_POST
def publish(request):
    workspace = request.workspace
    action = request.POST.get("action")
    if action == "publish":
        workspace.is_public = True
    elif action == "unpublish":
        workspace.is_public = False
    elif action == "new-link":
        workspace.public_token = make_token()
    workspace.save()
    return redirect(reverse("settings") + "#public")


@owner_required
@require_POST
def add_field(request):
    form = FieldForm(request.POST)
    if form.is_valid():
        field = form.save(commit=False)
        field.workspace = request.workspace
        field.position = request.workspace.fields.count()
        field.save()
        add_options(field, form.cleaned_data["options"])
    return redirect(reverse("settings") + "#taxonomy")


def add_options(field, text):
    start = field.choices.count()
    labels = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for i, label in enumerate(labels):
        FieldOption.objects.create(field=field, label=label[:60], position=start + i,
                                   color=FieldOption.COLORS[(start + i) % len(FieldOption.COLORS)])


@owner_required
@require_POST
def change_field(request, pk):
    field = get_object_or_404(Field, workspace=request.workspace, pk=pk)
    action = request.POST.get("action")
    if action == "delete":
        field.delete()
    elif action == "rename" and request.POST.get("name", "").strip():
        field.name = request.POST["name"].strip()[:60]
        field.allow_many = bool(request.POST.get("allow_many"))
        field.save()
    elif action == "add":
        add_options(field, request.POST.get("options"))
    return redirect(reverse("settings") + "#taxonomy")


@owner_required
@require_POST
def change_option(request, pk):
    option = get_object_or_404(FieldOption, field__workspace=request.workspace, pk=pk)
    if request.POST.get("action") == "delete":
        option.delete()
    else:
        option.label = request.POST.get("label", option.label).strip()[:60] or option.label
        if request.POST.get("color") in FieldOption.COLORS:
            option.color = request.POST["color"]
        option.save()
    return redirect(reverse("settings") + "#taxonomy")
