"""Registration, login, rate limiting, profile and password reset."""
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse

from core.testing import PASSWORD, MongoTestCase


class AccountTests(MongoTestCase):
    def test_register_hashes_password_and_signs_in(self):
        response = self.client.post(reverse("accounts:register"), {
            "first_name": "Rutuja", "username": "rutuja", "email": "Rutuja@Example.com",
            "password1": PASSWORD, "password2": PASSWORD})
        self.assertRedirects(response, reverse("surveys:dashboard"))
        user = User.objects.get(username="rutuja")
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertNotIn(PASSWORD, user.password)
        self.assertEqual(user.email, "rutuja@example.com")
        self.assertFalse(user.is_staff)  # new accounts are creators, never admins

    def test_weak_password_and_duplicate_email(self):
        self.make_user("taken")
        response = self.client.post(reverse("accounts:register"), {
            "first_name": "A", "username": "weak", "email": "taken@example.com", "password1": "12345678", "password2": "12345678"})
        self.assertContains(response, "already exists")
        self.assertContains(response, "too common")
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_login_logout(self):
        self.make_user("asha")
        response = self.client.post(reverse("accounts:login"), {"username": "asha", "password": PASSWORD})
        self.assertRedirects(response, reverse("surveys:dashboard"))
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)  # GET logout refused
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("surveys:dashboard")).status_code, 302)

    def test_failed_logins_are_rate_limited_and_audited(self):
        self.make_user("asha")
        for _ in range(10):
            self.client.post(reverse("accounts:login"), {"username": "asha", "password": "wrong"})
        response = self.client.post(reverse("accounts:login"), {"username": "asha", "password": PASSWORD})
        self.assertContains(response, "Too many attempts")
        self.assertEqual(self.db.audit_logs.count_documents({"action": "account.login_failed"}), 10)

    def test_profile_update_and_password_change(self):
        user = self.make_user("asha")
        self.client.force_login(user)
        self.client.post(reverse("accounts:profile"), {"action": "profile", "first_name": "Asha K", "email": "asha.k@example.com"})
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Asha K")
        self.client.post(reverse("accounts:profile"), {"action": "password", "old_password": PASSWORD,
                                                       "new_password1": "An0ther!Secret", "new_password2": "An0ther!Secret"})
        user.refresh_from_db()
        self.assertTrue(user.check_password("An0ther!Secret"))
        self.assertEqual(self.client.get(reverse("surveys:dashboard")).status_code, 200)  # still signed in

    def test_password_reset_email(self):
        self.make_user("asha")
        response = self.client.post(reverse("accounts:password_reset"), {"email": "asha@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/reset/", mail.outbox[0].body)
        self.client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        self.assertEqual(len(mail.outbox), 1)  # unknown address: same page, no email
