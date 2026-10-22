class WorkspaceMiddleware:
    """Attach the signed-in user's workspace and membership to the request.

    A user belongs to one workspace for now; the model allows more later.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.workspace = None
        request.membership = None
        if request.user.is_authenticated:
            membership = request.user.memberships.select_related("workspace").first()
            if membership:
                request.membership = membership
                request.workspace = membership.workspace
        return self.get_response(request)
