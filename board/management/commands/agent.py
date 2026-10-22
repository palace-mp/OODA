import time

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from accounts.models import Membership, User, Workspace
from board.agent import Agent, has_credentials


class Command(BaseCommand):
    help = (
        "Run an AI agent as a member of a board. It reviews documents sent for review and answers "
        "in its threads. Uses Claude when Anthropic credentials are set, simple rules otherwise."
    )

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Ada", help="The agent's name, also its @handle (default: Ada)")
        parser.add_argument("--workspace", type=int, help="Board id; defaults to the only (or first) board")
        parser.add_argument("--once", action="store_true", help="Do one round and stop")
        parser.add_argument("--interval", type=int, default=15, help="Seconds between rounds (default: 15)")
        parser.add_argument("--rules", action="store_true", help="Use the simple rules even if Claude is available")

    def handle(self, *args, **options):
        workspace = (Workspace.objects.filter(pk=options["workspace"]) if options["workspace"]
                     else Workspace.objects.order_by("pk")).first()
        if workspace is None:
            raise CommandError("No board found. Create one first.")
        name = options["name"]
        user, created = User.objects.get_or_create(
            email=f"{slugify(name) or 'agent'}@agents.ooda.local",
            defaults={"name": name, "is_agent": True, "password": make_password(None)},
        )
        membership, _ = Membership.objects.get_or_create(workspace=workspace, user=user)
        use_claude = has_credentials() and not options["rules"]
        if use_claude:
            try:
                import anthropic  # noqa: F401
            except ImportError:
                use_claude = False
        self.stdout.write(
            f"{name} ({'new' if created else 'existing'} agent, @{user.handle}) on board “{workspace}”, "
            f"thinking with {'Claude' if use_claude else 'simple rules'}. Ctrl+C stops it."
        )
        agent = Agent(user, membership, use_claude, log=lambda m: self.stdout.write(m))
        while True:
            try:
                agent.run_once()
            except Exception as error:  # keep the agent alive through one bad round
                self.stderr.write(f"Round failed: {error}")
                if options["once"]:
                    raise
            if options["once"]:
                return
            time.sleep(options["interval"])
