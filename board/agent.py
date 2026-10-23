"""An AI agent that takes part like any member: it reads documents sent for
review, comments on passages, suggests changes and answers in its threads.

It never edits a document itself; people accept or reject what it proposes.
With Anthropic credentials it asks Claude. Without them it falls back to a few
plain rules, so the flow can be tried out offline.
"""

import json
import os
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from . import notify
from .document import GUIDES, experiment_labels, plain_text
from .models import Comment, Document, Reply

MODEL = "claude-opus-5-5"

# What a good document looks like in each phase, from the reviewer's side.
CRITERIA = {
    "observe": (
        "An observation states what was seen, as it is: what happened, where and when, how often, "
        "and how we know (a source, a count, who saw it). It does not explain, judge or propose. "
        "Point out conclusions, proposals, vague quantities and missing sources."
    ),
    "orient": (
        "Orientation fills in knowledge around the observation: internal data (our own numbers, logs, "
        "sales, support), external data (research, benchmarks, the market), hard numbers and soft "
        "information (interviews, impressions), history and projections. Its output is used in the "
        "Decide phase to design experiments, so it must be backed up and comparable. Ask for the "
        "source behind claims, for numbers where there are only impressions, for a comparison "
        "(baseline, earlier period, peers) where a number stands alone, for the kind of knowledge "
        "that is missing, and for hypotheses concrete enough to test."
    ),
    "decide": (
        "Decide designs a set of experiments that validate the assumptions from Orient (its hypotheses "
        "are the input). Each experiment tests one assumption and states what we do, how we measure, "
        "the result that would confirm it and the result that would refute it, and its time and cost. "
        "Point out assumptions from Orient that no experiment tests, experiments that test several "
        "things at once, outcomes that cannot be measured, thresholds that are not decided in advance, "
        "and experiments too big or slow to run soon."
    ),
    "act": (
        "An action report says what was done and what came of it, concretely enough that the next "
        "observation can start from it. Point out missing results, dates and what to observe next."
    ),
}


@dataclass
class Note:
    kind: str  # "comment" or "suggestion"
    quote: str
    comment: str
    replacement: str = ""


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


def anchor(html, quote):
    """(quote, prefix) if `quote` occurs within one paragraph, else None."""
    quote = (quote or "").strip()
    if not quote:
        return None
    for text in blocks(html):
        at = text.find(quote)
        if at >= 0:
            return quote, text[max(0, at - 60):at]
    return None


# --- Reviewing --------------------------------------------------------------


def has_credentials():
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or (Path.home() / ".config" / "anthropic").exists()
    )


def claude_review(doc, input_doc):
    import anthropic

    schema = {
        "type": "object",
        "properties": {
            "notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["comment", "suggestion"]},
                        "quote": {"type": "string"},
                        "replacement": {"type": "string"},
                        "comment": {"type": "string"},
                    },
                    "required": ["kind", "quote", "replacement", "comment"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["notes"],
        "additionalProperties": False,
    }
    system = (
        "You review a shared document in a case-handling tool built on the OODA loop "
        "(Observe, Orient, Decide, Act). You take part like a careful colleague: you comment on "
        "passages and suggest concrete rewordings; people decide what to accept.\n\n"
        f"This document belongs to the {doc.get_phase_display()} phase. {CRITERIA[doc.phase]}\n\n"
        "Return at most four notes, the ones that matter most; none if the document is good. Each note "
        "quotes an exact passage copied character for character from a single paragraph. A suggestion "
        "gives the text to put in place of the quote; a comment asks a question or points something out "
        "and leaves replacement empty. Keep comments to one or two sentences, in the language the "
        "document is written in. Leave the template's question headings alone."
    )
    parts = [f"Title: {doc.item.title}"]
    if input_doc:
        parts.append(f"Input, the {input_doc.get_phase_display()} document before this one:\n{plain_text(input_doc.body)}")
    parts.append("The document to review, one paragraph per line:\n" + "\n".join(blocks(doc.body)))

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
    )
    if response.stop_reason == "refusal":
        return []
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text) if text else {"notes": []}
    return [Note(n["kind"], n["quote"], n["comment"], n["replacement"]) for n in data["notes"]]


JUDGEMENT = re.compile(
    r"\b(should|must|need to|needs to|have to|has to|we fix|solve|because|obviously|probably|I think|"
    r"bör|måste|borde|behöver|eftersom|troligen|uppenbarligen|jag tror)\b", re.I)
VAGUE = re.compile(r"\b(often|many|some|several|a lot|lots of|always|never|ofta|många|några|flera|alltid|aldrig)\b", re.I)


COMPARISON = re.compile(
    r"\b(compared|than|vs\.?|versus|baseline|last (year|month|week|quarter)|average|median|benchmark|"
    r"jämfört|än|snitt|genomsnitt|förra (året|månaden|veckan|kvartalet)|i fjol)\b", re.I)
SOURCE = re.compile(r"\b(source|according to|data from|report|survey|study|enligt|källa|rapport|undersökning|studie)\b", re.I)


def rule_review(doc, input_doc):
    """A few fixed checks, used when Claude isn't available."""
    notes = []
    texts = [t for t in blocks(doc.body, headings=False) if t.strip()]
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if doc.phase == "observe" and JUDGEMENT.search(sentence):
                notes.append(Note(
                    "suggestion", sentence.strip(),
                    "This reads as a conclusion or a proposal rather than an observation. "
                    "Could it move to Orient or Decide, keeping only what was actually seen here?",
                ))
            elif VAGUE.search(sentence):
                word = VAGUE.search(sentence).group(0)
                notes.append(Note("comment", word, f"How {word.lower()} exactly? A number or a period makes this easier to act on."))
    first = next((t for t in texts if not t.endswith("?")), None)
    numbers = re.search(r"\d", " ".join(texts))
    if doc.phase == "observe" and first and not numbers:
        notes.append(Note("comment", first, "Is there a number, a date or a source to go with this? How do we know?"))
    if doc.phase == "orient" and first:
        notes += orient_rules(doc, texts, first, numbers)
    if doc.phase == "decide":
        notes += decide_rules(doc)
    unique = {}
    for note in notes:
        unique.setdefault(note.quote, note)
    return list(unique.values())[:4]


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


def decide_rules(doc):
    hypothesis, what, measure, holds, fails, _cost = experiment_labels()
    blocks_ = experiments(doc.body)
    started = {h: fields for h, fields in blocks_.items() if any(v for _l, v in fields.values())}
    if not started:
        heading = next(iter(blocks_), None)
        return [Note("comment", heading, (
            "Which assumption from Orient do we test first? One small experiment is enough to start."))] if heading else []
    notes = []
    for fields in started.values():
        for label, question in (
            (hypothesis, "Which assumption from Orient does this experiment test? One per experiment keeps the answer clear."),
            (measure, "How will we know? Name the number or signal we'll read off, and where it comes from."),
            (holds, "What result confirms the assumption? Decide it before the experiment starts."),
            (fails, "What result would show the assumption is wrong, so we stop or change course?"),
        ):
            line, value = fields.get(label, ("", ""))
            if line and not value:
                notes.append(Note("comment", line, question))
        line, value = fields.get(holds, ("", ""))
        if value and not re.search(r"\d", value):
            notes.append(Note("comment", line, "Can this threshold be a number, so nobody has to argue about it afterwards?"))
    return notes


def orient_rules(doc, texts, first, numbers):
    notes = []
    joined = " ".join(texts)
    if not numbers:
        notes.append(Note("comment", first, (
            "Can this be backed up? A number from our own data, an outside source or a benchmark "
            "would make it something Decide can build an experiment on.")))
    else:
        with_number = next(s for t in texts for s in re.split(r"(?<=[.!?])\s+", t) if re.search(r"\d", s))
        if not COMPARISON.search(joined):
            notes.append(Note("comment", with_number.strip(), (
                "Compared to what? An earlier period, a baseline or peers makes this number comparable.")))
    if "href=" not in (doc.body or "") and not SOURCE.search(joined):
        notes.append(Note("comment", first, "Where does this come from? A link or a named source lets others check it."))
    hypotheses, written = section(doc.body, ("hypotes", "hypothes"))
    if hypotheses and not written:
        notes.append(Note("comment", hypotheses, (
            "Which explanations could Decide design an experiment around? Even one testable hypothesis helps.")))
    return notes


def review(doc, use_claude):
    input_doc = doc.previous()
    notes = claude_review(doc, input_doc) if use_claude else rule_review(doc, input_doc)
    return notes


def claude_reply(thread, doc):
    import anthropic

    lines = [f"Passage: {thread.quote}"] if thread.quote else []
    if thread.is_suggestion:
        lines.append(f"Suggested instead: {thread.replacement}")
    lines.append(f"{thread.author.name}: {thread.body}")
    lines += [f"{r.author.name if r.author else 'someone'}: {r.body}" for r in thread.replies.all()]
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system=(
            "You take part in a comment thread on a shared document in a tool built on the OODA loop. "
            f"The document is in the {doc.get_phase_display()} phase: {GUIDES[doc.phase]} "
            "Answer the latest message briefly, in one to three sentences, in the language of the thread. "
            "Be concrete; agree or concede when the other person has a point. Plain text only."
        ),
        messages=[{"role": "user", "content": "Document:\n" + plain_text(doc.body) + "\n\nThread:\n" + "\n".join(lines)}],
    )
    if response.stop_reason == "refusal":
        return ""
    return next((b.text for b in response.content if b.type == "text"), "").strip()


def rule_reply(thread, doc):
    return "Thanks, noted. The people assigned decide what to keep; I'll look again if the text changes."


# --- One round --------------------------------------------------------------


class Agent:
    def __init__(self, user, membership, use_claude, log=print):
        self.user = user
        self.membership = membership
        self.use_claude = use_claude
        self.log = log

    def run_once(self):
        workspace = self.membership.workspace
        for doc in Document.objects.filter(item__workspace=workspace, status=Document.REVIEW,
                                           item__merged_into=None).select_related("item"):
            if doc.is_current and not self.reviewed(doc):
                self.review(doc)
        for thread in self.threads_waiting():
            self.answer(thread)

    def reviewed(self, doc):
        mine = doc.comments.filter(author=self.user)
        if doc.submitted_at:
            mine = mine.filter(created_at__gte=doc.submitted_at)
        return mine.exists()

    def review(self, doc):
        item = doc.item
        self.log(f"Reviewing #{item.number} {item.title} ({doc.get_phase_display()})")
        notes = review(doc, self.use_claude)
        made = 0
        for note in notes:
            found = anchor(doc.body, note.quote)
            if not found:
                continue
            quote, prefix = found
            suggestion = note.kind == "suggestion"
            c = Comment.objects.create(
                item=item, document=doc, author=self.user, quote=quote, prefix=prefix,
                kind=Comment.SUGGESTION if suggestion else Comment.COMMENT,
                replacement=note.replacement if suggestion else "", body=note.comment.strip(),
            )
            if suggestion:
                editors = list(item.assignees.all()) or [
                    m.user for m in item.workspace.memberships.filter(role="owner").select_related("user")]
                notify.suggested(item, editors, self.user, c)
            made += 1
        if not made:
            # Leave a trace so the same version isn't reviewed again.
            Comment.objects.create(item=item, document=doc, author=self.user, status=Comment.RESOLVED,
                                   body="Read it; nothing to add.")
        self.log(f"  {made} comment(s) and suggestion(s)")

    def threads_waiting(self):
        """Open threads it started or is @mentioned in, where someone else spoke last."""
        handle = re.compile(rf"@{re.escape(self.user.handle)}\b", re.I)
        workspace = self.membership.workspace
        candidates = Comment.objects.filter(
            item__workspace=workspace, status=Comment.OPEN, document__isnull=False,
        ).select_related("item", "document", "author")
        for c in candidates:
            if not c.document.is_current:
                continue
            replies = list(c.replies.select_related("author"))
            involved = c.author_id == self.user.pk or handle.search(c.body) or any(handle.search(r.body) for r in replies)
            if not involved:
                continue
            last_author = replies[-1].author_id if replies else c.author_id
            if last_author != self.user.pk:
                yield c

    def answer(self, thread):
        text = claude_reply(thread, thread.document) if self.use_claude else rule_reply(thread, thread.document)
        if not text:
            return
        Reply.objects.create(comment=thread, body=text, author=self.user)
        people = {thread.author, *[r.author for r in thread.replies.select_related("author")]}
        notify.replied(thread.item, people, self.user, thread, text)
        self.log(f"Answered a thread on #{thread.item.number}")
