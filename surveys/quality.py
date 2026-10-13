"""
Survey quality checker (rule-based).

Each rule looks at the question list and returns Issues:

    level "error"    -> blocks publishing (the survey would not work)
    level "warning"  -> likely problem; the creator should review it
    level "tip"      -> a suggestion

These are heuristics based on common survey-design guidance. They can be
wrong (e.g. 'and' is not always a double-barrelled question), so the UI
presents warnings and tips as recommendations, not judgments.
"""
import re
from dataclasses import asdict, dataclass

from .question_types import get_type

LONG_QUESTION_CHARS = 150
LONG_QUESTION_WORDS = 25
SIMILARITY_THRESHOLD = 0.8

LEADING_PATTERNS = [
    (r"\b(don'?t|wouldn'?t|isn'?t|aren'?t|doesn'?t|won'?t|shouldn'?t) you (agree|think|feel|like|love)\b",
     "asks for agreement ('don't you agree ...')"),
    (r"\b(agree|disagree) that\b", "states an opinion and asks the respondent to agree with it"),
    (r"\bhow (great|good|amazing|wonderful|excellent|bad|terrible|awful|useful|helpful)\b",
     "assumes a quality in the question ('how great was ...')"),
    (r"\b(obviously|clearly|of course|everyone knows|most people|surely)\b",
     "uses wording that pushes towards one answer"),
    (r"\bwhy do you (like|love|hate|dislike|prefer)\b", "assumes the respondent already holds an opinion"),
]
VAGUE_WORDS = r"\b(regularly|frequently|sometimes|rarely|recently|usually|a lot|several|occasionally)\b|(?<!how )\b(often|many|few)\b"
ABSOLUTE_WORDS = r"\b(always|never|all|every|everyone|nobody|none)\b"
DOUBLE_NEGATIVE = r"\b(not|no|never|don'?t|isn'?t)\b[^.?!]{0,25}\b(un\w+|dis\w+|not|no|never)\b"
OTHER_LIKE = re.compile(r"^(other|others|none|none of (the|these) above|n/?a|not applicable|prefer not to (say|answer)|don'?t know|something else)\b", re.I)
WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass
class Issue:
    level: str          # error | warning | tip
    code: str
    message: str
    suggestion: str = ""
    qid: str = ""
    number: int = 0     # 1-based question number (0 = whole survey)

    def as_dict(self):
        return asdict(self)


def _words(text):
    return set(WORD_RE.findall(text.lower()))


def similarity(a, b):
    """Jaccard similarity of two texts' word sets: |A ∩ B| / |A ∪ B| (0..1)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _norm_option(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def check_survey(survey):
    """Run every rule. Returns a list of Issue objects (errors first)."""
    issues = []
    questions = survey.get("questions", [])
    if not questions:
        issues.append(Issue("error", "no_questions", "The survey has no questions.", "Add at least one question."))

    scales = []
    for number, q in enumerate(questions, start=1):
        qtype = get_type(q["type"])
        title = q.get("title", "")
        lower = title.lower()
        ctx = {"qid": q["qid"], "number": number}

        # ---- completeness (blocking) ----
        for message in qtype.publish_errors(q):
            issues.append(Issue("error", "incomplete", message, "Fix this before publishing.", **ctx))

        # ---- duplicate / near-duplicate options ----
        if qtype.has_options:
            seen = {}
            for option in q.get("options", []):
                key = _norm_option(option["text"])
                if key and key in seen and seen[key].casefold() != option["text"].casefold():
                    issues.append(Issue("warning", "similar_options",
                                        f"Options “{seen[key]}” and “{option['text']}” look the same.",
                                        "Merge them or make the difference clear.", **ctx))
                seen.setdefault(key, option["text"])

        if not title:
            continue

        # ---- leading questions ----
        for pattern, reason in LEADING_PATTERNS:
            if re.search(pattern, lower):
                issues.append(Issue("warning", "leading", f"This question may be leading: it {reason}.",
                                    "Rephrase neutrally, e.g. 'How would you rate ...?'", **ctx))
                break

        # ---- ambiguity ----
        if q["type"] in ("single_choice", "dropdown", "yes_no", "rating", "linear_scale") and \
                re.search(r"\b(and|or)\b", lower) and re.search(r"\b(how|do|did|are|is|was|were|would|rate)\b", lower):
            issues.append(Issue("tip", "double_barrelled",
                                "This question may ask about two things at once ('and'/'or').",
                                "If so, split it into two questions so each answer is clear.", **ctx))
        vague = re.search(VAGUE_WORDS, lower)
        if vague:
            issues.append(Issue("tip", "vague", f"“{vague.group(0)}” can mean different things to different people.",
                                "Use a concrete time frame or amount, e.g. 'in the last 7 days'.", **ctx))
        absolute = re.search(ABSOLUTE_WORDS, lower)
        if absolute and q["type"] in ("yes_no", "single_choice"):
            issues.append(Issue("tip", "absolute", f"The absolute word “{absolute.group(0)}” makes the question hard to answer honestly.",
                                "Consider a frequency scale instead.", **ctx))
        if re.search(DOUBLE_NEGATIVE, lower):
            issues.append(Issue("warning", "double_negative", "The question may contain a double negative.",
                                "Rewrite it in a positive form.", **ctx))

        # ---- length ----
        if len(title) > LONG_QUESTION_CHARS or len(title.split()) > LONG_QUESTION_WORDS:
            issues.append(Issue("warning", "too_long",
                                f"The question is long ({len(title.split())} words).",
                                "Move background information into the description field.", **ctx))

        # ---- missing 'Other' choice ----
        if q["type"] in ("single_choice", "multiple_choice") and len(q.get("options", [])) >= 3 \
                and not q["config"].get("allow_other") \
                and not any(OTHER_LIKE.match(o["text"]) for o in q.get("options", [])):
            issues.append(Issue("tip", "no_other",
                                "Respondents whose answer is not listed have nothing to choose.",
                                "Turn on the 'Other' option or add 'None of these'.", **ctx))

        if q["type"] in ("rating", "linear_scale"):
            low, high = get_type(q["type"]).bounds(q)
            scales.append((q["type"], number, low, high))

    # ---- duplicate questions (pairwise similarity) ----
    for i, a in enumerate(questions):
        for j in range(i + 1, len(questions)):
            b = questions[j]
            if a.get("title") and b.get("title") and similarity(a["title"], b["title"]) >= SIMILARITY_THRESHOLD:
                issues.append(Issue("warning", "duplicate_question",
                                    f"Questions {i + 1} and {j + 1} are very similar.",
                                    "Remove one, or make the difference explicit.", qid=b["qid"], number=j + 1))

    # ---- inconsistent scales (compared within the same type: star ratings
    #      with star ratings, numbered scales with numbered scales) ----
    for scale_type, label in (("rating", "Star-rating"), ("linear_scale", "Linear-scale")):
        same = [(n, low, high) for t, n, low, high in scales if t == scale_type]
        if len({(low, high) for _, low, high in same}) > 1:
            ranges = ", ".join(f"Q{n}: {low}–{high}" for n, low, high in same)
            issues.append(Issue("warning", "inconsistent_scales",
                                f"{label} questions use different ranges ({ranges}).",
                                "Use one scale for the same kind of question so answers can be compared."))

    order = {"error": 0, "warning": 1, "tip": 2}
    return sorted(issues, key=lambda issue: (order[issue.level], issue.number))


def blocking_errors(survey):
    """Messages that must be fixed before publishing."""
    return [
        (f"Question {issue.number}: " if issue.number else "") + issue.message
        for issue in check_survey(survey) if issue.level == "error"
    ]


def summary(issues):
    counts = {"error": 0, "warning": 0, "tip": 0}
    for issue in issues:
        counts[issue.level] += 1
    return counts
