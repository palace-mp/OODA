"""The item document: a small, safe subset of HTML.

Browsers produce the HTML (contenteditable); everything is cleaned here before
it is stored, so templates can print it as is.
"""

import re
from html import escape, unescape

import nh3

TAGS = {"p", "br", "h2", "h3", "ul", "ol", "li", "strong", "b", "em", "i", "a", "blockquote", "code"}
ATTRIBUTES = {"a": {"href"}}
URL_SCHEMES = {"http", "https", "mailto"}


def sanitize(html):
    html = nh3.clean(
        html or "", tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer",
    )
    # Browsers leave loose text and <div>s behind; make sure text sits in paragraphs.
    html = re.sub(r"(<p>\s*(<br>)?\s*</p>\s*)+$", "", html.strip())
    if html and not html.lstrip().startswith("<"):
        html = f"<p>{html}</p>"
    return html


def from_text(text):
    """Plain text (as written before documents existed) to paragraphs."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def heading(text):
    return f"<h3>{escape(text)}</h3>"


def plain_text(html):
    text = re.sub(r"<br\s*/?>|</(p|h2|h3|li|blockquote)>", "\n", html or "")
    return unescape(re.sub(r"<[^>]+>", "", text)).strip()


def from_entry(entry):
    """An inbox note as a paragraph, labelled with its kind and who wrote it."""
    who = f" <em>— {escape(entry.author.name)}</em>" if entry.author else ""
    text = escape(entry.body).replace("\n", "<br>")
    return f"<p><strong>{escape(str(entry.get_kind_display()))}:</strong> {text}{who}</p>"
