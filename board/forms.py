from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Entry, Field, FieldOption, Item, Link


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["title", "deadline", "is_priority", "assignees", "parent"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": _("What needs doing?"), "autofocus": True}),
            "deadline": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "assignees": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace
        if self.instance.pk:
            del self.fields["title"]  # edited in place, together with the document
        self.fields["assignees"].queryset = workspace_users(workspace)
        parents = Item.objects.filter(workspace=workspace, is_done=False, merged_into=None)
        if self.instance.pk:
            parents = parents.exclude(pk=self.instance.pk).exclude(parent=self.instance)
        self.fields["parent"].queryset = parents
        self.fields["parent"].label_from_instance = lambda i: f"#{i.number} {i.title}"
        self.fields["parent"].empty_label = "—"
        self.taxonomy = list(Field.objects.filter(workspace=workspace).prefetch_related("choices"))
        for field in self.taxonomy:
            initial = [o.pk for o in self.instance.options_for(field)] if self.instance.pk else []
            choices = [(o.pk, o.label) for o in field.choices.all()]
            if field.allow_many:
                self.fields[f"field_{field.pk}"] = forms.MultipleChoiceField(
                    label=field.name, choices=choices, required=False, initial=initial,
                    widget=forms.CheckboxSelectMultiple,
                )
            else:
                self.fields[f"field_{field.pk}"] = forms.ChoiceField(
                    label=field.name, choices=[("", "—")] + choices, required=False,
                    initial=initial[0] if initial else "",
                )

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        node = parent
        while node is not None and self.instance.pk:
            if node.pk == self.instance.pk:
                raise forms.ValidationError(_("An item can't sit under one of its own sub-items."))
            node = node.parent
        return parent

    def taxonomy_fields(self):
        return [self[f"field_{f.pk}"] for f in self.taxonomy]

    def save(self, commit=True):
        item = super().save(commit=commit)
        chosen = []
        for field in self.taxonomy:
            value = self.cleaned_data.get(f"field_{field.pk}")
            chosen += [int(v) for v in (value if isinstance(value, list) else [value]) if v]
        other = item.options.exclude(field__in=self.taxonomy)
        item.options.set(list(other) + list(FieldOption.objects.filter(pk__in=chosen, field__in=self.taxonomy)))
        return item


def workspace_users(workspace):
    from accounts.models import User

    return User.objects.filter(memberships__workspace=workspace, is_active=True).order_by("name")


class EntryForm(forms.ModelForm):
    class Meta:
        model = Entry
        fields = ["kind", "signal", "body"]
        widgets = {
            "kind": forms.RadioSelect,
            "signal": forms.RadioSelect,
            "body": forms.Textarea(attrs={"rows": 3, "placeholder": _("What did you see, think, decide or do?")}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = Entry.WRITABLE_KINDS
        self.fields["signal"].required = False
        self.fields["signal"].initial = Entry.SEEN

    def clean_signal(self):
        return self.cleaned_data.get("signal") or Entry.SEEN


class LinkForm(forms.ModelForm):
    class Meta:
        model = Link
        fields = ["url", "label"]
        widgets = {
            "url": forms.URLInput(attrs={"placeholder": "https://"}),
            "label": forms.TextInput(attrs={"placeholder": _("Label (optional)")}),
        }


class FieldForm(forms.ModelForm):
    options = forms.CharField(
        label=_("Options"), required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("One per line."),
    )

    class Meta:
        model = Field
        fields = ["name", "allow_many"]
