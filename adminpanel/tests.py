"""Administrator access control, user management, moderation and audit."""
import json

from django.contrib.auth.models import User
from django.urls import reverse

from core.testing import PASSWORD, MongoTestCase


class AdminAccessTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.creator = self.make_user("creator")
        self.admin = self.make_user("staffer", is_staff=True)
        self.root = User.objects.create_superuser("root", "root@example.com", PASSWORD)

    def test_admin_pages_need_admin_role(self):
        urls = [reverse("adminpanel:dashboard"), reverse("adminpanel:users"), reverse("adminpanel:surveys"),
                reverse("adminpanel:audit"), reverse("adminpanel:settings")]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302, url)           # anonymous -> login
        self.client.force_login(self.creator)
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)                       # creators are refused
        self.assertContains(self.client.get(urls[0]), "can't open this page", status_code=403)
        self.client.force_login(self.admin)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for period in ("week", "month"):
            self.assertEqual(self.client.get(f"{urls[0]}?period={period}").status_code, 200)

    def test_admin_api_permission(self):
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(reverse("api:admin_stats")).status_code, 403)
        self.client.force_login(self.admin)
        data = self.client.get(reverse("api:admin_stats")).json()
        self.assertEqual(data["overview"]["users_total"], 3)

    def test_suspend_user_blocks_login_and_is_audited(self):
        self.client.force_login(self.admin)
        url = reverse("adminpanel:user_status", args=[self.creator.id])
        self.client.post(url, {"action": "suspend", "reason": ""})               # reason required
        self.creator.refresh_from_db()
        self.assertTrue(self.creator.is_active)
        self.client.post(url, {"action": "suspend", "reason": "Spam surveys"})
        self.creator.refresh_from_db()
        self.assertFalse(self.creator.is_active)
        entry = self.db.audit_logs.find_one({"action": "admin.user_suspend"})
        self.assertEqual((entry["actor_username"], entry["details"]["reason"]), ("staffer", "Spam surveys"))
        login = self.client_class().post(reverse("accounts:login"), {"username": "creator", "password": PASSWORD})
        self.assertEqual(login.status_code, 200)  # stays on the login page
        self.client.post(url, {"action": "activate"})
        self.creator.refresh_from_db()
        self.assertTrue(self.creator.is_active)

    def test_protective_rules(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("adminpanel:user_status", args=[self.admin.id]), {"action": "suspend", "reason": "testing self"})
        self.client.post(reverse("adminpanel:user_status", args=[self.root.id]), {"action": "suspend", "reason": "testing super"})
        self.client.post(reverse("adminpanel:user_role", args=[self.creator.id]), {"grant": "1"})
        self.admin.refresh_from_db(), self.root.refresh_from_db(), self.creator.refresh_from_db()
        self.assertTrue(self.admin.is_active and self.root.is_active)
        self.assertFalse(self.creator.is_staff)  # only superusers grant roles
        self.client.force_login(self.root)
        self.client.post(reverse("adminpanel:user_role", args=[self.creator.id]), {"grant": "1"})
        self.creator.refresh_from_db()
        self.assertTrue(self.creator.is_staff)

    def test_survey_moderation(self):
        survey_id = self.make_survey(self.creator)
        token = self.survey_doc(survey_id)["public_token"]
        self.client.force_login(self.admin)
        url = reverse("adminpanel:survey_moderate", args=[survey_id])
        self.client.post(url, {"action": "suspend", "reason": "Collects personal data without consent"})
        self.assertTrue(self.survey_doc(survey_id)["suspended"])
        public = self.client_class().get(reverse("surveys:respond", args=[token]))
        self.assertContains(public, "suspended")
        self.client.force_login(self.creator)
        publish = self.client.post(reverse("surveys:publish", args=[survey_id]), follow=True)
        self.assertContains(publish, "suspended by an administrator")
        self.client.force_login(self.admin)
        self.client.post(url, {"action": "unsuspend"})
        self.client.post(url, {"action": "archive"})
        self.assertEqual(self.survey_doc(survey_id)["status"], "archived")
        actions = {e["action"] for e in self.db.audit_logs.find({"target_id": survey_id})}
        self.assertTrue({"admin.survey_suspend", "admin.survey_unsuspend", "admin.survey_archive"} <= actions)
        api = self.client.post(reverse("api:admin_moderation", args=[survey_id]), json.dumps({"action": "suspend", "reason": "x"}),
                               content_type="application/json")
        self.assertEqual(api.status_code, 400)  # reason too short

    def test_registration_switch(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("adminpanel:settings"), {"announcement": "Maintenance on Saturday", "max_surveys_per_user": "50"})
        self.client.logout()
        self.assertContains(self.client.get(reverse("accounts:register")), "Registration is closed")
        self.client.force_login(self.creator)
        self.assertContains(self.client.get(reverse("surveys:dashboard")), "Maintenance on Saturday")
        self.assertEqual(self.db.audit_logs.count_documents({"action": "admin.settings"}), 1)

    def test_audit_filters(self):
        self.make_survey(self.creator)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("adminpanel:audit") + "?action=survey.publish")
        self.assertContains(page, '<td class="text-nowrap">Published survey</td>', html=False)
        self.assertNotContains(page, '<td class="text-nowrap">Created survey</td>', html=False)
