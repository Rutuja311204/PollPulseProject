"""
Insight summary — short findings generated from aggregated numbers.

Every sentence is produced by a fixed rule from the analytics report, so it
can be traced back to a number on the analytics page. Nothing is invented:
if there is no data, the summary says so. (An optional AI service can
rephrase these sentences; see ai.py. It only ever receives these facts.)
"""
SMALL_SAMPLE = 10
LOW_PARTICIPATION = 70.0


def _fmt_seconds(seconds):
    if seconds is None:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def _join(labels):
    labels = [f"“{label}”" for label in labels]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def question_insight(q):
    """One sentence for one analysed question, or None if nothing to say."""
    title = f"Q{q['number']} “{q['title'] or 'Untitled'}”"
    if q["answered"] == 0:
        return None
    if q["kind"] in ("choice", "multi"):
        options = sorted(q["options"], key=lambda o: -o["count"])
        leaders = q["mode"]
        if len(leaders) > 1:
            return f"{title}: {_join(leaders)} were equally common ({options[0]['pct']}% each)."
        first = options[0]
        second = options[1] if len(options) > 1 else None
        share = "a majority" if first["pct"] > 50 and q["kind"] == "choice" else f"{first['pct']}%"
        sentence = f"{title}: the most common answer was “{first['label']}” ({share} of {q['answered']} respondents"
        if share == "a majority":
            sentence = sentence.replace("a majority of", f"{first['pct']}%, a majority of")
        if second and second["count"] and first["pct"] - second["pct"] < 5:
            sentence += f"), narrowly ahead of “{second['label']}” ({second['pct']}%)."
        else:
            sentence += ")."
        return sentence
    if q["kind"] == "scale":
        s = q["stats"]
        return (f"{title}: average {s['mean']} on a {q['low']}–{q['high']} scale (median {s['median']}); "
                f"{q['top_two_pct']}% chose one of the top two values.")
    if q["kind"] == "ranking":
        top = q["ranking"][0]
        return (f"{title}: “{top['label']}” had the best average rank ({top['avg_rank']}) and was ranked first "
                f"by {top['first_place_pct']}% of respondents.")
    if q["kind"] == "numeric":
        s = q["stats"]
        return f"{title}: answers ranged from {s['min']} to {s['max']} (median {s['median']}, mean {s['mean']})."
    if q["kind"] == "text":
        t = q["text"]
        if not t["keywords"]:
            return f"{title}: {t['count']} written answers."
        words = ", ".join(k["word"] for k in t["keywords"][:3])
        s = t["sentiment"]
        return (f"{title}: {t['count']} written answers; frequent words: {words}. Estimated tone "
                f"(word-list method): {s['positive']['pct']}% positive, {s['negative']['pct']}% negative.")
    if q["kind"] == "date" and q.get("earliest"):
        return f"{title}: dates range from {q['earliest']} to {q['latest']}."
    return None


def build_insights(report, max_questions=8):
    """Return a list of {"kind", "text"} findings for an analytics report."""
    findings = []
    n = report["total_responses"]
    if n == 0:
        return [{"kind": "volume", "text": "No responses have been collected in this period yet."}]

    first, last = report["first_response_at"], report["last_response_at"]
    span = f"between {first:%d %b %Y} and {last:%d %b %Y}" if first.date() != last.date() else f"on {first:%d %b %Y}"
    findings.append({"kind": "volume", "text": f"{n} response{'s' if n != 1 else ''} were collected {span}."})
    if n < SMALL_SAMPLE:
        findings.append({"kind": "caution", "text": f"With fewer than {SMALL_SAMPLE} responses, treat the percentages below as indicative only."})

    peak = report.get("peak_day")
    active_days = sum(1 for d in report["trend"] if d["count"])
    if peak and active_days > 1:
        findings.append({"kind": "trend", "text": f"The busiest day was {peak['label']} with {peak['count']} responses."})

    completion = report["completion"]
    if completion["started"] >= 5 and completion["completion_rate"] is not None:
        findings.append({"kind": "completion", "text": (
            f"{completion['completion_rate']}% of visits that opened the survey ended in a submission "
            f"({completion['completed']} of {completion['started']}).")})
    timing = report["timing"]
    if timing["count"] >= 3:
        findings.append({"kind": "timing", "text": f"Respondents typically needed {_fmt_seconds(timing['median_seconds'])} to finish (median)."})

    for q in report["questions"][:max_questions]:
        sentence = question_insight(q)
        if sentence:
            findings.append({"kind": "question", "text": sentence})

    low = [q for q in report["questions"] if q["participation_pct"] < LOW_PARTICIPATION and not q["required"]]
    if low:
        names = ", ".join(f"Q{q['number']} ({q['participation_pct']}%)" for q in low[:4])
        findings.append({"kind": "participation", "text": f"Optional questions with low participation: {names}. Check whether they are clear and necessary."})
    return findings
