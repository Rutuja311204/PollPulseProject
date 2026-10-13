"""Analytics calculations, text analysis, insights and the quality checker."""
from datetime import timedelta

from django.test import SimpleTestCase

from core.testing import MongoTestCase
from core.utils import keyed_hash, new_token, utc_now
from surveys import analytics, quality
from surveys.analytics import chi_square, describe_numbers, histogram, pct
from surveys.insights import build_insights
from surveys.response_service import Respondent, ResponseService
from surveys.text_analysis import analyze_texts, sentiment_label, sentiment_score

ALL_TYPES = [
    {"type": "single_choice", "title": "Language", "required": True, "options": ["Python", "Java", "C++"]},
    {"type": "multiple_choice", "title": "Tools", "options": ["Git", "Docker", "VS Code"]},
    {"type": "rating", "title": "Rate it", "config": {"max": 5}},
    {"type": "ranking", "title": "Rank", "options": ["A", "B", "C"]},
    {"type": "number", "title": "Age"},
    {"type": "long_text", "title": "Feedback"},
    {"type": "yes_no", "title": "Again?"},
    {"type": "date", "title": "When"},
    {"type": "email", "title": "Email"},
]


class MathTests(SimpleTestCase):
    def test_percentages(self):
        self.assertEqual([pct(12, 25), pct(8, 25), pct(5, 25)], [48.0, 32.0, 20.0])
        self.assertEqual(pct(3, 0), 0.0)  # no division by zero

    def test_describe_and_histogram(self):
        stats = describe_numbers([2, 4, 4, 4, 5, 5, 7, 9])
        self.assertEqual((stats["mean"], stats["median"], stats["min"], stats["max"]), (5.0, 4.5, 2, 9))
        self.assertEqual(stats["stdev"], 2.14)
        self.assertEqual(describe_numbers([])["mean"], None)
        bins = histogram([0, 1, 2, 3, 4, 5], bins=5)
        self.assertEqual(sum(b["count"] for b in bins), 6)
        self.assertEqual(bins[-1]["count"], 2)  # the maximum falls in the last bin

    def test_chi_square_known_values(self):
        result = chi_square([[10, 20], [30, 40]])
        self.assertEqual((result["chi2"], result["df"], result["p_value"]), (0.794, 1, 0.373))
        strong = chi_square([[20, 0], [0, 20]])
        self.assertLess(strong["p_value"], 0.001)
        self.assertEqual(strong["cramers_v"], 1.0)
        self.assertIsNone(chi_square([[5, 5]]))  # one row: not testable


class TextAnalysisTests(SimpleTestCase):
    def test_negation_flips_sentiment(self):
        self.assertEqual(sentiment_label(sentiment_score("The staff were good")), "positive")
        self.assertEqual(sentiment_label(sentiment_score("The staff were not good")), "negative")
        self.assertEqual(sentiment_label(sentiment_score("We met on Monday")), "neutral")

    def test_keywords_count_answers_and_group_plurals(self):
        result = analyze_texts(["Prices are high", "price too high, prices everywhere", "Great food"])
        top = {k["word"]: k["count"] for k in result["keywords"]}
        self.assertEqual(top.get("prices") or top.get("price"), 2)  # counted once per answer
        self.assertEqual(result["topics"][0]["topic"], "Price and value")
        self.assertEqual(result["count"], 3)

    def test_empty_input(self):
        result = analyze_texts(["", "   "])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["sentiment"]["positive"]["pct"], 0.0)


class ReportTests(MongoTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user()
        self.survey_id = self.make_survey(self.owner, questions=ALL_TYPES)
        self.survey = self.survey_doc(self.survey_id)
        rows = [
            ("Python", ["Git", "Docker"], 5, ["A", "B", "C"], 20, "Great course, very helpful", "yes", "2026-11-14"),
            ("Python", ["Git"], 4, ["A", "C", "B"], 22, "Too slow and noisy lab", "yes", "2026-11-14"),
            ("Java", ["Git", "VS Code"], 2, ["B", "A", "C"], 30, "Not good", "no", "2026-12-01"),
            ("Python", [], 4, ["C", "A", "B"], 25, "", "yes", ""),
        ]
        for i, row in enumerate(rows):
            self.submit_row(i, *row)

    def oid(self, number, text):
        return self.option_id(self.survey, number, text)

    def submit_row(self, i, lang, tools, rating, rank, age, text, again, when):
        raw = {
            self.qid(self.survey, 1): {"value": self.oid(1, lang)},
            self.qid(self.survey, 2): {"value": [self.oid(2, t) for t in tools]},
            self.qid(self.survey, 3): rating,
            self.qid(self.survey, 4): {"value": [self.oid(4, r) for r in rank]},
            self.qid(self.survey, 5): age,
            self.qid(self.survey, 6): text,
            self.qid(self.survey, 7): again,
            self.qid(self.survey, 8): when,
            self.qid(self.survey, 9): f"user{i}@college.edu",
        }
        ResponseService(self.survey).submit(Respondent(f"session:{i}"), raw)

    def report(self):
        return {q["number"]: q for q in analytics.survey_report(self.survey)["questions"]}

    def test_choice_counts_include_zero_options(self):
        q1 = self.report()[1]
        counts = {o["label"]: (o["count"], o["pct"]) for o in q1["options"]}
        self.assertEqual(counts, {"Python": (3, 75.0), "Java": (1, 25.0), "C++": (0, 0.0)})
        self.assertEqual(q1["mode"], ["Python"])

    def test_multiple_choice_percent_of_respondents(self):
        q2 = self.report()[2]
        self.assertEqual(q2["answered"], 3)   # the empty selection was a skip
        self.assertEqual(q2["missing"], 1)
        git = next(o for o in q2["options"] if o["label"] == "Git")
        self.assertEqual(git["pct"], 100.0)
        self.assertGreater(sum(o["pct"] for o in q2["options"]), 100)

    def test_scale_statistics(self):
        q3 = self.report()[3]
        self.assertEqual(q3["stats"]["mean"], 3.75)
        self.assertEqual(q3["stats"]["median"], 4.0)
        self.assertEqual(q3["top_two_pct"], 75.0)
        self.assertEqual([o["count"] for o in q3["options"]], [0, 1, 0, 2, 1])

    def test_ranking_scores(self):
        ranking = self.report()[4]["ranking"]
        a = next(r for r in ranking if r["label"] == "A")
        self.assertEqual(a["avg_rank"], 1.5)           # positions 1, 1, 2, 2 -> 6 / 4
        self.assertEqual(ranking[0]["label"], "A")
        self.assertEqual(a["first_place"], 2)

    def test_numeric_text_date_email(self):
        report = self.report()
        self.assertEqual(report[5]["stats"]["mean"], 24.25)
        self.assertEqual(report[6]["answered"], 3)
        self.assertEqual(report[6]["text"]["sentiment"]["negative"]["count"], 2)
        self.assertEqual((report[8]["earliest"], report[8]["latest"]), ("2026-11-14", "2026-12-01"))
        self.assertEqual(report[9]["domains"][0], {"label": "college.edu", "count": 4, "pct": 100.0})

    def test_crosstab(self):
        responses_list = analytics.load_responses(self.survey["_id"])
        table = analytics.crosstab(self.survey, responses_list, self.qid(self.survey, 1), self.qid(self.survey, 7))
        python_row = table["rows"][0]
        self.assertEqual(python_row["counts"], [3, 0])
        self.assertEqual(table["n"], 4)
        self.assertIsNone(analytics.crosstab(self.survey, responses_list, self.qid(self.survey, 1), self.qid(self.survey, 3)))

    def test_date_range_filter(self):
        self.db.responses.update_one({}, {"$set": {"submitted_at": utc_now() - timedelta(days=10)}})
        today = utc_now().date()
        report = analytics.survey_report(self.survey, today - timedelta(days=2), today + timedelta(days=1))
        self.assertEqual(report["total_responses"], 3)

    def test_completion_and_abandonment(self):
        now = utc_now()
        sessions = self.db.response_sessions
        for status, minutes_ago in (("completed", 5), ("in_progress", 120), ("in_progress", 5)):
            sessions.insert_one({"survey_id": self.survey["_id"], "respondent_key": keyed_hash(new_token()),
                                 "status": status, "resume_token": new_token(16),
                                 "started_at": now - timedelta(minutes=minutes_ago),
                                 "last_seen_at": now - timedelta(minutes=minutes_ago)})
        stats = analytics.completion_stats(self.survey["_id"])
        self.assertEqual((stats["started"], stats["completed"], stats["abandoned"], stats["in_progress"]), (3, 1, 1, 1))
        self.assertEqual(stats["completion_rate"], 33.3)

    def test_timing_excludes_resumed_outliers(self):
        stats = analytics.timing_stats([{"duration_seconds": 60}, {"duration_seconds": 120}, {"duration_seconds": 200000},
                                        {"duration_seconds": None}])
        self.assertEqual((stats["count"], stats["median_seconds"]), (2, 90))

    def test_insights_are_traceable(self):
        findings = [f["text"] for f in build_insights(analytics.survey_report(self.survey))]
        self.assertTrue(findings[0].startswith("4 responses"))
        self.assertTrue(any("“Python” (75.0%" in f for f in findings))
        self.assertTrue(any("fewer than 10" in f for f in findings))

    def test_insights_with_no_data(self):
        other = self.survey_doc(self.make_survey(self.owner, title="Empty one"))
        findings = build_insights(analytics.survey_report(other))
        self.assertEqual(len(findings), 1)
        self.assertIn("No responses", findings[0]["text"])

    def test_compare_two_surveys(self):
        second_id = self.make_survey(self.owner, questions=ALL_TYPES, title="Second run")
        second = self.survey_doc(second_id)
        ResponseService(second).submit(Respondent("session:z"), {
            second["questions"][0]["qid"]: {"value": self.option_id(second, 1, "Java")},
            second["questions"][7 - 1]["qid"]: "no"})
        result = analytics.compare_questions(self.survey, self.qid(self.survey, 1), second, second["questions"][0]["qid"])
        java = next(r for r in result["rows"] if r["label"] == "Java")
        self.assertEqual((java["a_pct"], java["b_pct"], java["diff"]), (25.0, 100.0, 75.0))
        mismatch = analytics.compare_questions(self.survey, self.qid(self.survey, 3), second, second["questions"][4]["qid"])
        self.assertIn("error", mismatch)


class QualityCheckerTests(SimpleTestCase):
    def codes(self, questions):
        from surveys.question_types import normalize_questions
        return {(i.code, i.number) for i in quality.check_survey({"questions": normalize_questions(questions)})}

    def test_rules(self):
        codes = self.codes([
            {"type": "yes_no", "title": "Don't you agree the canteen is great?"},
            {"type": "single_choice", "title": "How do you rate food and service?", "options": ["Good", "Bad", "OK"]},
            {"type": "single_choice", "title": "Pick", "options": ["Only"]},
            {"type": "single_choice", "title": "Choose", "options": ["Yes", "Yes."]},
            {"type": "rating", "title": "Rate the teacher", "config": {"max": 5}},
            {"type": "rating", "title": "Rate the teacher", "config": {"max": 10}},
            {"type": "short_text", "title": " ".join(["word"] * 30)},
            {"type": "long_text", "title": "Do you often visit the library?"},
        ])
        for expected in [("leading", 1), ("double_barrelled", 2), ("no_other", 2), ("incomplete", 3),
                         ("similar_options", 4), ("duplicate_question", 6), ("inconsistent_scales", 0),
                         ("too_long", 7), ("vague", 8)]:
            self.assertIn(expected, codes)

    def test_how_many_is_not_vague_and_numbers_scale_types_separately(self):
        codes = self.codes([
            {"type": "number", "title": "How many years have you studied?"},
            {"type": "single_choice", "title": "How often do you study?", "options": ["Daily", "Weekly"]},
            {"type": "rating", "title": "Overall rating", "config": {"max": 5}},
            {"type": "linear_scale", "title": "Recommend us?", "config": {"min": 0, "max": 10}},
        ])
        self.assertNotIn(("vague", 1), codes)
        self.assertNotIn(("vague", 2), codes)
        self.assertNotIn(("inconsistent_scales", 0), codes)

    def test_empty_survey_blocks_publishing(self):
        self.assertTrue(quality.blocking_errors({"questions": []}))
