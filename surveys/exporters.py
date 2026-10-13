"""
CSV exports (Python's csv module).

1. responses_csv  – one row per response, one column per question
2. analytics_csv  – aggregated numbers per question/option + insight summary

Safety
------
* Spreadsheet formula injection: a cell that starts with = + - @ (or a tab
  / carriage return) can be executed as a formula by Excel. `safe_cell`
  prefixes such text with an apostrophe so it is shown as plain text.
  Numbers we generated ourselves are written as numbers.
* Encoding: files are UTF-8 with a byte-order mark so Excel shows
  non-English characters correctly; csv.writer handles quoting of commas,
  quotes and line breaks.
* Authorization is checked in the view before these functions are called.
"""
import csv
from datetime import date

from django.utils import timezone as dj_timezone

from core.utils import as_aware, slugify_filename

from .question_types import get_type

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.startswith(DANGEROUS_PREFIXES):
        return "'" + text
    return text


def _local(dt):
    return dj_timezone.localtime(as_aware(dt)).strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def filename(survey, kind):
    return f"pollpulse_{slugify_filename(survey['title'])}_{kind}_{date.today().isoformat()}.csv"


def write_bom(file_obj):
    file_obj.write("﻿")


def responses_csv(survey, responses_list, file_obj):
    writer = csv.writer(file_obj)
    identified = survey["settings"]["identity"] == "identified"
    questions = survey["questions"]
    header = ["Response no.", "Submitted at", "Edited at", "Time taken (seconds)", "Source"]
    if identified:
        header.append("Respondent username")
    header += [f"Q{i}. {q['title']}" for i, q in enumerate(questions, start=1)]
    writer.writerow([safe_cell(h) for h in header])

    for number, response in enumerate(responses_list, start=1):
        answers = {a["qid"]: a for a in response.get("answers", [])}
        row = [number, _local(response.get("submitted_at")), _local(response.get("edited_at")),
               response.get("duration_seconds"), response.get("source", "web")]
        if identified:
            row.append(safe_cell((response.get("respondent") or {}).get("username", "")))
        for q in questions:
            answer = answers.get(q["qid"])
            if answer is None:
                row.append("")
                continue
            if q["type"] in ("number", "rating", "linear_scale"):
                row.append(answer["value"])
            else:
                row.append(safe_cell(get_type(q["type"]).display(q, answer)))
        writer.writerow(row)


def analytics_csv(report, insights, file_obj):
    writer = csv.writer(file_obj)
    writer.writerow(["PollPulse analytics export"])
    writer.writerow(["Survey", safe_cell(report["title"])])
    period = f"{report['period']['start'] or 'first response'} to {report['period']['end'] or 'today'}"
    writer.writerow(["Period", period])
    writer.writerow(["Total responses", report["total_responses"]])
    c = report["completion"]
    writer.writerow(["Started attempts", c["started"]])
    writer.writerow(["Completion rate (%)", "" if c["completion_rate"] is None else c["completion_rate"]])
    median = report["timing"]["median_seconds"]
    writer.writerow(["Median completion time (s)", "" if median is None else median])
    writer.writerow([])
    writer.writerow(["Question no.", "Question", "Type", "Answered", "Missing", "Item", "Count", "Percent of answerers",
                     "Mean", "Median", "Min", "Max"])
    for q in report["questions"]:
        base = [q["number"], safe_cell(q["title"]), q["type_label"], q["answered"], q["missing"]]
        if q["kind"] in ("choice", "multi", "scale"):
            stats = q.get("stats", {})
            for option in q["options"]:
                writer.writerow(base + [safe_cell(option["label"]), option["count"], option["pct"],
                                        stats.get("mean", ""), stats.get("median", ""), stats.get("min", ""), stats.get("max", "")])
        elif q["kind"] == "numeric":
            s = q["stats"]
            writer.writerow(base + ["(all answers)", s["count"], "", s["mean"], s["median"], s["min"], s["max"]])
        elif q["kind"] == "ranking":
            for row in q["ranking"]:
                writer.writerow(base + [safe_cell(row["label"]), row["first_place"], row["first_place_pct"],
                                        row["avg_rank"], "", "", ""])
        elif q["kind"] == "text":
            for keyword in q["text"]["keywords"]:
                writer.writerow(base + [safe_cell(f"keyword: {keyword['word']}"), keyword["count"], keyword["pct"], "", "", "", ""])
            if not q["text"]["keywords"]:
                writer.writerow(base + ["(text answers)", q["answered"], "", "", "", "", ""])
        elif q["kind"] == "date":
            for month in q["by_month"]:
                writer.writerow(base + [month["label"], month["count"], "", "", "", "", ""])
        elif q["kind"] == "email":
            for domain in q["domains"]:
                writer.writerow(base + [safe_cell(f"domain: {domain['label']}"), domain["count"], domain["pct"], "", "", "", ""])
    writer.writerow([])
    writer.writerow(["Summary (rule-based)"])
    for finding in insights:
        writer.writerow([safe_cell(finding["text"])])
