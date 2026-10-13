"""Response rules: availability, dates, limits, duplicates, edits, drafts, consent, identity."""
from datetime import timedelta
from unittest import mock

from core.exceptions import DuplicateResponse, SurveyUnavailable, ValidationFailed
from core.testing import MongoTestCase
from core.utils import utc_now
from surveys.response_service import Respondent, ResponseService
from surveys.survey_service import SurveyService


class SubmissionTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        self.survey_id = self.make_survey(self.owner)
        self.survey = self.survey_doc(self.survey_id)

    def test_valid_response_is_stored_with_typed_answers(self):
        result = self.submit(self.survey_id, raw=self.answers(self.survey, rating="4"))
        doc = self.db.responses.find_one()
        self.assertFalse(result["edited"])
        self.assertEqual([a["type"] for a in doc["answers"]], ["single_choice", "yes_no", "rating"])
        self.assertEqual(doc["answers"][2]["value"], 4)
        self.assertIsNone(doc["respondent"])                 # anonymous survey
        self.assertNotIn("session:a", doc["dedupe_key"])     # only a hash is stored
        self.assertEqual(self.survey_doc(self.survey_id)["response_count"], 1)

    def test_required_question_missing(self):
        raw = self.answers(self.survey)
        del raw[self.qid(self.survey, 2)]
        with self.assertRaises(ValidationFailed) as ctx:
            self.submit(self.survey_id, raw=raw)
        self.assertIn(self.qid(self.survey, 2), ctx.exception.field_errors)
        self.assertEqual(self.db.responses.count_documents({}), 0)

    def test_option_from_another_question_or_survey_rejected(self):
        raw = self.answers(self.survey)
        raw[self.qid(self.survey, 1)] = {"value": "o_deadbeef"}
        with self.assertRaises(ValidationFailed):
            self.submit(self.survey_id, raw=raw)

    def test_unknown_question_rejected(self):
        raw = self.answers(self.survey)
        raw["q_12345678"] = "x"
        with self.assertRaises(ValidationFailed):
            self.submit(self.survey_id, raw=raw)

    def test_draft_closed_archived_and_suspended_surveys_reject(self):
        service = SurveyService(self.owner)
        draft_id = self.make_survey(self.owner, publish=False, title="Draft survey")
        with self.assertRaises(SurveyUnavailable) as ctx:
            self.submit(draft_id, raw=self.answers(self.survey_doc(draft_id)))
        self.assertEqual(ctx.exception.reason, "draft")
        service.close(self.survey_id)
        with self.assertRaises(SurveyUnavailable) as ctx:
            self.submit(self.survey_id)
        self.assertEqual(ctx.exception.reason, "closed")
        service.publish(self.survey_id)
        self.db.surveys.update_one({}, {"$set": {"moderation.status": "suspended"}})
        with self.assertRaises(SurveyUnavailable) as ctx:
            self.submit(self.survey_id)
        self.assertEqual(ctx.exception.reason, "suspended")

    def test_start_and_end_dates(self):
        service = SurveyService(self.owner)
        now = utc_now()
        service.update_settings(self.survey_id, {"start_at": now + timedelta(days=1)})
        with self.assertRaises(SurveyUnavailable) as ctx:
            self.submit(self.survey_id)
        self.assertEqual(ctx.exception.reason, "not_started")
        service.update_settings(self.survey_id, {"start_at": now - timedelta(days=2), "end_at": now - timedelta(days=1)})
        with self.assertRaises(SurveyUnavailable) as ctx:
            self.submit(self.survey_id)
        self.assertEqual(ctx.exception.reason, "ended")
        service.update_settings(self.survey_id, {"start_at": now - timedelta(days=1), "end_at": now + timedelta(days=1)})
        self.submit(self.survey_id)

    def test_response_limit(self):
        SurveyService(self.owner).update_settings(self.survey_id, {"max_responses": 2})
        self.submit(self.survey_id, "session:1")
        self.submit(self.survey_id, "session:2")
        with self.assertRaises(SurveyUnavailable) as ctx:
            self.submit(self.survey_id, "session:3")
        self.assertEqual(ctx.exception.reason, "limit_reached")
        self.assertEqual(self.db.responses.count_documents({}), 2)

    def test_limit_reservation_is_atomic(self):
        """Even if the availability check is bypassed (simulating two requests
        at the same moment), the conditional $inc refuses the extra response."""
        SurveyService(self.owner).update_settings(self.survey_id, {"max_responses": 1})
        self.submit(self.survey_id, "session:1")
        with mock.patch("surveys.response_service.availability", return_value=(True, "", "")):
            with self.assertRaises(SurveyUnavailable):
                self.submit(self.survey_id, "session:2")
        self.assertEqual(self.survey_doc(self.survey_id)["response_count"], 1)

    def test_duplicate_per_session(self):
        self.submit(self.survey_id, "session:same")
        with self.assertRaises(DuplicateResponse):
            self.submit(self.survey_id, "session:same")
        self.submit(self.survey_id, "session:other")
        self.assertEqual(self.db.responses.count_documents({}), 2)

    def test_unique_index_stops_race_condition(self):
        self.submit(self.survey_id, "session:racer")
        with mock.patch.object(ResponseService, "existing_response", return_value=None):
            with self.assertRaises(DuplicateResponse):
                self.submit(self.survey_id, "session:racer")
        self.assertEqual(self.survey_doc(self.survey_id)["response_count"], 1)  # reserved slot released

    def test_no_protection_allows_repeat_submissions(self):
        SurveyService(self.owner).update_settings(self.survey_id, {"duplicate_protection": "none"})
        self.submit(self.survey_id, "session:kiosk")
        self.submit(self.survey_id, "session:kiosk")
        self.assertEqual(self.db.responses.count_documents({}), 2)

    def test_editing_an_existing_response(self):
        SurveyService(self.owner).update_settings(self.survey_id, {"allow_edit": True})
        self.submit(self.survey_id, "session:e", raw=self.answers(self.survey, lang="Python"))
        result = self.submit(self.survey_id, "session:e", raw=self.answers(self.survey, lang="Java"))
        self.assertTrue(result["edited"])
        doc = self.db.responses.find_one()
        self.assertEqual(doc["answers"][0]["value"], self.option_id(self.survey, 1, "Java"))
        self.assertIsNotNone(doc["edited_at"])
        self.assertEqual(self.db.responses.count_documents({}), 1)

    def test_consent_required_when_configured(self):
        SurveyService(self.owner).update_settings(self.survey_id, {"consent_text": "I agree to take part."})
        with self.assertRaises(ValidationFailed) as ctx:
            self.submit(self.survey_id)
        self.assertIn("consent", ctx.exception.field_errors)
        self.submit(self.survey_id, consent=True)
        self.assertTrue(self.db.responses.find_one()["consent"])

    def test_identified_survey_records_username(self):
        SurveyService(self.owner).update_settings(self.survey_id, {"access": "login", "identity": "identified",
                                                                   "duplicate_protection": "user"})
        user = self.make_user("asha")
        survey = self.survey_doc(self.survey_id)
        ResponseService(survey).submit(Respondent(f"user:{user.id}", user.id, user.username), self.answers(survey))
        self.assertEqual(self.db.responses.find_one()["respondent"]["username"], "asha")


class DraftAndCompletionTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        self.survey_id = self.make_survey(self.owner)
        self.survey = self.survey_doc(self.survey_id)
        self.service = ResponseService(self.survey)
        self.respondent = Respondent("session:draft")

    def test_save_and_resume_by_token(self):
        attempt = self.service.start_attempt(self.respondent)
        token = self.service.save_draft(self.respondent, {self.qid(self.survey, 2): "yes", "q_unknown1": "dropped"})
        self.assertEqual(token, attempt["resume_token"])
        resumed = self.service.attempt_by_resume_token(token)
        self.assertEqual(resumed["draft"], {self.qid(self.survey, 2): "yes"})

    def test_drafts_can_be_disabled(self):
        SurveyService(self.owner).update_settings(self.survey_id, {"allow_save_resume": False})
        service = ResponseService(self.survey_doc(self.survey_id))
        with self.assertRaises(ValidationFailed):
            service.save_draft(self.respondent, {})

    def test_submission_completes_attempt_and_records_duration(self):
        attempt = self.service.start_attempt(self.respondent)
        self.db.response_sessions.update_one({"_id": attempt["_id"]}, {"$set": {"started_at": utc_now() - timedelta(seconds=90)}})
        self.service.submit(self.respondent, self.answers(self.survey))
        session = self.db.response_sessions.find_one({"_id": attempt["_id"]})
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["draft"], {})
        self.assertAlmostEqual(self.db.responses.find_one()["duration_seconds"], 90, delta=3)

    def test_start_attempt_is_idempotent(self):
        first = self.service.start_attempt(self.respondent)
        second = self.service.start_attempt(self.respondent)
        self.assertEqual(first["_id"], second["_id"])
        self.assertEqual(self.db.response_sessions.count_documents({}), 1)
