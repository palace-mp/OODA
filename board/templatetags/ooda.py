import re
from datetime import timedelta

from django import template
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import Truncator
from django.utils.translation import gettext as _, ngettext

register = template.Library()

URL = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)\]'\"])")
MENTION = re.compile(r"(?<![\w@&])@([\w-]+(?:\.[\w-]+)*)")
REF = re.compile(r"(?<![\w&/])#(\d+)\b")


@register.filter
def rich(text, base=""):
    """Escape text, then make links, @mentions and #references visible.

    Line breaks are kept by CSS (white-space: pre-wrap) so the text nodes stay
    identical to what was written, which margin comments rely on.
    """
    html = escape(text or "")
    html = URL.sub(r'<a href="\1" rel="noopener" target="_blank">\1</a>', html)
    html = MENTION.sub(r'<span class="mention">@\1</span>', html)
    html = REF.sub(lambda m: f'<a class="ref" href="{base}{m.group(1)}/">#{m.group(1)}</a>', html)
    return mark_safe(html)


@register.filter
def span(delta):
    """A duration in the largest sensible unit: "3 d", "5 h", "12 min"."""
    if delta is None:
        return "—"
    if not isinstance(delta, timedelta):
        return delta
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return _("%(n)s min") % {"n": max(minutes, 0)}
    hours = minutes // 60
    if hours < 48:
        return _("%(n)s h") % {"n": hours}
    days = hours // 24
    if days < 21:
        return _("%(n)s d") % {"n": days}
    return _("%(n)s wk") % {"n": days // 7}


@register.simple_tag(takes_context=True)
def item_url(context, item):
    if context.get("public"):
        return reverse("public_item", args=[context["token"], item.number])
    return item.get_absolute_url()


@register.simple_tag(takes_context=True)
def ref_base(context):
    if context.get("public"):
        return reverse("public_index", args=[context["token"]])
    return reverse("board:index")


@register.simple_tag(takes_context=True)
def query(context, **changes):
    """The current query string with some parameters replaced."""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return "?" + params.urlencode() if params else "?"


@register.filter
def initials(user):
    name = (getattr(user, "name", "") or getattr(user, "email", "?")).split()
    return "".join(part[0] for part in name[:2]).upper()


@register.filter
def first_line(text, length=90):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return Truncator(line).chars(length)


@register.filter
def count_label(n, kind):
    labels = {
        "entry": lambda n: ngettext("%(n)s note", "%(n)s notes", n),
        "comment": lambda n: ngettext("%(n)s comment", "%(n)s comments", n),
    }
    return labels[kind](n) % {"n": n}


@register.filter
def contains(collection, value):
    return value in collection


@register.filter
def options_for(item, field):
    return item.options_for(field)


@register.filter
def has_content(html):
    from board.document import has_content as check

    return check(html)


@register.simple_tag(takes_context=True)
def document_url(context, doc):
    if context.get("public"):
        return reverse("public_document", args=[context["token"], doc.item.number, doc.pk])
    return doc.get_absolute_url()


@register.filter
def tidy(html):
    from board.document import tidy as clean

    return mark_safe(clean(html))
