"""E-mail notifications. Kept small: assigned, mentioned, unblocked, deadline."""

import re

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

MENTION = re.compile(r"(?<![\w@])@([\w-]+(?:\.[\w-]+)*)", re.UNICODE)


def mentioned_users(text, workspace):
    handles = {m.lower() for m in MENTION.findall(text or "")}
    if not handles:
        return []
    members = [m.user for m in workspace.memberships.select_related("user")]
    return [u for u in members if u.handle in handles]


def _send(users, subject, template, context, exclude=None):
    # Agents read the board directly; they get no e-mail.
    for user in {u for u in users if u and u != exclude and u.is_active and not u.is_agent}:
        body = render_to_string(template, {**context, "user": user, "site_url": settings.SITE_URL})
        send_mail(subject, body, None, [user.email], fail_silently=True)


def assigned(item, users, by):
    _send(users, _("#%(n)s assigned to you: %(t)s") % {"n": item.number, "t": item.title},
          "email/assigned.txt", {"item": item, "by": by}, exclude=by)


def mentioned(item, users, by, text):
    _send(users, _("%(who)s mentioned you on #%(n)s") % {"who": by, "n": item.number},
          "email/mentioned.txt", {"item": item, "by": by, "text": text}, exclude=by)


def unblocked(item, finished, by):
    """`finished` was the last thing `item` waited for."""
    _send(item.assignees.all(), _("#%(n)s can start: %(t)s") % {"n": item.number, "t": item.title},
          "email/unblocked.txt", {"item": item, "finished": finished}, exclude=by)


def deadline(item):
    _send(item.assignees.all(), _("Due soon: #%(n)s %(t)s") % {"n": item.number, "t": item.title},
          "email/deadline.txt", {"item": item})


def suggested(item, users, by, suggestion):
    _send(users, _("%(who)s suggested a change to #%(n)s") % {"who": by, "n": item.number},
          "email/suggested.txt", {"item": item, "by": by, "c": suggestion}, exclude=by)


def replied(item, users, by, thread, text):
    _send(users, _("%(who)s replied on #%(n)s") % {"who": by, "n": item.number},
          "email/replied.txt", {"item": item, "by": by, "c": thread, "text": text}, exclude=by)


def decided(item, suggestion, by):
    _send([suggestion.author], _("Your suggestion on #%(n)s was %(status)s") % {
        "n": item.number, "status": suggestion.get_status_display().lower()},
        "email/decided.txt", {"item": item, "by": by, "c": suggestion}, exclude=by)


def review_requested(item, doc, users, by):
    _send(users, _("Review asked for: #%(n)s %(t)s (%(phase)s)") % {
        "n": item.number, "t": item.title, "phase": doc.get_phase_display()},
        "email/review.txt", {"item": item, "doc": doc, "by": by}, exclude=by)
