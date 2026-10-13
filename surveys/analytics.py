"""
Analytics engine — every number is computed from stored responses.

Definitions used throughout (also shown in the UI):

* Responses            number of submitted response documents in the period.
* Answered (question)  responses that contain an answer to that question.
* Missing  (question)  responses - answered (optional question skipped).
* Participation        answered / responses x 100.
* Option %             option count / answered x 100  -> "% of respondents who
                       answered this question". For multiple choice these add
                       up to more than 100 % because one person picks several.
* Started attempts     visits that opened the survey page (response_sessions).
* Completion rate      completed attempts / started attempts x 100.
* Abandonment rate     abandoned attempts / started attempts x 100, where an
                       attempt is "abandoned" if it is still unfinished and
                       had no activity for ABANDON_AFTER_MINUTES.
* Completion time      submitted_at - started_at for responses with a recorded
                       start. Times over 24 hours (left and resumed later) are
                       excluded; we report the median (robust) and the mean.

Method: one streaming pass over the period's responses in Python
(O(responses x questions)), which keeps every calculation readable.
"""
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from django.utils import timezone as dj_timezone

from core import mongo
from core.utils import as_aware, utc_now

from .question_types import CHOICE_LIKE, OTHER, get_type, option_labels
from .text_analysis import analyze_texts

ABANDON_AFTER_MINUTES = 30
MAX_DURATION_SECONDS = 24 * 3600


def pct(part, whole, digits=1):
    """Safe percentage: returns 0.0 instead of dividing by zero."""
    return round(part * 100 / whole, digits) if whole else 0.0


def local_tz():
    return dj_timezone.get_current_timezone()


def day_bounds(start_date, end_date):
    """Convert local calendar dates into an aware [start, end) datetime range."""
    tz = local_tz()
    start = datetime.combine(start_date, time.min, tzinfo=tz) if start_date else None
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz) if end_date else None
    return start, end


def load_responses(survey_oid, start=None, end=None):
    query = {"survey_id": survey_oid}
    if start or end:
        query["submitted_at"] = {}
        if start:
            query["submitted_at"]["$gte"] = start
        if end:
            query["submitted_at"]["$lt"] = end
    cursor = mongo.responses().find(query).sort("submitted_at", 1)
    return mongo.db_call(list, cursor)


def answers_by_qid(response):
    return {a["qid"]: a for a in response.get("answers", [])}


# --------------------------------------------------------------------------
# Small statistics helpers
# --------------------------------------------------------------------------

def describe_numbers(values):
    """Count, mean, median, min, max and sample standard deviation."""
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 2),
        "median": round(statistics.median(values), 2),
        "min": min(values),
        "max": max(values),
        "stdev": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
    }


def histogram(values, bins=6):
    """Equal-width bins between min and max: [{label, count}]."""
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        return [{"label": f"{low:g}", "count": len(values)}]
    width = (high - low) / bins
    counts = [0] * bins
    for v in values:
        index = min(int((v - low) / width), bins - 1)  # max value goes in the last bin
        counts[index] += 1
    return [{"label": f"{low + i * width:.4g}–{low + (i + 1) * width:.4g}", "count": c} for i, c in enumerate(counts)]


def modes(counts_by_label):
    """Most common label(s); a tie returns all of them. Empty if no answers."""
    if not counts_by_label or max(counts_by_label.values()) == 0:
        return []
    top = max(counts_by_label.values())
    return [label for label, count in counts_by_label.items() if count == top]


# --------------------------------------------------------------------------
# Per-question analysis (one function per analysis kind)
# --------------------------------------------------------------------------

def _choice(question, answers):
    counter = Counter(a["value"] for a in answers)
    answered = len(answers)
    labels = option_labels(question)
    options = [{"key": key, "label": label, "count": counter.get(key, 0), "pct": pct(counter.get(key, 0), answered)}
               for key, label in labels]
    return {
        "options": options,
        "mode": modes({o["label"]: o["count"] for o in options}),
        "other_texts": [a.get("other_text", "") for a in answers if a["value"] == OTHER][-20:],
        "chart": "choice",
    }


def _multi(question, answers):
    counter = Counter(v for a in answers for v in a["value"])
    answered = len(answers)
    options = [{"key": key, "label": label, "count": counter.get(key, 0), "pct": pct(counter.get(key, 0), answered)}
               for key, label in option_labels(question)]
    selections = sum(len(a["value"]) for a in answers)
    return {
        "options": options,
        "mode": modes({o["label"]: o["count"] for o in options}),
        "total_selections": selections,
        "avg_selections": round(selections / answered, 2) if answered else 0,
        "other_texts": [a.get("other_text", "") for a in answers if OTHER in a["value"]][-20:],
        "chart": "choice",
        "multi_note": "Percentages are of respondents who answered; they add up to more than 100% because several options can be chosen.",
    }


def _scale(question, answers):
    low, high = get_type(question["type"]).bounds(question)
    values = [a["value"] for a in answers]
    counter = Counter(values)
    answered = len(values)
    distribution = [{"key": v, "label": str(v), "count": counter.get(v, 0), "pct": pct(counter.get(v, 0), answered)}
                    for v in range(low, high + 1)]
    top_two = sum(counter.get(v, 0) for v in (high, high - 1))
    stats = describe_numbers(values)
    return {
        "low": low, "high": high,
        "options": distribution,
        "stats": stats,
        "mode": modes({str(v): counter.get(v, 0) for v in range(low, high + 1)}),
        "top_two_pct": pct(top_two, answered),
        "min_label": question["config"].get("min_label", ""),
        "max_label": question["config"].get("max_label", ""),
        "chart": "scale",
    }


def _numeric(question, answers):
    values = [a["value"] for a in answers]
    return {"stats": describe_numbers(values), "histogram": histogram(values), "chart": "histogram"}


def _date(question, answers):
    values = sorted(a["value"] for a in answers)
    months = Counter(v[:7] for v in values)
    return {
        "earliest": values[0] if values else None,
        "latest": values[-1] if values else None,
        "by_month": [{"label": m, "count": c} for m, c in sorted(months.items())],
        "mode": modes(Counter(values)) if values else [],
        "chart": "months",
    }


def _ranking(question, answers):
    """Average rank (1 = best), first-place votes and a Borda score.

    Borda: with n options, rank 1 earns n points, rank 2 earns n-1 ... The
    total is divided by the maximum possible (n x answers) to give 0-100.
    """
    options = question.get("options", [])
    n = len(options)
    positions = defaultdict(list)
    for a in answers:
        for position, oid in enumerate(a["value"], start=1):
            positions[oid].append(position)
    answered = len(answers)
    rows = []
    for option in options:
        ranks = positions.get(option["oid"], [])
        borda = sum(n - r + 1 for r in ranks)
        rows.append({
            "key": option["oid"], "label": option["text"],
            "avg_rank": round(statistics.fmean(ranks), 2) if ranks else None,
            "first_place": ranks.count(1),
            "first_place_pct": pct(ranks.count(1), answered),
            "score": pct(borda, n * answered) if answered else 0.0,
        })
    rows.sort(key=lambda r: (r["avg_rank"] is None, r["avg_rank"] or 0))
    return {"ranking": rows, "mode": [rows[0]["label"]] if answered and rows else [], "chart": "ranking"}


def _text(question, answers, include_samples=True):
    texts = [a["value"] for a in answers]
    result = {"text": analyze_texts(texts), "chart": "text"}
    if include_samples:
        result["samples"] = texts[-10:][::-1]  # newest first
    return result


def _email(question, answers):
    domains = Counter(a["value"].split("@")[-1] for a in answers)
    return {"domains": [{"label": d, "count": c, "pct": pct(c, len(answers))} for d, c in domains.most_common(5)],
            "chart": "domains"}


ANALYZERS = {"choice": _choice, "multi": _multi, "scale": _scale, "numeric": _numeric,
             "date": _date, "ranking": _ranking, "text": _text, "email": _email}


def analyze_question(question, number, responses_list):
    total = len(responses_list)
    answers = [a for r in responses_list for a in r.get("answers", []) if a["qid"] == question["qid"]]
    qtype = get_type(question["type"])
    result = {
        "qid": question["qid"], "number": number, "title": question["title"],
        "type": question["type"], "type_label": qtype.label, "kind": qtype.analysis,
        "required": question.get("required", False),
        "answered": len(answers), "missing": total - len(answers),
        "participation_pct": pct(len(answers), total),
    }
    result.update(ANALYZERS[qtype.analysis](question, answers))
    return result


# --------------------------------------------------------------------------
# Survey-level metrics
# --------------------------------------------------------------------------

def completion_stats(survey_oid, start=None, end=None, now=None):
    """Started / completed / abandoned attempts within the period."""
    now = now or utc_now()
    query = {"survey_id": survey_oid}
    if start or end:
        query["started_at"] = {}
        if start:
            query["started_at"]["$gte"] = start
        if end:
            query["started_at"]["$lt"] = end
    cursor = mongo.sessions().find(query, {"status": 1, "last_seen_at": 1, "last_saved_at": 1, "started_at": 1})
    started = completed = abandoned = active = 0
    cutoff = now - timedelta(minutes=ABANDON_AFTER_MINUTES)
    for doc in mongo.db_call(list, cursor):
        started += 1
        if doc["status"] == "completed":
            completed += 1
        else:
            last = max(filter(None, [as_aware(doc.get("last_seen_at")), as_aware(doc.get("last_saved_at")),
                                     as_aware(doc.get("started_at"))]))
            if last < cutoff:
                abandoned += 1
            else:
                active += 1
    return {
        "started": started, "completed": completed, "abandoned": abandoned, "in_progress": active,
        "completion_rate": pct(completed, started) if started else None,
        "abandonment_rate": pct(abandoned, started) if started else None,
    }


def timing_stats(responses_list):
    durations = [r["duration_seconds"] for r in responses_list
                 if r.get("duration_seconds") is not None and 0 <= r["duration_seconds"] <= MAX_DURATION_SECONDS]
    if not durations:
        return {"count": 0, "median_seconds": None, "mean_seconds": None}
    return {"count": len(durations), "median_seconds": round(statistics.median(durations)),
            "mean_seconds": round(statistics.fmean(durations))}


def daily_trend(responses_list, start_date=None, end_date=None):
    """Responses per local calendar day, including days with zero."""
    tz = local_tz()
    days = Counter(as_aware(r["submitted_at"]).astimezone(tz).date() for r in responses_list)
    if not start_date:
        start_date = min(days) if days else datetime.now(tz).date() - timedelta(days=13)
    if not end_date:
        end_date = datetime.now(tz).date()
    span = (end_date - start_date).days
    if span > 366:  # keep charts readable
        start_date = end_date - timedelta(days=366)
        span = 366
    return [{"date": (start_date + timedelta(days=i)).isoformat(),
             "label": (start_date + timedelta(days=i)).strftime("%d %b"),
             "count": days.get(start_date + timedelta(days=i), 0)} for i in range(span + 1)]


def survey_report(survey, start_date=None, end_date=None, include_samples=True):
    """Everything the analytics page, the API and the exports need."""
    start, end = day_bounds(start_date, end_date)
    responses_list = load_responses(survey["_id"], start, end)
    questions = [analyze_question(q, i, responses_list) for i, q in enumerate(survey["questions"], start=1)]
    if not include_samples:
        for q in questions:
            q.pop("samples", None)
            q.pop("other_texts", None)
    peak = None
    trend = daily_trend(responses_list, start_date, end_date)
    if responses_list:
        peak = max(trend, key=lambda d: d["count"])
    return {
        "survey_id": str(survey["_id"]),
        "title": survey["title"],
        "status": survey["status"],
        "period": {"start": start_date.isoformat() if start_date else None,
                   "end": end_date.isoformat() if end_date else None},
        "total_responses": len(responses_list),
        "first_response_at": as_aware(responses_list[0]["submitted_at"]) if responses_list else None,
        "last_response_at": as_aware(responses_list[-1]["submitted_at"]) if responses_list else None,
        "completion": completion_stats(survey["_id"], start, end),
        "timing": timing_stats(responses_list),
        "trend": trend,
        "peak_day": peak,
        "questions": questions,
    }


# --------------------------------------------------------------------------
# Cross-tabulation with a chi-square test of independence
# --------------------------------------------------------------------------

def _gamma_q(a, x):
    """Regularized upper incomplete gamma Q(a, x) (Numerical Recipes method).
    The chi-square p-value is Q(df / 2, chi2 / 2)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:  # series expansion for P, then Q = 1 - P
        term = total = 1.0 / a
        ap = a
        for _ in range(500):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-12:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - gln))
    b = x + 1 - a  # continued fraction for Q
    c, d = 1 / 1e-300, 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - gln) * h)


def chi_square(table):
    """Chi-square test of independence for a table of counts (list of rows).

    expected[i][j] = row_total[i] x col_total[j] / grand_total
    chi2 = sum((observed - expected)^2 / expected)
    df = (rows - 1) x (columns - 1), using only non-empty rows/columns
    Cramér's V = sqrt(chi2 / (n x (min(rows, cols) - 1))) measures strength 0..1
    """
    rows = [r for r in table if sum(r) > 0]
    if not rows:
        return None
    col_totals = [sum(col) for col in zip(*rows)]
    keep = [j for j, total in enumerate(col_totals) if total > 0]
    rows = [[r[j] for j in keep] for r in rows]
    col_totals = [col_totals[j] for j in keep]
    row_totals = [sum(r) for r in rows]
    n = sum(row_totals)
    r_count, c_count = len(rows), len(col_totals)
    if r_count < 2 or c_count < 2:
        return None
    chi2, low_expected = 0.0, 0
    for i in range(r_count):
        for j in range(c_count):
            expected = row_totals[i] * col_totals[j] / n
            if expected < 5:
                low_expected += 1
            chi2 += (rows[i][j] - expected) ** 2 / expected
    df = (r_count - 1) * (c_count - 1)
    return {
        "chi2": round(chi2, 3), "df": df,
        "p_value": round(_gamma_q(df / 2, chi2 / 2), 4),
        "cramers_v": round(math.sqrt(chi2 / (n * (min(r_count, c_count) - 1))), 3),
        "n": n,
        "reliable": low_expected == 0,
        "low_expected_cells": low_expected,
    }


def crosstab(survey, responses_list, qid_rows, qid_cols):
    """Counts of respondents for every (row answer, column answer) pair.
    Only responses that answered BOTH questions are included."""
    questions = {q["qid"]: q for q in survey["questions"]}
    qa, qb = questions.get(qid_rows), questions.get(qid_cols)
    if not qa or not qb or qa["type"] not in CHOICE_LIKE or qb["type"] not in CHOICE_LIKE or qid_rows == qid_cols:
        return None
    row_labels, col_labels = option_labels(qa), option_labels(qb)
    row_index = {key: i for i, (key, _) in enumerate(row_labels)}
    col_index = {key: j for j, (key, _) in enumerate(col_labels)}
    table = [[0] * len(col_labels) for _ in row_labels]
    for response in responses_list:
        answers = answers_by_qid(response)
        a, b = answers.get(qid_rows), answers.get(qid_cols)
        if a and b and a["value"] in row_index and b["value"] in col_index:
            table[row_index[a["value"]]][col_index[b["value"]]] += 1
    row_totals = [sum(r) for r in table]
    n = sum(row_totals)
    return {
        "rows_question": qa["title"], "cols_question": qb["title"],
        "col_labels": [label for _, label in col_labels],
        "rows": [{"label": label, "counts": table[i], "total": row_totals[i],
                  "row_pct": [pct(c, row_totals[i]) for c in table[i]]} for i, (_, label) in enumerate(row_labels)],
        "col_totals": [sum(col) for col in zip(*table)] if table else [],
        "n": n,
        "test": chi_square(table),
    }


# --------------------------------------------------------------------------
# Comparison between two surveys (or two questions)
# --------------------------------------------------------------------------

COMPARABLE = {"choice", "multi", "scale", "numeric"}


def compare_questions(survey_a, qid_a, survey_b, qid_b, start_date=None, end_date=None):
    """Compare one question from survey A with one from survey B.

    Choice questions: options are matched by their text (case-insensitive),
    and we report each side's % of respondents and the difference in
    percentage points (B - A). Scale/number questions: mean and median.
    """
    def find(survey, qid):
        for number, q in enumerate(survey["questions"], start=1):
            if q["qid"] == qid:
                return q, number
        return None, None

    qa, na = find(survey_a, qid_a)
    qb, nb = find(survey_b, qid_b)
    if not qa or not qb:
        return {"error": "Choose a question from each survey."}
    kind_a, kind_b = get_type(qa["type"]).analysis, get_type(qb["type"]).analysis
    if kind_a not in COMPARABLE or kind_b not in COMPARABLE:
        return {"error": "Only choice, scale and number questions can be compared."}
    if {kind_a, kind_b} - {"choice", "multi"} and kind_a != kind_b:
        return {"error": "Both questions must be the same kind (choice with choice, scale with scale ...)."}

    start, end = day_bounds(start_date, end_date)
    ra = analyze_question(qa, na, load_responses(survey_a["_id"], start, end))
    rb = analyze_question(qb, nb, load_responses(survey_b["_id"], start, end))
    result = {"a": ra, "b": rb, "kind": kind_a}
    if kind_a in ("choice", "multi"):
        labels, rows = [], {}
        for side, data in (("a", ra), ("b", rb)):
            for option in data["options"]:
                key = option["label"].casefold()
                if key not in rows:
                    labels.append(key)
                    rows[key] = {"label": option["label"], "a_count": 0, "a_pct": 0.0, "b_count": 0, "b_pct": 0.0}
                rows[key][f"{side}_count"] = option["count"]
                rows[key][f"{side}_pct"] = option["pct"]
        for row in rows.values():
            row["diff"] = round(row["b_pct"] - row["a_pct"], 1)
        result["rows"] = [rows[k] for k in labels]
    else:
        sa, sb = ra["stats"], rb["stats"]
        result["diff_mean"] = round(sb["mean"] - sa["mean"], 2) if None not in (sa["mean"], sb["mean"]) else None
        if kind_a == "scale" and (ra["low"], ra["high"]) != (rb["low"], rb["high"]):
            result["warning"] = "The two questions use different scales, so their averages are not directly comparable."
    return result
