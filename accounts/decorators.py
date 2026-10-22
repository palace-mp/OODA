from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def member_required(view):
    protected = login_required(view)

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from .models import User

            if not User.objects.exists() and not settings.SETUP_KEY:
                return redirect("start")
            return protected(request, *args, **kwargs)
        if request.workspace is None:
            return redirect("no_workspace")
        return view(request, *args, **kwargs)

    return wrapper


def owner_required(view):
    @wraps(view)
    def check(request, *args, **kwargs):
        if not request.membership.is_owner:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return member_required(check)
