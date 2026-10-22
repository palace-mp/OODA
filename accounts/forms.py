from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from .models import User, Workspace


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label=_("E-mail"), widget=forms.EmailInput(attrs={"autofocus": True}))


class SignupForm(forms.Form):
    name = forms.CharField(label=_("Your name"), max_length=80)
    email = forms.EmailField(label=_("E-mail"))
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("There is already a user with that e-mail. Sign in instead."))
        return email

    def clean(self):
        data = super().clean()
        if data.get("password"):
            user = User(email=data.get("email", ""), name=data.get("name", ""))
            password_validation.validate_password(data["password"], user)
        return data

    def save(self):
        d = self.cleaned_data
        return User.objects.create_user(d["email"], d["password"], name=d["name"])


class NewWorkspaceSignupForm(SignupForm):
    workspace = forms.CharField(label=_("Name of the board"), max_length=80, initial="OODA")
    field_order = ["workspace", "name", "email", "password"]


class WorkspaceForm(forms.ModelForm):
    class Meta:
        model = Workspace
        fields = ["name"]


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["name", "email"]
