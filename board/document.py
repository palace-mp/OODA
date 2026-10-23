"""The item document: a small, safe subset of HTML.

Browsers produce the HTML (contenteditable); everything is cleaned here before
it is stored, so templates can print it as is.
"""

import re
from html import escape, unescape

import nh3
from django.utils.translation import gettext as _, gettext_lazy

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


def has_content(html):
    """Is anything written apart from the template's questions?"""
    return bool(plain_text(re.sub(r"<h[23]>.*?</h[23]>", "", tidy(html))).strip())


def plain_text(html):
    text = re.sub(r"<br\s*/?>|</(p|h2|h3|li|blockquote)>", "\n", html or "")
    return unescape(re.sub(r"<[^>]+>", "", text)).strip()


def from_entry(entry):
    """An inbox note as a paragraph, labelled with its kind and who wrote it."""
    who = f" <em>— {escape(entry.author.name)}</em>" if entry.author else ""
    text = escape(entry.body).replace("\n", "<br>")
    return f"<p><strong>{escape(str(entry.get_kind_display()))}:</strong> {text}{who}</p>"


# What each phase is about, shown above its document.
GUIDES = {
    "observe": gettext_lazy("Describe what you see, as it is — not what should be done about it."),
    "orient": gettext_lazy(
        "Back the observation up with knowledge: internal and external data, hard numbers and soft "
        "signals, history and projections. Make it comparable, and end with hypotheses Decide can "
        "design experiments around."
    ),
    "decide": gettext_lazy(
        "Design experiments that validate the assumptions from Orient. Each one tests one assumption, "
        "with a result we can measure and decide on. The output is a set of experiments."
    ),
    "act": gettext_lazy("What was done, and what came of it? This is what the next observation starts from."),
}


def template(phase, previous=None):
    """A new document starts with the questions its phase asks. `previous` is the document before it."""
    questions = {
        "observe": [_("What happened?"), _("Where and when?"), _("How often, how many?"), _("How do we know?")],
        "orient": [
            _("What do our own data say? Numbers, logs, sales, support"),
            _("What do outside sources say? Research, benchmarks, the market"),
            _("What do people say? Interviews, conversations, impressions"),
            _("Has this happened before, and where is it heading?"),
            _("Compared to what? Baseline, earlier periods, peers"),
            _("Hypotheses: what could explain it, and how could we test it?"),
            _("What don't we know yet?"),
        ],
        "act": [_("What we did"), _("What happened"), _("What to observe next")],
    }.get(phase, [])
    if phase == "decide":
        return decide_template(previous)
    return "".join(f"<h3>{escape(q)}</h3><p><br></p>" for q in questions)


def experiment_labels():
    return [_("Hypothesis"), _("What we do"), _("How we measure"), _("It holds if"), _("It fails if"),
            _("Time and cost")]


def experiment(n):
    items = "".join(f"<li><strong>{escape(label)}:</strong> </li>" for label in experiment_labels())
    return f"<h3>{escape(_('Experiment %(n)s') % {'n': n})}</h3><ul>{items}</ul>"


def decide_template(orient=None):
    """Decide starts from the hypotheses Orient ended with, and room for experiments to test them."""
    assumptions = hypotheses(orient.body) if orient else ""
    return (
        f"<h3>{escape(_('Assumptions from Orient to validate'))}</h3>{assumptions or '<p><br></p>'}"
        + experiment(1) + experiment(2)
        + f"<h3>{escape(_('Which first, and why?'))}</h3><p><br></p>"
    )


def hypotheses(html):
    """What was written under Orient's hypotheses heading, as HTML."""
    match = re.search(r"<h[23]>\s*(Hypothes|Hypotes)[^<]*</h[23]>(.*?)(?=<h[23]>|$)", html or "", re.S | re.I)
    return tidy(match.group(2)) if match else ""


def tidy(html):
    """For reading afterwards: drop empty paragraphs and questions nobody answered."""
    html = re.sub(r"<p>(\s|<br>)*</p>", "", html or "")
    html = re.sub(r"<li>\s*(<strong>[^<]*</strong>)?\s*(<br>)?\s*</li>", "", html)  # labels left blank
    html = re.sub(r"<(ul|ol)>\s*</\1>", "", html)
    previous = None
    while previous != html:
        previous = html
        html = re.sub(r"<h([23])>[^<]*</h\1>(?=\s*(<h[23]>|$))", "", html)
    return html
