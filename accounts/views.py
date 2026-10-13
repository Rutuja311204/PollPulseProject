"""
Registration, login (rate limited), logout, profile and password reset.

Authentication = proving who you are (username + password).
Authorization  = what you may do (survey roles, admin role) — enforced in
                 surveys/permissions.py and core/roles.py.
"""
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from core import audit, ratelimit
from core.exceptions import DatabaseUnavailable, RateLimited
from core.platform import get_settings as platform_settings
from surveys.survey_service import SurveyService

from .forms import (LoginForm, ProfileForm, RegisterForm, StyledPasswordChangeForm, StyledPasswordResetForm,
                    StyledSetPasswordForm)

LOGIN_LIMIT, LOGIN_WINDOW = 10, 300          # 10 attempts per 5 minutes per IP
REGISTER_LIMIT, REGISTER_WINDOW = 5, 3600    # 5 registrations per hour per IP


def register(request):
    if request.user.is_authenticated:
        return redirect("surveys:dashboard")
    if not platform_settings()["registration_open"]:
        return render(request, "accounts/register_closed.html")
    form = RegisterForm(request.POST or None)
    if request.method == "POST":
        try:
            ratelimit.check(request, "register", REGISTER_LIMIT, REGISTER_WINDOW)
        except RateLimited as exc:
            form.add_error(None, exc.message)
        else:
            if form.is_valid():
                user = form.save()
                login(request, user)
                audit.log(user, "account.register", "user", user.pk, {}, request)
                messages.success(request, f"Welcome to PollPulse, {user.first_name or user.username}.")
                return redirect("surveys:dashboard")
    return render(request, "accounts/register.html", {"form": form})


class PollPulseLoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        try:
            ratelimit.check(request, "login", LOGIN_LIMIT, LOGIN_WINDOW)
        except RateLimited as exc:
            form = self.get_form()
            form.errors.clear()
            form.add_error(None, exc.message)
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        audit.log(None, "account.login_failed", "user", "",
                  {"username": self.request.POST.get("username", "")[:150]}, self.request)
        return super().form_invalid(form)


class PollPulseLogoutView(auth_views.LogoutView):
    """Django only allows logout via POST, so a link on another site cannot log you out."""

    def post(self, request, *args, **kwargs):
        messages.info(request, "You have been signed out.")
        return super().post(request, *args, **kwargs)


@login_required
def profile(request):
    profile_form = ProfileForm(instance=request.user)
    password_form = StyledPasswordChangeForm(request.user)
    if request.method == "POST":
        if request.POST.get("action") == "profile":
            profile_form = ProfileForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Profile saved.")
                return redirect("accounts:profile")
        elif request.POST.get("action") == "password":
            password_form = StyledPasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # keep this session signed in
                messages.success(request, "Password changed.")
                return redirect("accounts:profile")
    try:
        stats = SurveyService(request.user).dashboard()
    except DatabaseUnavailable:
        stats = None
    return render(request, "accounts/profile.html", {
        "profile_form": profile_form, "password_form": password_form, "stats": stats, "nav": "profile",
    })


# ---- Password reset (Django's secure, token-based flow) ----

password_reset = auth_views.PasswordResetView.as_view(
    template_name="accounts/password_reset.html", form_class=StyledPasswordResetForm,
    email_template_name="accounts/email/password_reset.txt", subject_template_name="accounts/email/password_reset_subject.txt",
    success_url=reverse_lazy("accounts:password_reset_done"))
password_reset_done = auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html")
password_reset_confirm = auth_views.PasswordResetConfirmView.as_view(
    template_name="accounts/password_reset_confirm.html", form_class=StyledSetPasswordForm,
    success_url=reverse_lazy("accounts:password_reset_complete"))
password_reset_complete = auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html")
