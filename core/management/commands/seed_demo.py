"""
python manage.py seed_demo [--reset] [--password PASSWORD]

Creates SAMPLE data for demonstrations and testing:
  * creator account  "demo"      (survey owner)
  * creator account  "reviewer"  (added to one survey as a viewer collaborator)
  * four surveys covering all 12 question types and every status
  * generated responses over the last 14 days, plus some abandoned attempts

Every generated answer is passed through the real validation code, and all
responses are marked source="seed" so they can be told apart from real ones.
No administrator is created here — use `python manage.py createsuperuser`.
"""
import random
import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core import mongo
from core.utils import keyed_hash, new_token, utc_now
from surveys.response_service import validate_answers
from surveys.survey_service import SurveyService

DEFAULT_PASSWORD = "PollPulse@2026"

WORKSHOP = {
    "title": "Python Workshop Feedback",
    "description": "Feedback on the two-day Python and Django workshop. It takes about 3 minutes.",
    "status": "published", "responses": 64, "abandoned": 9,
    "settings": {"display_mode": "one_per_page", "results_public": True,
                 "thank_you_message": "Thanks! Your feedback helps us plan the next workshop."},
    "questions": [
        {"type": "rating", "title": "Overall, how would you rate the workshop?", "required": True, "config": {"max": 5},
         "gen": {"weights": [1, 2, 8, 22, 18]}},
        {"type": "single_choice", "title": "How did you hear about the workshop?", "required": True,
         "options": ["Department notice", "WhatsApp group", "Friend or classmate", "College website"],
         "config": {"allow_other": True}, "gen": {"weights": [10, 22, 14, 5, 2]}},
        {"type": "multiple_choice", "title": "Which sessions were most useful to you?", "required": True,
         "description": "Choose up to three.", "options": ["Python basics refresher", "Django models", "REST APIs",
                                                            "MongoDB with PyMongo", "Deployment"],
         "config": {"max_select": 3}, "gen": {"weights": [6, 14, 18, 15, 5], "max": 3}},
        {"type": "linear_scale", "title": "How likely are you to recommend this workshop to a friend?", "required": True,
         "config": {"min": 0, "max": 10, "min_label": "Not at all likely", "max_label": "Extremely likely"},
         "gen": {"weights": [0, 0, 1, 1, 2, 3, 4, 8, 12, 9, 6]}},
        {"type": "ranking", "title": "Rank these topics for a follow-up workshop", "required": True,
         "options": ["Data analysis with pandas", "Machine learning basics", "Web scraping", "Testing and CI"],
         "gen": {"weights": [5, 7, 3, 2]}},
        {"type": "yes_no", "title": "Would you attend a follow-up workshop?", "required": True, "gen": {"weights": [5, 1]}},
        {"type": "dropdown", "title": "Which programme are you in?", "required": True,
         "options": ["MCA", "MSc Computer Science", "BCA", "BSc IT", "Other"], "gen": {"weights": [14, 5, 6, 4, 1]}},
        {"type": "number", "title": "How many years of programming experience do you have?", "required": False,
         "config": {"min_value": 0, "max_value": 30, "integer_only": True}, "gen": {"range": (0, 6)}},
        {"type": "date", "title": "Which date would suit you for the follow-up?", "required": False,
         "config": {"min_date": "2026-11-01", "max_date": "2026-12-31"}, "gen": {"dates": ["2026-11-14", "2026-11-21", "2026-11-28", "2026-12-05"]}},
        {"type": "long_text", "title": "What should we improve next time?", "required": False,
         "config": {"max_length": 1000}, "gen": {"texts": "workshop"}},
        {"type": "short_text", "title": "Describe the workshop in one word", "required": False,
         "config": {"max_length": 30}, "gen": {"texts": "one_word"}},
        {"type": "email", "title": "Email address (only if you want details of the next workshop)", "required": False,
         "gen": {"email": True}},
    ],
}

CANTEEN = {
    "title": "College Canteen Feedback",
    "description": "Help the canteen committee improve food, prices and service.",
    "status": "published", "responses": 38, "abandoned": 6,
    "settings": {"allow_edit": True},
    "questions": [
        {"type": "rating", "title": "How would you rate the food quality?", "required": True, "config": {"max": 5},
         "gen": {"weights": [3, 7, 12, 10, 4]}},
        {"type": "single_choice", "title": "How often do you eat at the canteen?", "required": True,
         "options": ["Every day", "2–3 times a week", "Once a week", "Rarely"], "gen": {"weights": [9, 14, 8, 5]}},
        {"type": "linear_scale", "title": "How reasonable are the prices?", "required": True,
         "config": {"min": 1, "max": 5, "min_label": "Too expensive", "max_label": "Very reasonable"},
         "gen": {"weights": [6, 10, 11, 7, 2]}},
        {"type": "yes_no", "title": "Is the seating area usually clean?", "required": True, "gen": {"weights": [3, 2]}},
        {"type": "long_text", "title": "Any suggestions for the canteen committee?", "required": False,
         "gen": {"texts": "canteen"}},
    ],
}

HACKATHON = {
    "title": "Hackathon Team Preferences",
    "description": "Tell us how you would like to form teams for the inter-college hackathon.",
    "status": "closed", "responses": 22, "abandoned": 3, "settings": {},
    "questions": [
        {"type": "single_choice", "title": "Preferred team size", "required": True,
         "options": ["2 members", "3 members", "4 members"], "gen": {"weights": [3, 8, 6]}},
        {"type": "multiple_choice", "title": "Which roles can you take?", "required": True,
         "options": ["Frontend", "Backend", "Design", "Presentation"], "gen": {"weights": [8, 9, 4, 5], "max": 2}},
        {"type": "yes_no", "title": "Do you already have a team?", "required": True, "gen": {"weights": [2, 3]}},
    ],
}

LIBRARY = {
    "title": "Library Opening Hours",
    "description": "Draft survey — not published yet. Open the builder to see the quality checker.",
    "status": "draft", "responses": 0, "abandoned": 0, "settings": {},
    "questions": [
        {"type": "yes_no", "title": "Don't you agree that the library should always stay open late?", "required": True},
        {"type": "single_choice", "title": "When do you usually visit the library and how long do you stay?",
         "required": True, "options": ["Morning", "Afternoon", "Evening"]},
        {"type": "rating", "title": "Rate the library", "config": {"max": 10}},
        {"type": "rating", "title": "Rate the reading room", "config": {"max": 5}},
    ],
}

TEXTS = {
    "workshop": [
        "More hands-on time for the REST API session would help.",
        "The Django models part was great, but the pace was a bit fast.",
        "Please share the slides before the session.",
        "Excellent workshop, the examples were very clear.",
        "The lab WiFi was slow and we lost time installing packages.",
        "Loved the MongoDB section. More practice exercises please.",
        "Deployment was confusing, a step-by-step handout would help.",
        "Good balance of theory and practice.",
        "Sessions started late on day two.",
        "Helpful mentors, they answered every question.",
        "The room was too crowded for the practical session.",
        "Not enough time for questions at the end.",
    ],
    "canteen": [
        "Prices went up but the quality is the same. Too expensive for students.",
        "Food is good but the queue at lunch is very long.",
        "Please add more healthy options like fruit and salads.",
        "Staff are friendly and polite.",
        "The seating area is dirty after 1 pm.",
        "Tea and snacks are great value.",
        "Waiting time is too long during the break.",
        "Not happy with the taste of the meals recently.",
        "Clean kitchen and fresh food, thank you.",
        "Add UPI payment so we do not need change.",
    ],
    "one_word": ["Useful", "Practical", "Engaging", "Fast-paced", "Informative", "Fun", "Helpful", "Intense"],
}


class Command(BaseCommand):
    help = "Create demo users, surveys and generated responses (sample data)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the demo user's surveys first.")
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password for the demo accounts.")

    def handle(self, *args, **options):
        demo = self._user("demo", "Demo Creator", "demo@example.com", options["password"])
        reviewer = self._user("reviewer", "Review Partner", "reviewer@example.com", options["password"])
        service = SurveyService(demo)
        if options["reset"]:
            for survey in service.list_surveys(status="", page_size=500)[0] + service.list_surveys(status="archived", page_size=500)[0]:
                if survey["owner_id"] == demo.id:
                    service.delete(survey["id"])
            self.stdout.write("Removed previous demo surveys.")

        rng = random.Random(15)
        for spec in (WORKSHOP, CANTEEN, HACKATHON, LIBRARY):
            questions = [{k: v for k, v in q.items() if k != "gen"} for q in spec["questions"]]
            survey_id = service.create(spec["title"], spec["description"], questions, spec["settings"])
            if spec["status"] != "draft":
                service.publish(survey_id)
            survey = service.get(survey_id)
            self._generate(survey, spec, rng)
            if spec["status"] == "closed":
                service.close(survey_id)
            if spec is CANTEEN:
                service.add_collaborator(survey_id, reviewer.username, "viewer")
            self.stdout.write(f"  + {spec['title']} [{spec['status']}] — {spec['responses']} responses")

        self.stdout.write(self.style.SUCCESS(
            f"Sample data ready. Sign in as 'demo' (or 'reviewer') with password '{options['password']}'."))

    def _user(self, username, name, email, password):
        user, created = User.objects.get_or_create(username=username, defaults={"first_name": name, "email": email})
        if created:
            user.set_password(password)  # stored as a salted hash
            user.save()
            self.stdout.write(f"Created user '{username}'.")
        return user

    # ------------------------------------------------------------------ data generation

    def _answer(self, question, gen, rng, index):
        qtype = question["type"]
        options = question.get("options", [])
        if qtype in ("single_choice", "dropdown"):
            weights = gen["weights"]
            pool = [o["oid"] for o in options] + (["__other__"] if len(weights) > len(options) else [])
            value = rng.choices(pool, weights[: len(pool)])[0]
            return {"value": value, "other_text": "Instagram page" if value == "__other__" else ""}
        if qtype == "multiple_choice":
            count = rng.randint(1, gen.get("max", 2))
            chosen = set()
            while len(chosen) < count:
                chosen.add(rng.choices([o["oid"] for o in options], gen["weights"])[0])
            return {"value": list(chosen)}
        if qtype == "rating":
            return rng.choices(range(1, len(gen["weights"]) + 1), gen["weights"])[0]
        if qtype == "linear_scale":
            low = question["config"]["min"]
            return rng.choices(range(low, low + len(gen["weights"])), gen["weights"])[0]
        if qtype == "yes_no":
            return rng.choices(["yes", "no"], gen["weights"])[0]
        if qtype == "ranking":
            ids = [o["oid"] for o in options]
            weights = dict(zip(ids, gen["weights"]))
            order = []
            while ids:  # weighted sampling without replacement
                pick = rng.choices(ids, [weights[i] for i in ids])[0]
                order.append(pick)
                ids.remove(pick)
            return {"value": order}
        if qtype == "number":
            return rng.randint(*gen["range"])
        if qtype == "date":
            return rng.choice(gen["dates"])
        if qtype in ("long_text", "short_text"):
            return rng.choice(TEXTS[gen["texts"]])
        if qtype == "email":
            return f"student{index}@example.com"
        return None

    def _generate(self, survey, spec, rng):
        gens = {q["qid"]: src.get("gen", {}) for q, src in zip(survey["questions"], spec["questions"])}
        now = utc_now()
        responses, attempts = [], []
        for index in range(spec["responses"]):
            raw = {}
            for question in survey["questions"]:
                if not question["required"] and rng.random() < 0.35:
                    continue  # optional questions are sometimes skipped
                raw[question["qid"]] = self._answer(question, gens[question["qid"]], rng, index)
            answers = validate_answers(survey, raw)  # same validation as real submissions
            submitted = now - timedelta(days=rng.randint(0, 13), hours=rng.randint(0, 10), minutes=rng.randint(0, 59))
            duration = rng.randint(70, 420)
            key = keyed_hash(f"seed:{uuid.uuid4().hex}")
            responses.append({
                "survey_id": survey["_id"], "dedupe_key": key, "respondent": None, "answers": answers,
                "consent": False, "source": "seed", "started_at": submitted - timedelta(seconds=duration),
                "submitted_at": submitted, "duration_seconds": duration, "edited_at": None,
            })
            attempts.append({"survey_id": survey["_id"], "respondent_key": key, "status": "completed",
                             "resume_token": new_token(16), "started_at": submitted - timedelta(seconds=duration),
                             "last_seen_at": submitted, "completed_at": submitted, "draft": {}})
        for _ in range(spec["abandoned"]):
            started = now - timedelta(days=rng.randint(0, 13), hours=rng.randint(1, 10))
            attempts.append({"survey_id": survey["_id"], "respondent_key": keyed_hash(uuid.uuid4().hex),
                             "status": "in_progress", "resume_token": new_token(16), "started_at": started,
                             "last_seen_at": started + timedelta(minutes=2), "draft": {}})
        if responses:
            mongo.responses().insert_many(responses)
            mongo.surveys().update_one({"_id": survey["_id"]}, {"$set": {"response_count": len(responses)}})
        if attempts:
            mongo.sessions().insert_many(attempts)
