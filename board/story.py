"""An item's story: the loops told as episodes, assembled from what was already written.

Strategy told as a soap opera rather than a plan: what we saw, what we believed, what we
tried, what happened, and when we changed our minds. Nobody writes it; it is read.
"""

from .document import (
    blocks, experiment_labels, experiments, hypotheses, plain_text, results_heading, section_html, tidy,
)
from .models import ACT, DECIDE, OBSERVE, ORIENT, Comment, Verdict


def first_lines(html, n=2):
    return [t.strip() for t in blocks(tidy(html), headings=False) if t.strip()][:n]


def episodes(item):
    docs = {(d.phase, d.cycle): d for d in item.documents.all()}
    verdicts = {}
    for v in Verdict.objects.filter(item=item):
        verdicts.setdefault(v.cycle, []).append(v)
    reorients = {}
    for s in item.spans.exclude(reason=""):
        reorients.setdefault(s.cycle, []).append(s)
    out = []
    for cycle in range(1, item.cycle + 1):
        observe, orient, decide, act = (docs.get((p, cycle)) for p in (OBSERVE, ORIENT, DECIDE, ACT))
        if not any((observe, orient, decide, act)):
            continue
        tried = []
        if decide:
            label = experiment_labels()[0]
            for name, fields in experiments(decide.body).items():
                text = fields.get(label, ("", ""))[1]
                if text:
                    tried.append(text)
        dates = [d.created_at for d in (observe, orient, decide, act) if d]
        out.append({
            "cycle": cycle,
            "start": min(dates),
            "saw": first_lines(observe.body) if observe else [],
            "believed": [line for line in plain_text(hypotheses(orient.body)).splitlines() if line.strip()][:3]
            if orient else [],
            "tried": tried,
            "happened": [line for line in plain_text(section_html(act.body, results_heading())).splitlines()
                         if line.strip()] if act else [],
            "verdicts": sorted(verdicts.get(cycle, []), key=lambda v: v.experiment),
            "reorients": reorients.get(cycle, []),
            "readings": list(Comment.objects.filter(item=item, document__cycle=cycle, kind=Comment.READING,
                                                    status=Comment.OPEN).select_related("author")),
            "current": cycle == item.cycle and not item.is_done,
        })
    return out
