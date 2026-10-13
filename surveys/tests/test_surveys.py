"""Survey life cycle, settings rules, roles and listing."""
from datetime import timedelta

from core.exceptions import ConflictError, NotFound, PermissionDeniedError, ValidationFailed
from core.testing import MongoTestCase
from core.utils import utc_now
from surveys.survey_service import SurveyService, validate_settings


class SettingsRuleTests(MongoTestCase):
    def assertInvalid(self, raw, fragment):
        with self.assertRaises(ValidationFailed) as ctx:
            validate_settings(raw)
        self.assertIn(fragment, " ".join(ctx.exception.errors))

    def test_combinations(self):
        self.assertInvalid({"identity": "identified"}, "sign in")
        self.assertInvalid({"duplicate_protection": "user"}, "sign in")
        self.assertInvalid({"allow_edit": True, "duplicate_protection": "none"}, "duplicate protection")
        now = utc_now()
        self.assertInvalid({"start_at": now, "end_at": now - timedelta(hours=1)}, "after the start")
        self.assertInvalid({"max_responses": 0}, "Maximum responses")
        clean = validate_settings({"access": "login", "identity": "identified", "duplicate_protection": "user"})
        self.assertEqual(clean["identity"], "identified")


class LifecycleTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        self.service = SurveyService(self.owner)

    def test_create_starts_as_draft_with_unpredictable_token(self):
        survey_id = self.make_survey(self.owner, publish=False)
        survey = self.service.get(survey_id)
        self.assertEqual(survey["status"], "draft")
        self.assertGreaterEqual(len(survey["public_token"]), 12)
        self.assertNotIn(survey["id"], survey["public_token"])
        self.assertEqual(self.db.audit_logs.count_documents({"action": "survey.create"}), 1)

    def test_invalid_survey_is_not_saved(self):
        with self.assertRaises(ValidationFailed) as ctx:
            self.service.create("x", "", [{"type": "rating", "title": "R", "config": {"max": 99}}])
        self.assertGreaterEqual(len(ctx.exception.errors), 2)
        self.assertEqual(self.db.surveys.count_documents({}), 0)

    def test_publish_is_blocked_by_quality_errors(self):
        survey_id = self.service.create("Draft survey", "", [{"type": "single_choice", "title": "Pick", "options": ["Only one"]}])
        with self.assertRaises(ValidationFailed) as ctx:
            self.service.publish(survey_id)
        self.assertIn("at least 2", ctx.exception.errors[0])
        empty = self.service.create("Empty survey", "")
        with self.assertRaises(ValidationFailed):
            self.service.publish(empty)

    def test_publish_close_reopen_archive_restore(self):
        survey_id = self.make_survey(self.owner)
        self.assertEqual(self.service.get(survey_id)["status"], "published")
        self.service.close(survey_id)
        self.assertEqual(self.service.get(survey_id)["status"], "closed")
        with self.assertRaises(ConflictError):
            self.service.close(survey_id)
        self.service.publish(survey_id)  # reopen
        self.assertTrue(self.service.get(survey_id)["accepting"])
        self.service.archive(survey_id)
        survey = self.service.get(survey_id)
        self.assertEqual(survey["status"], "archived")
        self.assertFalse(survey["accepting"])
        with self.assertRaises(ConflictError):
            self.service.publish(survey_id)
        self.service.restore(survey_id)
        self.assertEqual(self.service.get(survey_id)["status"], "closed")

    def test_duplicate_creates_independent_draft(self):
        survey_id = self.make_survey(self.owner)
        copy_id = self.service.duplicate(survey_id)
        original, copy = self.service.get(survey_id), self.service.get(copy_id)
        self.assertEqual(copy["status"], "draft")
        self.assertTrue(copy["title"].startswith("Copy of"))
        self.assertEqual([q["title"] for q in copy["questions"]], [q["title"] for q in original["questions"]])
        self.assertNotEqual(copy["questions"][0]["qid"], original["questions"][0]["qid"])
        self.assertNotEqual(copy["public_token"], original["public_token"])

    def test_questions_lock_after_first_response(self):
        survey_id = self.make_survey(self.owner)
        self.service.save_questions(survey_id, self.service.get(survey_id)["questions"])  # allowed: no responses
        self.submit(survey_id)
        with self.assertRaises(ConflictError):
            self.service.save_questions(survey_id, [])
        self.service.update_details(survey_id, "Renamed survey", "still editable")
        self.assertEqual(self.service.get(survey_id)["title"], "Renamed survey")

    def test_question_level_operations(self):
        survey_id = self.make_survey(self.owner, publish=False)
        new = self.service.add_question(survey_id, {"type": "email", "title": "Your email"})
        self.assertEqual(self.service.get(survey_id)["question_count"], 4)
        self.service.update_question(survey_id, new["qid"], {"required": True})
        self.assertTrue(self.service.get(survey_id)["questions"][3]["required"])
        order = [q["qid"] for q in self.service.get(survey_id)["questions"]][::-1]
        self.service.reorder_questions(survey_id, order)
        self.assertEqual([q["qid"] for q in self.service.get(survey_id)["questions"]], order)
        with self.assertRaises(ValidationFailed):
            self.service.reorder_questions(survey_id, order[:2])
        self.service.delete_question(survey_id, new["qid"])
        self.assertEqual(self.service.get(survey_id)["question_count"], 3)

    def test_delete_removes_responses_and_attempts(self):
        survey_id = self.make_survey(self.owner)
        self.submit(survey_id, "session:1")
        self.submit(survey_id, "session:2")
        self.assertEqual(self.service.delete(survey_id), 2)
        self.assertEqual(self.db.surveys.count_documents({}), 0)
        self.assertEqual(self.db.responses.count_documents({}), 0)

    def test_survey_limit_per_user(self):
        from core.platform import update_settings
        update_settings(True, "", 2, self.make_user("boss", is_staff=True))
        self.make_survey(self.owner)
        self.make_survey(self.owner)
        with self.assertRaises(ConflictError):
            self.make_survey(self.owner)


class RoleTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user("owner")
        self.editor = self.make_user("editor")
        self.viewer = self.make_user("viewer")
        self.stranger = self.make_user("stranger")
        self.survey_id = self.make_survey(self.owner)
        service = SurveyService(self.owner)
        service.add_collaborator(self.survey_id, "editor", "editor")
        service.add_collaborator(self.survey_id, "VIEWER", "viewer")  # usernames are case-insensitive

    def test_editor_can_edit_but_not_delete(self):
        service = SurveyService(self.editor)
        service.update_details(self.survey_id, "Edited by editor", "")
        service.close(self.survey_id)
        with self.assertRaises(PermissionDeniedError):
            service.delete(self.survey_id)
        with self.assertRaises(PermissionDeniedError):
            service.add_collaborator(self.survey_id, "stranger", "viewer")

    def test_viewer_is_read_only(self):
        service = SurveyService(self.viewer)
        self.assertEqual(service.get(self.survey_id)["role"], "viewer")
        with self.assertRaises(PermissionDeniedError):
            service.update_details(self.survey_id, "Nope", "")
        with self.assertRaises(PermissionDeniedError):
            service.publish(self.survey_id)

    def test_stranger_gets_not_found(self):
        service = SurveyService(self.stranger)
        for call in (lambda: service.get(self.survey_id), lambda: service.delete(self.survey_id),
                     lambda: service.update_settings(self.survey_id, {})):
            with self.assertRaises(NotFound):
                call()

    def test_shared_scope_and_removal(self):
        shared, total = SurveyService(self.viewer).list_surveys(scope="shared")
        self.assertEqual(total, 1)
        SurveyService(self.owner).remove_collaborator(self.survey_id, self.viewer.id)
        self.assertEqual(SurveyService(self.viewer).list_surveys()[1], 0)

    def test_cannot_add_owner_or_unknown_user(self):
        service = SurveyService(self.owner)
        with self.assertRaises(ValidationFailed):
            service.add_collaborator(self.survey_id, "owner", "editor")
        with self.assertRaises(ValidationFailed):
            service.add_collaborator(self.survey_id, "ghost", "editor")
        with self.assertRaises(ValidationFailed):
            service.add_collaborator(self.survey_id, "stranger", "admin")


class ListingTests(MongoTestCase):
    def test_search_filter_sort_paginate(self):
        owner = self.make_user()
        service = SurveyService(owner)
        ids = [self.make_survey(owner, title=t) for t in ("Python Poll", "Canteen Feedback", "Advanced python")]
        service.close(ids[1])
        self.make_survey(owner, title="Draft only", publish=False)
        self.assertEqual(service.list_surveys(q="PYTHON")[1], 2)
        self.assertEqual([s["title"] for s in service.list_surveys(status="closed")[0]], ["Canteen Feedback"])
        self.assertEqual(service.list_surveys(q=".*")[1], 0)  # regex characters are escaped
        newest = [s["title"] for s in service.list_surveys(sort="newest", page_size=10)[0]]
        oldest = [s["title"] for s in service.list_surveys(sort="oldest", page_size=10)[0]]
        self.assertEqual(newest, oldest[::-1])
        page1, total = service.list_surveys(page=1, page_size=3)
        page2, _ = service.list_surveys(page=2, page_size=3)
        self.assertEqual((len(page1), len(page2), total), (3, 1, 4))
        service.archive(ids[0])
        self.assertEqual(service.list_surveys()[1], 3)  # archived hidden by default
        self.assertEqual(service.list_surveys(status="archived")[1], 1)

    def test_dashboard_counts_come_from_database(self):
        owner = self.make_user()
        a = self.make_survey(owner, title="Survey A")
        self.make_survey(owner, title="Survey B", publish=False)
        self.submit(a, "session:1")
        self.submit(a, "session:2")
        stats = SurveyService(owner).dashboard()
        self.assertEqual((stats["total"], stats["published"], stats["draft"], stats["responses"]), (2, 1, 1, 2))
        self.assertEqual(stats["top"][0]["title"], "Survey A")
