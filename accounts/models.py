import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def make_token():
    return secrets.token_urlsafe(24)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractUser):
    """Logs in with e-mail. `name` is what other people see."""

    username = None
    first_name = None
    last_name = None
    email = models.EmailField(_("e-mail"), unique=True)
    name = models.CharField(_("name"), max_length=80)
    is_agent = models.BooleanField(_("AI agent"), default=False)
    # For agents: the angle it reviews from, see board/agent.py LENSES.
    agent_lens = models.CharField(_("lens"), max_length=20, blank=True)
    # Relaxed alertness: one e-mail a day with everything, instead of one per event.
    email_digest = models.BooleanField(_("one e-mail a day instead of one per event"), default=False)
    last_digest_at = models.DateTimeField(null=True, blank=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    objects = UserManager()

    def __str__(self):
        return self.name or self.email

    @property
    def handle(self):
        """The word used to @mention this person, e.g. "@anna"."""
        return (self.name.split()[0] if self.name else self.email.split("@")[0]).lower()


class Workspace(models.Model):
    """An account. It owns one board and has up to a few dozen members."""

    name = models.CharField(_("name"), max_length=80)
    is_public = models.BooleanField(_("published"), default=False)
    public_token = models.CharField(max_length=64, default=make_token, unique=True)
    # Visitors to the published board may send observations. Open systems stay alive.
    open_to_observations = models.BooleanField(_("visitors can send observations"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    OWNER = "owner"
    MEMBER = "member"
    ROLES = [(OWNER, _("Owner")), (MEMBER, _("Member"))]

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=ROLES, default=MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "user"], name="one_membership")]
        ordering = ["joined_at"]

    @property
    def is_owner(self):
        return self.role == self.OWNER


def default_invite_expiry():
    return timezone.now() + timedelta(days=14)


class Invite(models.Model):
    """A link that lets someone create a user and join the workspace."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="invites")
    token = models.CharField(max_length=64, default=make_token, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_invite_expiry)
    uses = models.PositiveIntegerField(default=0)
    revoked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_valid(self):
        return not self.revoked and self.expires_at > timezone.now()
