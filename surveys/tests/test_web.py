"""HTML pages, the respondent flow, exports and the REST API (Django test client)."""
import csv
import io
import json
from unittest import mock

from django.test import Client
from django.urls import reverse
from pymongo.errors import ServerSelectionTimeoutError

from core.testing import MongoTestCase
from surveys import exporters
from surveys.survey_service import SurveyService


class PageTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        self.survey_id = self.make_survey(self.owner)
        self.submit(self.survey_id, raw=None)
        self.login(self.owner)

    def test_creator_pages_render(self):
        sid = self.survey_id
        for url in [reverse("surveys:dashboard"), reverse("surveys:list"), reverse("surveys:create"),
                    reverse("surveys:overview", args=[sid]), reverse("surveys:builder", args=[sid]),
                    reverse("surveys:settings", args=[sid]), reverse("surveys:analytics", args=[sid]),
                    reverse("surveys:responses", args=[sid]), reverse("surveys:reports", args=[sid]),
                    reverse("surveys:print_report", args=[sid]), reverse("surveys:preview", args=[sid]),
                    reverse("surveys:reports_index"), reverse("surveys:compare"), reverse("accounts:profile"),
                    reverse("surveys:list") + "?q=nothing-matches", reverse("surveys:analytics", args=[sid]) + "?start=2020-01-01&end=2020-01-02"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_login_required_everywhere(self):
        self.client.logout()
        for name in ("surveys:dashboard", "surveys:list", "surveys:reports_index"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_create_with_suggested_questions(self):
        response = self.client.post(reverse("surveys:create"), {"title": "Workshop survey", "description": "",
                                                                 "start": "suggest", "topic": "Python workshop"})
        doc = self.db.surveys.find_one({"title": "Workshop survey"})
        self.assertRedirects(response, reverse("surveys:builder", args=[str(doc["_id"])]))
        self.assertGreaterEqual(len(doc["questions"]), 4)
        self.assertEqual(doc["status"], "draft")

    def test_settings_form_enforces_rules(self):
        url = reverse("surveys:settings", args=[self.survey_id])
        data = {"details-title": "Course Feedback", "details-description": "",
                "settings-access": "public", "settings-identity": "identified", "settings-duplicate_protection": "session",
                "settings-display_mode": "all", "settings-allow_save_resume": "on"}
        response = self.client.post(url, data)
        self.assertContains(response, "Identified responses require")
        data["settings-identity"] = "anonymous"
        data["settings-max_responses"] = "50"
        self.assertRedirects(self.client.post(url, data), url)
        self.assertEqual(self.survey_doc(self.survey_id)["settings"]["max_responses"], 50)

    def test_status_actions_are_post_only(self):
        url = reverse("surveys:close", args=[self.survey_id])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.assertEqual(self.survey_doc(self.survey_id)["status"], "closed")

    def test_delete_requires_typed_title(self):
        url = reverse("surveys:delete", args=[self.survey_id])
        self.client.post(url, {"confirm_title": "wrong"})
        self.assertEqual(self.db.surveys.count_documents({}), 1)
        self.assertRedirects(self.client.post(url, {"confirm_title": "Course Feedback"}), reverse("surveys:list"))
        self.assertEqual(self.db.surveys.count_documents({}), 0)

    def test_other_users_get_404_and_viewers_get_403(self):
        stranger = self.make_user("stranger")
        viewer = self.make_user("viewer")
        SurveyService(self.owner).add_collaborator(self.survey_id, "viewer", "viewer")
        self.client.force_login(stranger)
        for name in ("overview", "analytics", "settings", "export_responses"):
            self.assertEqual(self.client.get(reverse(f"surveys:{name}", args=[self.survey_id])).status_code, 404, name)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("surveys:analytics", args=[self.survey_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("surveys:settings", args=[self.survey_id])).status_code, 403)
        self.assertEqual(self.client.get("/surveys/not-an-id/").status_code, 404)

    def test_mongodb_outage_shows_friendly_page(self):
        with mock.patch("surveys.survey_service.SurveyService.dashboard", side_effect=ServerSelectionTimeoutError("down")):
            response = self.client.get(reverse("surveys:dashboard"))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "database is not reachable", status_code=503)

    def test_security_headers(self):
        response = self.client.get(reverse("surveys:dashboard"))
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")


class RespondentFlowTests(MongoTestCase):
    QUESTIONS = [
        {"type": "single_choice", "title": "Language", "required": True, "options": ["Python", "Java"], "config": {"allow_other": True}},
        {"type": "ranking", "title": "Rank", "required": True, "options": ["A", "B", "C"]},
        {"type": "long_text", "title": "Comments <b>bold?</b>"},
    ]

    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        self.survey_id = self.make_survey(self.owner, questions=self.QUESTIONS)
        self.survey = self.survey_doc(self.survey_id)
        self.url = reverse("surveys:respond", args=[self.survey["public_token"]])

    def form(self, lang="Python", ranks=("1", "2", "3"), other=""):
        q1, q2, q3 = self.survey["questions"]
        data = {f"q_{q1['qid']}": self.option_id(self.survey, 1, lang) if lang != "other" else "__other__",
                f"q_{q1['qid']}_other": other, f"q_{q3['qid']}": "Nice", "action": "submit"}
        for option, rank in zip(q2["options"], ranks):
            data[f"q_{q2['qid']}_rank_{option['oid']}"] = rank
        return data

    def test_full_html_flow_once_per_browser(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Language")
        self.assertContains(page, "Comments &lt;b&gt;bold?&lt;/b&gt;")  # user text is escaped
        self.assertEqual(self.db.response_sessions.count_documents({}), 1)  # visit recorded
        response = self.client.post(self.url, self.form())
        self.assertRedirects(response, reverse("surveys:thanks", args=[self.survey["public_token"]]))
        doc = self.db.responses.find_one()
        self.assertEqual(len(doc["answers"][1]["value"]), 3)
        self.assertIsNotNone(doc["duration_seconds"])
        self.assertRedirects(self.client.get(self.url), reverse("surveys:thanks", args=[self.survey["public_token"]]) + "?already=1")

    def test_invalid_answers_are_shown_and_not_saved(self):
        response = self.client.post(self.url, self.form(ranks=("1", "1", "3")))
        self.assertContains(response, "exactly once")
        response = self.client.post(self.url, self.form(lang="other", other=""))
        self.assertContains(response, "Other")
        self.assertEqual(self.db.responses.count_documents({}), 0)

    def test_save_draft_and_resume(self):
        self.client.get(self.url)
        data = self.form()
        data["action"] = "save_draft"
        response = self.client.post(self.url, data)
        self.assertContains(response, "?resume=")
        token = self.db.response_sessions.find_one()["resume_token"]
        other_device = Client()
        page = other_device.get(f"{self.url}?resume={token}")
        self.assertContains(page, "restored")  # prefilled from the draft
        self.assertContains(page, '>Nice</textarea>', html=False)

    def test_unavailable_states(self):
        SurveyService(self.owner).close(self.survey_id)
        self.assertContains(self.client.get(self.url), "Not accepting responses")
        draft_id = self.make_survey(self.owner, publish=False, title="Hidden draft")
        draft_url = reverse("surveys:respond", args=[self.survey_doc(draft_id)["public_token"]])
        self.assertEqual(self.client.get(draft_url).status_code, 404)
        self.assertEqual(self.client.get("/s/doesnotexist123/").status_code, 404)

    def test_login_required_survey_redirects_to_login(self):
        SurveyService(self.owner).update_settings(self.survey_id, {"access": "login", "duplicate_protection": "user"})
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_preview_validates_without_saving(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("surveys:preview", args=[self.survey_id]), self.form())
        self.assertContains(response, "Nothing was saved")
        self.assertEqual(self.db.responses.count_documents({}), 0)

    def test_public_results_respect_setting_and_hide_text(self):
        results_url = reverse("surveys:public_results", args=[self.survey["public_token"]])
        self.assertContains(self.client.get(results_url), "not public")
        SurveyService(self.owner).update_settings(self.survey_id, {"results_public": True})
        self.client.post(self.url, self.form())
        page = self.client.get(results_url)
        self.assertContains(page, "Python")
        self.assertNotContains(page, "Nice")  # written answers are never public


class ExportTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        questions = [{"type": "short_text", "title": "Name", "required": True},
                     {"type": "multiple_choice", "title": "Tools", "options": ["Git", "Docker"]}]
        self.survey_id = self.make_survey(self.owner, questions=questions)
        survey = self.survey_doc(self.survey_id)
        self.submit(self.survey_id, raw={survey["questions"][0]["qid"]: "=HYPERLINK(\"http://evil\")",
                                         survey["questions"][1]["qid"]: {"value": [o["oid"] for o in survey["questions"][1]["options"]]}})

    def test_safe_cell(self):
        for dangerous in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx"):
            self.assertTrue(exporters.safe_cell(dangerous).startswith("'"))
        self.assertEqual(exporters.safe_cell(-5), -5)  # real numbers stay numbers
        self.assertEqual(exporters.safe_cell("Python"), "Python")

    def test_responses_csv_is_safe_and_complete(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("surveys:export_responses", args=[self.survey_id]))
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("﻿"))
        rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
        self.assertEqual(rows[0][-2:], ["Q1. Name", "Q2. Tools"])
        self.assertTrue(rows[1][-2].startswith("'="))
        self.assertEqual(rows[1][-1], "Git; Docker")
        self.assertEqual(self.db.audit_logs.count_documents({"action": "survey.export"}), 1)

    def test_analytics_csv(self):
        self.client.force_login(self.owner)
        body = self.client.get(reverse("surveys:export_analytics", args=[self.survey_id])).content.decode("utf-8")
        self.assertIn("Total responses,1", body)
        self.assertIn("Git,1,100.0", body)

    def test_export_permissions(self):
        url = reverse("surveys:export_responses", args=[self.survey_id])
        self.assertEqual(self.client.get(url).status_code, 302)              # anonymous -> login
        self.client.force_login(self.make_user("stranger"))
        self.assertEqual(self.client.get(url).status_code, 404)              # not a member
        viewer = self.make_user("viewer")
        SurveyService(self.owner).add_collaborator(self.survey_id, "viewer", "viewer")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(url).status_code, 200)              # viewers may export


class ApiTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        self.client.force_login(self.owner)

    def api(self, method, url, data=None, client=None):
        client = client or self.client
        return getattr(client, method)(url, json.dumps(data) if data is not None else None, content_type="application/json")

    def test_survey_crud_and_builder_flow(self):
        created = self.api("post", reverse("api:surveys"), {"title": "API survey"})
        self.assertEqual(created.status_code, 201)
        sid = created.json()["id"]
        saved = self.api("put", reverse("api:questions", args=[sid]), {"questions": [
            {"type": "single_choice", "title": "Pick one", "options": [{"text": "A"}], "required": True}]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["issues"][0]["level"], "error")
        blocked = self.api("post", reverse("api:survey_publish", args=[sid]))
        self.assertEqual(blocked.status_code, 400)
        question = saved.json()["questions"][0]
        question["options"].append({"text": "B"})
        self.api("put", reverse("api:questions", args=[sid]), {"questions": [question]})
        published = self.api("post", reverse("api:survey_publish", args=[sid]))
        self.assertEqual(published.json()["status"], "published")
        self.assertEqual(self.api("get", reverse("api:surveys")).json()["count"], 1)
        self.assertEqual(self.api("patch", reverse("api:survey", args=[sid]), {"title": "Renamed via API"}).json()["title"], "Renamed via API")
        self.assertEqual(self.api("delete", reverse("api:survey", args=[sid])).status_code, 200)

    def test_bad_question_payload(self):
        sid = self.api("post", reverse("api:surveys"), {"title": "API survey"}).json()["id"]
        response = self.api("put", reverse("api:questions", args=[sid]), {"questions": [{"type": "hologram", "title": "x"}]})
        self.assertEqual(response.status_code, 400)

    def test_public_submission_and_status_codes(self):
        sid = self.make_survey(self.owner)
        survey = self.survey_doc(sid)
        public = Client()
        detail = public.get(reverse("api:public_survey", args=[survey["public_token"]])).json()
        self.assertNotIn("owner_id", detail)
        self.assertNotIn("response_count", detail)
        payload = {"answers": [{"qid": self.qid(survey, 1), "value": self.option_id(survey, 1, "Java")},
                               {"qid": self.qid(survey, 2), "value": "no"}]}
        url = reverse("api:public_submit", args=[survey["public_token"]])
        self.assertEqual(self.api("post", url, payload, public).status_code, 201)
        self.assertEqual(self.api("post", url, payload, public).status_code, 409)       # same session again
        bad = {"answers": [{"qid": self.qid(survey, 1), "value": "o_00000000"}, {"qid": self.qid(survey, 2), "value": "no"}]}
        response = self.api("post", url, bad, Client())
        self.assertEqual(response.status_code, 400)
        self.assertIn(self.qid(survey, 1), response.json()["field_errors"])
        SurveyService(self.owner).close(sid)
        closed = self.api("post", url, payload, Client())
        self.assertEqual((closed.status_code, closed.json()["reason"]), (403, "closed"))

    def test_analytics_and_suggestions(self):
        sid = self.make_survey(self.owner)
        self.submit(sid)
        data = self.api("get", reverse("api:analytics", args=[sid])).json()
        self.assertEqual(data["total_responses"], 1)
        self.assertTrue(data["insights"])
        suggestions = self.api("post", reverse("api:ai_suggest"), {"topic": "college canteen"}).json()
        self.assertEqual(suggestions["source"], "template")
        self.assertTrue(suggestions["questions"])

    def test_api_requires_login_and_csrf(self):
        anonymous = Client()
        self.assertIn(anonymous.get(reverse("api:surveys")).status_code, (401, 403))
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        response = strict.post(reverse("api:surveys"), json.dumps({"title": "No token"}), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.db.surveys.count_documents({}), 0)


class AITests(MongoTestCase):
    def test_ai_suggestions_are_validated_and_fall_back(self):
        from surveys import ai
        reply = json.dumps({"questions": [
            {"type": "rating", "title": "How satisfied are you?", "config": {"max": 5}, "rationale": "overall"},
            {"type": "teleport", "title": "Invalid type is dropped"},
            {"type": "single_choice", "title": "Pick", "options": ["A", "B"]},
        ]})
        with self.settings(AI_API_BASE="http://ai.local/v1", AI_MODEL="test-model"):
            with mock.patch("surveys.ai.chat", return_value="Here you go:\n" + reply):
                result = ai.suggest_questions("library services")
            self.assertEqual(result["source"], "ai")
            self.assertEqual([q["type"] for q in result["questions"]], ["rating", "single_choice"])
            with mock.patch("surveys.ai.chat", side_effect=ai.AIUnavailable("down")):
                result = ai.suggest_questions("library services")
            self.assertEqual(result["source"], "template")
            self.assertIn("unavailable", result["note"])
        self.assertFalse(ai.is_configured())
        self.assertIsNone(ai.rewrite_insights([{"text": "x"}]))
