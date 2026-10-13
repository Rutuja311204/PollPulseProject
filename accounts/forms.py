"""
Account forms built on Django's authentication system.

Passwords are hashed by Django (PBKDF2-SHA256 with a random salt) and stored
in SQLite's auth_user table. Plain-text passwords are never stored anywhere.
"""
from django import forms
from django.contrib.auth.forms import (AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm,
                                       UserCreationForm)
from django.contrib.auth.models import User


def _style(form):
    for field in form.fields.values():
        field.widget.attrs.setdefault("class", "form-control")


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(max_length=60, label="Full name")
    email = forms.EmailField(label="Email address", help_text="Used for password reset.")

    class Meta:
        model = User
        fields = ("first_name", "username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields["username"].help_text = "Letters, digits and @/./+/-/_ only. Collaborators find you by this name."

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields["username"].widget.attrs["autocomplete"] = "username"
        self.fields["password"].widget.attrs["autocomplete"] = "current-password"


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "email")
        labels = {"first_name": "Full name", "email": "Email address"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        _style(self)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class StyledPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
