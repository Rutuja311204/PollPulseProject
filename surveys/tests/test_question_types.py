"""Each question type validates its own configuration and answers."""
from django.test import SimpleTestCase

from core.exceptions import ValidationFailed
from surveys.question_types import OTHER, REGISTRY, get_type, normalize_question, normalize_questions


def q(qtype, options=(), required=True, **config):
    """Build a stored-style question for tests."""
    question = normalize_question({"type": qtype, "title": "Question", "required": required,
                                   "options": list(options), "config": config}, 1, set())
    return question


class AnswerValidationTests(SimpleTestCase):
    def clean(self, question, raw):
        return get_type(question["type"]).clean_answer(question, raw)

    def assertRejected(self, question, raw, fragment=""):
        with self.assertRaises(ValidationFailed) as ctx:
            self.clean(question, raw)
        if fragment:
            self.assertIn(fragment, " ".join(ctx.exception.errors))

    def test_registry_has_twelve_distinct_types(self):
        self.assertEqual(len(REGISTRY), 12)

    def test_required_question_rejects_empty_answer(self):
        for qtype, opts in [("short_text", ()), ("single_choice", ("A", "B")), ("rating", ()), ("ranking", ("A", "B"))]:
            self.assertRejected(q(qtype, opts), None, "required")

    def test_optional_question_returns_none_when_skipped(self):
        self.assertIsNone(self.clean(q("short_text", required=False), ""))
        self.assertIsNone(self.clean(q("multiple_choice", ("A", "B"), required=False), {"value": []}))

    def test_single_choice(self):
        question = q("single_choice", ("Python", "Java"))
        oid = question["options"][0]["oid"]
        self.assertEqual(self.clean(question, {"value": oid}), {"value": oid})
        self.assertRejected(question, {"value": "o_00000000"}, "not valid")
        self.assertRejected(question, {"value": ["a", "b"]})  # a list is not a single choice

    def test_other_option_requires_text(self):
        question = q("single_choice", ("A", "B"), allow_other=True)
        self.assertRejected(question, {"value": OTHER, "other_text": ""}, "Other")
        self.assertEqual(self.clean(question, {"value": OTHER, "other_text": " Radio "}), {"value": OTHER, "other_text": "Radio"})
        self.assertRejected(q("single_choice", ("A", "B")), {"value": OTHER, "other_text": "x"})  # other not enabled

    def test_multiple_choice_limits_and_duplicates(self):
        question = q("multiple_choice", ("A", "B", "C", "D"), min_select=2, max_select=3)
        ids = [o["oid"] for o in question["options"]]
        self.assertEqual(self.clean(question, {"value": [ids[2], ids[0]]})["value"], [ids[0], ids[2]])  # stored in option order
        self.assertRejected(question, {"value": [ids[0]]}, "at least 2")
        self.assertRejected(question, {"value": ids}, "at most 3")
        self.assertRejected(question, {"value": [ids[0], ids[0]]}, "twice")

    def test_ranking_rejects_duplicated_or_missing_ranks(self):
        question = q("ranking", ("A", "B", "C"))
        a, b, c = [o["oid"] for o in question["options"]]
        self.assertEqual(self.clean(question, {"ranks": {a: "2", b: "1", c: "3"}}), {"value": [b, a, c]})
        self.assertEqual(self.clean(question, {"value": [c, b, a]}), {"value": [c, b, a]})
        self.assertRejected(question, {"ranks": {a: "1", b: "1", c: "3"}}, "exactly once")
        self.assertRejected(question, {"value": [a, a, b]}, "more than once")
        self.assertRejected(question, {"value": [a, b]}, "every option")

    def test_email(self):
        question = q("email")
        self.assertEqual(self.clean(question, " Asha@Example.COM "), {"value": "asha@example.com"})
        for bad in ("asha", "asha@", "@example.com", "a b@example.com"):
            self.assertRejected(question, bad, "valid email")

    def test_number_range_and_integer(self):
        question = q("number", min_value=0, max_value=10, integer_only=True)
        self.assertEqual(self.clean(question, "7"), {"value": 7})
        self.assertRejected(question, "11", "at most 10")
        self.assertRejected(question, "-1", "at least 0")
        self.assertRejected(question, "2.5", "whole number")
        self.assertRejected(question, "abc", "number")
        self.assertRejected(question, True, "number")

    def test_rating_and_linear_scale_ranges(self):
        rating = q("rating", max=5)
        self.assertEqual(self.clean(rating, "5"), {"value": 5})
        self.assertRejected(rating, "6", "1 to 5")
        self.assertRejected(rating, "0", "1 to 5")
        scale = q("linear_scale", min=0, max=10)
        self.assertEqual(self.clean(scale, 0), {"value": 0})
        self.assertRejected(scale, 11)

    def test_date_window(self):
        question = q("date", min_date="2026-01-01", max_date="2026-12-31")
        self.assertEqual(self.clean(question, "2026-05-01"), {"value": "2026-05-01"})
        self.assertRejected(question, "2027-01-01", "on or before")
        self.assertRejected(question, "31/12/2026", "valid date")

    def test_text_length_limits(self):
        question = q("short_text", min_length=3, max_length=12)
        self.assertEqual(self.clean(question, "  hello   world "), {"value": "hello world"})
        self.assertRejected(question, "hi", "at least 3")
        self.assertRejected(question, "x" * 13, "at most 12")
        long_text = q("long_text", max_length=50)
        self.assertEqual(self.clean(long_text, "line one\nline two")["value"], "line one\nline two")

    def test_yes_no(self):
        question = q("yes_no")
        self.assertEqual(self.clean(question, "YES"), {"value": "yes"})
        self.assertRejected(question, "maybe")

    def test_dropdown(self):
        question = q("dropdown", ("MCA", "MSc"))
        self.assertEqual(self.clean(question, {"value": question["options"][1]["oid"]})["value"], question["options"][1]["oid"])
        self.assertRejected(question, {"value": "MCA"})  # option text is not an option id


class BuilderNormalisationTests(SimpleTestCase):
    def test_config_errors_are_reported(self):
        cases = [
            ({"type": "multiple_choice", "options": ["A", "B"], "config": {"min_select": 3, "max_select": 2}}, "Minimum selections"),
            ({"type": "short_text", "config": {"min_length": 50, "max_length": 10}}, "Minimum length"),
            ({"type": "number", "config": {"min_value": 10, "max_value": 1}}, "Minimum value"),
            ({"type": "rating", "config": {"max": 11}}, "between 3 and 10"),
            ({"type": "linear_scale", "config": {"min": 2, "max": 5}}, "between 0 and 1"),
            ({"type": "date", "config": {"min_date": "2026-12-01", "max_date": "2026-01-01"}}, "before"),
        ]
        for raw, fragment in cases:
            with self.assertRaises(ValidationFailed, msg=raw) as ctx:
                normalize_question(raw, 1, set())
            self.assertIn(fragment, " ".join(ctx.exception.errors))

    def test_ids_are_kept_or_generated(self):
        first = normalize_question({"type": "single_choice", "title": "Q", "options": ["A", "B", ""]}, 1, set())
        self.assertRegex(first["qid"], r"^q_[a-z0-9]{8}$")
        self.assertEqual(len(first["options"]), 2)  # empty option row dropped
        again = normalize_question(first, 1, set())
        self.assertEqual(again["qid"], first["qid"])
        self.assertEqual(again["options"], first["options"])

    def test_duplicate_qids_get_new_ids(self):
        q1 = normalize_question({"type": "yes_no", "title": "A"}, 1, set())
        questions = normalize_questions([q1, dict(q1, title="B")])
        self.assertNotEqual(questions[0]["qid"], questions[1]["qid"])

    def test_unknown_type_and_too_many_questions(self):
        with self.assertRaises(ValidationFailed):
            normalize_questions([{"type": "video", "title": "x"}])
        with self.assertRaises(ValidationFailed):
            normalize_questions([{"type": "yes_no", "title": str(i)} for i in range(51)])
