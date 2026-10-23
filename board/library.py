"""The board's library of lenses: mental models, books and ways of seeing that Orient can borrow.

Boyd's point: the more models you can draw from, the better you orient. A board starts with a
few, and its members add their own.
"""

from django.utils.translation import gettext as _

from .models import Lens


def defaults():
    return [
        (_("The snowmobile"), "John Boyd, Destruction and Creation",
         _("Which pieces from other fields could we combine into a new picture?")),
        (_("Inversion"), "",
         _("What if it is the other way around?")),
        (_("The observational method"), _("Geotechnical engineering"),
         _("What will we measure, and what will we do if the ground behaves differently?")),
        (_("Strategy without design"), "Robert Chia & Robin Holt",
         _("What is already emerging here that nobody planned?")),
        (_("Team of teams"), "Stanley McChrystal",
         _("What intent would let the people closest to this act without asking?")),
        (_("Nested loops"), _("OKR"),
         _("Which loop does this belong to, and how fast is it allowed to turn?")),
        (_("Feelings as signals"), "",
         _("What is the unease telling us about the context, rather than about the people?")),
        (_("The experiencer"), "",
         _("How does this look when we live it rather than observe it: the emotions, the relationships?")),
        (_("Missing voices"), "",
         _("Whose story is this not, and what would they see?")),
    ]


def ensure(workspace):
    """A new board starts with the lenses above."""
    if not Lens.objects.filter(workspace=workspace).exists():
        Lens.objects.bulk_create([
            Lens(workspace=workspace, name=name, source=source, question=question)
            for name, source, question in defaults()
        ])
    return Lens.objects.filter(workspace=workspace)


def suggestions(doc, n=3):
    """A few lenses to try on an Orient document, changing from document to document."""
    lenses = list(ensure(doc.item.workspace))
    if not lenses:
        return []
    start = doc.pk % len(lenses)
    return (lenses[start:] + lenses[:start])[:n]
