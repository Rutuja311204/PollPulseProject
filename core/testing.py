"""
Shared test helpers.

Every test gets a fresh, empty MongoDB database:
  * by default an in-memory mongomock database (no server needed, never
    touches real data)
  * with POLLPULSE_TEST_REAL_MONGO=1, a real database called
    "pollpulse_test" on your local MongoDB, dropped after each test
"""
import os

import mongomock
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from core import mongo

USE_REAL_MONGO = os.getenv("POLLPULSE_TEST_REAL_MONGO") == "1"
TEST_DB_NAME = "pollpulse_test"
PASSWORD = "Str0ng!Pass2026"

SAMPLE_QUESTIONS = [
    {"type": "single_choice", "title": "Favourite language?", "required": True, "options": ["Python", "Java", "C++"]},
    {"type": "yes_no", "title": "Do you use Git?", "required": True},
    {"type": "rating", "title": "Rate the course", "required": False, "config": {"max": 5}},
]


class MongoTestCase(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # reset rate-limit counters
        if USE_REAL_MONGO:
            client = mongo.get_client()
            client.drop_database(TEST_DB_NAME)
            self.db = client[TEST_DB_NAME]
        else:
            self.db = mongomock.MongoClient(tz_aware=True)[TEST_DB_NAME]
        mongo.use_database(self.db)

    def tearDown(self):
        if USE_REAL_MONGO:
            mongo.get_client().drop_database(TEST_DB_NAME)
        mongo.use_database(None)
        super().tearDown()

    # ---------------- helpers ----------------

    def make_user(self, username="owner", password=PASSWORD, **extra):
        return User.objects.create_user(username=username, password=password, email=f"{username}@example.com", **extra)

    def login(self, user):
        self.client.force_login(user)

    def make_survey(self, owner, questions=None, settings=None, publish=True, title="Course Feedback"):
        from surveys.survey_service import SurveyService

        service = SurveyService(owner)
        survey_id = service.create(title, "A test survey", questions or SAMPLE_QUESTIONS, settings or {})
        if publish:
            service.publish(survey_id)
        return survey_id

    def survey_doc(self, survey_id):
        from surveys.survey_service import prepare

        return prepare(self.db.surveys.find_one({"_id": mongo.to_object_id(survey_id)}))

    def option_id(self, survey, number, text):
        question = survey["questions"][number - 1]
        return next(o["oid"] for o in question["options"] if o["text"] == text)

    def qid(self, survey, number):
        return survey["questions"][number - 1]["qid"]

    def answers(self, survey, lang="Python", git="yes", rating=None):
        raw = {self.qid(survey, 1): {"value": self.option_id(survey, 1, lang)}, self.qid(survey, 2): git}
        if rating is not None:
            raw[self.qid(survey, 3)] = rating
        return raw

    def submit(self, survey_id, key="session:a", raw=None, **kwargs):
        from surveys.response_service import Respondent, ResponseService

        survey = self.survey_doc(survey_id)
        return ResponseService(survey).submit(Respondent(key), raw or self.answers(survey), **kwargs)
