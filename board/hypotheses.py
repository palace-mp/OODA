"""The board's hypotheses: every experiment planned in Decide, and how it came out."""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from .document import experiment_labels, experiments, opportunities, opportunity_labels, risk_labels, risks
from .models import ACT, DECIDE, Document, Verdict

PLANNED, TESTING, WATCHING, NOT_CONCLUDED = "planned", "testing", "watching", "open"
STATUSES = [
    (PLANNED, _("Planned")),
    (TESTING, _("Testing")),
    (WATCHING, _("Watching")),
    (Verdict.HELD, _("Held")),
    (Verdict.FAILED, _("Failed")),
    (Verdict.UNCLEAR, _("Unclear")),
    (NOT_CONCLUDED, _("Not concluded")),
]


@dataclass
class Hypothesis:
    item: object
    cycle: int
    experiment: str
    text: str
    then: str
    status: str
    kind: str = "experiment"
    signal: str = ""

    @property
    def status_label(self):
        if self.kind != "experiment" and self.status in dict(Verdict.RISK_OUTCOMES):
            return dict(Verdict.RISK_OUTCOMES)[self.status]
        return dict(STATUSES)[self.status]


def from_document(doc, verdicts):
    """The experiments in one Decide document that someone has started to fill in."""
    hypothesis, *_rest = experiment_labels()
    then = experiment_labels()[5]
    item = doc.item
    out = []
    for name, fields in experiments(doc.body).items():
        if not any(value for _line, value in fields.values()):
            continue
        verdict = verdicts.get((item.pk, doc.cycle, name))
        if verdict:
            status = verdict
        elif doc.cycle == item.cycle and item.phase == DECIDE and not item.is_done:
            status = PLANNED
        elif doc.cycle == item.cycle and item.phase == ACT and not item.is_done:
            status = TESTING
        else:
            status = NOT_CONCLUDED
        out.append(Hypothesis(item, doc.cycle, name, fields.get(hypothesis, ("", ""))[1] or name,
                              fields.get(then, ("", ""))[1], status))
    live = doc.cycle == item.cycle and item.phase in (DECIDE, ACT) and not item.is_done
    for kind, labels, found in (("risk", risk_labels(), risks(doc.body)),
                                ("opportunity", opportunity_labels(), opportunities(doc.body))):
        what, signal, if_seen = labels
        for name, fields in found.items():
            text = fields.get(what, ("", ""))[1]
            if not text:
                continue
            status = verdicts.get((item.pk, doc.cycle, name)) or (WATCHING if live else NOT_CONCLUDED)
            out.append(Hypothesis(item, doc.cycle, name, text, fields.get(if_seen, ("", ""))[1], status,
                                  kind=kind, signal=fields.get(signal, ("", ""))[1]))
    return out


def for_items(items):
    items = list(items)
    verdicts = {
        (v.item_id, v.cycle, v.experiment): v.outcome
        for v in Verdict.objects.filter(item__in=items)
    }
    docs = Document.objects.filter(item__in=items, phase=DECIDE).select_related("item").order_by("item__number", "cycle")
    return [h for doc in docs for h in from_document(doc, verdicts)]
