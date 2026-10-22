from .models import Entry


def board_context(request):
    """Things every page needs: workspace and how much waits in the inbox."""
    workspace = getattr(request, "workspace", None)
    if workspace is None:
        return {}
    return {
        "workspace": workspace,
        "membership": request.membership,
        "inbox_count": Entry.objects.filter(workspace=workspace, item__isnull=True).count(),
    }
