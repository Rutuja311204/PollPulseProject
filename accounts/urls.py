from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.PollPulseLoginView.as_view(), name="login"),
    path("logout/", views.PollPulseLogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("password-reset/", views.password_reset, name="password_reset"),
    path("password-reset/sent/", views.password_reset_done, name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.password_reset_confirm, name="password_reset_confirm"),
    path("reset/done/", views.password_reset_complete, name="password_reset_complete"),
]
