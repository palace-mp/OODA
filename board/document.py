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
    "observe": gettext_lazy(
        "Describe what you see, as it is — not what should be done about it. Look hardest for what "
        "goes against the picture we have: noticing what matters beats collecting more."
    ),
    "orient": gettext_lazy(
        "Back the observation up with knowledge: internal and external data, hard numbers and soft "
        "signals, history and projections. Take the old picture apart, try other ways of seeing it, "
        "and build a new one. End with hypotheses Decide can design experiments around."
    ),
    "decide": gettext_lazy(
        "Design experiments that validate the assumptions from Orient. Each one tests one assumption, "
        "with a result we can measure and decide on. The output is a set of experiments."
    ),
    "act": gettext_lazy(
        "Run the experiments and keep a journal of what happens. Write the results as observations — "
        "what was seen and measured, against the thresholds set in Decide — so the next Observe can "
        "start from them."
    ),
}


def template(phase, previous=None):
    """A new document starts with the questions its phase asks. `previous` is the document before it."""
    questions = {
        "observe": [
            _("What happened?"), _("Where and when?"), _("How often, how many?"), _("How do we know?"),
            felt_heading(),
            surprise_heading(),
        ],
        "orient": [
            _("What do our own data say? Numbers, logs, sales, support"),
            _("What do outside sources say? Research, benchmarks, the market"),
            _("What do people say? Interviews, conversations, impressions"),
            _("Has this happened before, and where is it heading?"),
            _("Compared to what? Baseline, earlier periods, peers"),
            assumptions_heading(),
            other_eyes_heading(),
            inversion_heading(),
            missing_voices_heading(),
            new_picture_heading(),
            _("Hypotheses: what could explain it, and how could we test it?"),
            _("What don't we know yet?"),
        ],
    }.get(phase, [])
    if phase == "decide":
        return decide_template(previous)
    if phase == "act":
        return act_template(previous)
    html = "".join(f"<h3>{escape(q)}</h3><p><br></p>" for q in questions)
    if phase == "observe" and previous is not None:
        # A new loop starts from what the last one's experiments showed.
        found = section_html(previous.body, results_heading())
        if found:
            html = html.replace("<p><br></p>", found, 1)
    return html


def felt_heading():
    return _("What does it feel like? Unease, gut feeling, what we can't put into words yet")


def inversion_heading():
    return _("What if it is the other way around?")


def missing_voices_heading():
    return _("Whose perspective is missing? Who is affected but not heard?")


def surprise_heading():
    return _("What surprised us, or goes against our current picture?")


def assumptions_heading():
    return _("What do we assume today, and which parts still hold?")


def other_eyes_heading():
    return _("How would someone from a completely different field explain this?")


def new_picture_heading():
    return _("What new picture do the pieces form?")


def observations_list(entries):
    """Observations noted on an item while it was elsewhere in the loop, as a list."""
    lines = []
    for e in entries:
        who = f" <em>— {escape(e.author.name)}</em>" if e.author else ""
        how = f"<strong>{escape(str(e.get_signal_display()))}:</strong> " if e.signal != e.SEEN else ""
        lines.append(f"<li>{how}{escape(e.body).replace(chr(10), '<br>')}{who}</li>")
    return f"<ul>{''.join(lines)}</ul>" if lines else ""


def add_to_first_section(html, extra):
    """Put `extra` at the end of what is written under the first heading."""
    match = re.search(r"</h[23]>(.*?)(?=<h[23]>|$)", html, re.S)
    if not match or not extra:
        return html
    content = match.group(1)
    if not tidy(content):
        content = extra
    else:
        content = re.sub(r"</ul>\s*$", "", content) + re.sub(r"^<ul>", "", extra) if content.rstrip().endswith("</ul>") \
            else content + extra
    return html[:match.start(1)] + content + html[match.end(1):]


def results_heading():
    return _("Results as observations")


def act_template(decide=None):
    """A journal for running the experiments, and one result line per experiment planned in Decide."""
    lines = []
    hypothesis = experiment_labels()[0]
    for name, fields in experiments(decide.body if decide else "").items():
        tested = fields.get(hypothesis, ("", ""))[1].rstrip(" .")
        if any(value for _line, value in fields.values()):
            label = f"{name} — {tested}" if tested else name
            lines.append(f"<li><strong>{escape(label)}:</strong> </li>")
    results = f"<ul>{''.join(lines)}</ul>" if lines else "<p><br></p>"
    return (
        f"<h3>{escape(_('Journal'))}</h3><p><br></p>"
        f"<h3>{escape(results_heading())}</h3>{results}"
    )


def section_html(html, starts):
    """The HTML written under the heading that starts with `starts`, tidied."""
    pattern = r"<h[23]>\s*" + re.escape(escape(starts)) + r"[^<]*</h[23]>(.*?)(?=<h[23]>|$)"
    match = re.search(pattern, html or "", re.S | re.I)
    return tidy(match.group(1)) if match else ""


def experiment_labels():
    return [_("Hypothesis"), _("What we do"), _("How we measure"), _("It holds if"), _("It fails if"),
            _("If it fails, we will"), _("Time and cost")]


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
    html = re.sub(r"<li>\s*(<strong>[^<]*:</strong>)?\s*(<br>)?\s*</li>", "", html)  # labels left blank
    html = re.sub(r"<(ul|ol)>\s*</\1>", "", html)
    previous = None
    while previous != html:
        previous = html
        html = re.sub(r"<h([23])>[^<]*</h\1>(?=\s*(<h[23]>|$))", "", html)
    return html


# --- Reading documents as text ---------------------------------------------


def tagged_blocks(html):
    """(tag, text) for each paragraph-like element, the text as the browser's textContent sees it."""
    out = []
    for tag, inner in re.findall(r"<(p|h2|h3|li|blockquote)>(.*?)</\1>", html or "", re.S):
        if re.search(r"<(p|h2|h3|li|blockquote)>", inner):
            continue  # holds other blocks; those are listed on their own
        out.append((tag, unescape(re.sub(r"<[^>]+>", "", inner))))
    return out


def blocks(html, headings=True):
    return [text for tag, text in tagged_blocks(html) if headings or tag not in ("h2", "h3")]


def section(html, starts):
    """The heading starting with one of `starts`, and the text written under it."""
    heading, text = None, []
    for tag, value in tagged_blocks(html):
        if tag in ("h2", "h3"):
            if heading:
                break
            if value.lower().startswith(starts):
                heading = value
        elif heading:
            text.append(value)
    return heading, " ".join(text).strip()


def experiments(html):
    """{heading: {label: (line, value)}} for each experiment block in a Decide document."""
    found, current = {}, None
    labels = experiment_labels()
    for tag, text in tagged_blocks(html):
        if tag in ("h2", "h3"):
            current = text if text.lower().startswith("experiment") else None
            if current:
                found[current] = {}
        elif current is not None and tag == "li":
            for label in labels:
                if text.startswith(label + ":"):
                    found[current][label] = (text.strip(), text[len(label) + 1:].strip())
    return found
