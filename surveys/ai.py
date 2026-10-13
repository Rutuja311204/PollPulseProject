"""
Optional AI assistance + the built-in fallbacks that always work.

Configuration (.env):
    AI_API_BASE   e.g. https://api.openai.com/v1  or  http://localhost:11434/v1 (Ollama)
    AI_API_KEY    the provider's key (leave empty for local servers that need none)
    AI_MODEL      model name, e.g. gpt-4o-mini or llama3.1
AI is enabled only when AI_API_BASE and AI_MODEL are set. The client speaks
the widely used "OpenAI-compatible" Chat Completions format via Python's
built-in urllib, so no extra package is required.

Privacy: question suggestions send only the topic/objective the creator
typed; insight rewriting sends only the aggregated findings — never raw
respondent answers.

Without configuration, PollPulse uses:
  * template-based question suggestions (TEMPLATE_BANK below), labelled as such
  * rule-based insight sentences (insights.py)
"""
import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings

from core.exceptions import ValidationFailed
from core.utils import normalize_text

from .question_types import REGISTRY, normalize_question

logger = logging.getLogger(__name__)
MAX_SUGGESTIONS = 8


class AIUnavailable(Exception):
    pass


def is_configured():
    return bool(getattr(settings, "AI_API_BASE", "") and getattr(settings, "AI_MODEL", ""))


def provider_label():
    return f"{settings.AI_MODEL} via {settings.AI_API_BASE}" if is_configured() else "Not configured"


def chat(system, user, max_tokens=900):
    """Send one chat request; return the reply text or raise AIUnavailable."""
    if not is_configured():
        raise AIUnavailable("No AI service is configured.")
    body = json.dumps({
        "model": settings.AI_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if settings.AI_API_KEY:
        headers["Authorization"] = f"Bearer {settings.AI_API_KEY}"
    url = settings.AI_API_BASE.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=settings.AI_TIMEOUT) as response:
            data = json.loads(response.read().decode())
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        logger.warning("AI request failed: %s", exc)
        raise AIUnavailable("The AI service did not respond correctly.") from exc


def _extract_json(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON object in reply")
    return json.loads(match.group(0))


# --------------------------------------------------------------------------
# Question suggestions
# --------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    "event": ("event", "workshop", "seminar", "conference", "webinar", "fest", "festival", "session", "hackathon", "meetup"),
    "course": ("course", "class", "lecture", "teaching", "training", "subject", "faculty", "curriculum", "semester", "module"),
    "product": ("product", "app", "website", "software", "feature", "platform", "tool", "portal"),
    "service": ("customer", "client", "restaurant", "store", "shop", "canteen", "food", "hotel", "service", "delivery", "hospital"),
    "workplace": ("employee", "staff", "team", "workplace", "manager", "office", "hr", "work"),
}

TEMPLATE_BANK = {
    "event": [
        {"type": "rating", "title": "Overall, how would you rate {topic}?", "config": {"max": 5}},
        {"type": "single_choice", "title": "How did you hear about {topic}?", "options": ["Email", "Social media", "Friend or colleague", "Notice board"], "config": {"allow_other": True}},
        {"type": "multiple_choice", "title": "Which parts of {topic} were most useful to you?", "options": ["Talks", "Hands-on activities", "Networking", "Q&A"], "config": {"allow_other": True, "max_select": 3}},
        {"type": "linear_scale", "title": "How likely are you to attend a similar event again?", "config": {"min": 0, "max": 10, "min_label": "Not likely", "max_label": "Very likely"}},
        {"type": "long_text", "title": "What one change would improve {topic}?"},
    ],
    "course": [
        {"type": "rating", "title": "How would you rate {topic} overall?", "config": {"max": 5}},
        {"type": "linear_scale", "title": "How clear were the explanations?", "config": {"min": 1, "max": 5, "min_label": "Not clear", "max_label": "Very clear"}},
        {"type": "single_choice", "title": "How was the pace of {topic}?", "options": ["Too slow", "About right", "Too fast"]},
        {"type": "ranking", "title": "Rank these learning methods from most to least helpful", "options": ["Lectures", "Practical sessions", "Assignments", "Group discussion"]},
        {"type": "long_text", "title": "What should be added or changed in {topic}?"},
    ],
    "product": [
        {"type": "single_choice", "title": "How often do you use {topic}?", "options": ["Daily", "Weekly", "Monthly", "Less than monthly"]},
        {"type": "linear_scale", "title": "How easy is {topic} to use?", "config": {"min": 1, "max": 5, "min_label": "Very hard", "max_label": "Very easy"}},
        {"type": "multiple_choice", "title": "Which features do you use?", "options": ["Feature A", "Feature B", "Feature C"], "config": {"allow_other": True}},
        {"type": "linear_scale", "title": "How likely are you to recommend {topic} to a friend?", "config": {"min": 0, "max": 10, "min_label": "Not likely", "max_label": "Very likely"}},
        {"type": "long_text", "title": "What is the main problem {topic} should solve better?"},
    ],
    "service": [
        {"type": "rating", "title": "How satisfied were you with {topic}?", "config": {"max": 5}},
        {"type": "single_choice", "title": "How long did you wait to be served?", "options": ["Under 5 minutes", "5–15 minutes", "15–30 minutes", "Over 30 minutes"]},
        {"type": "linear_scale", "title": "How would you rate the value for money?", "config": {"min": 1, "max": 5, "min_label": "Poor", "max_label": "Excellent"}},
        {"type": "yes_no", "title": "Would you use {topic} again?"},
        {"type": "long_text", "title": "What could {topic} do better?"},
    ],
    "workplace": [
        {"type": "linear_scale", "title": "How satisfied are you with your work environment?", "config": {"min": 1, "max": 5, "min_label": "Very dissatisfied", "max_label": "Very satisfied"}},
        {"type": "single_choice", "title": "How often do you receive useful feedback on your work?", "options": ["Weekly", "Monthly", "A few times a year", "Rarely or never"]},
        {"type": "ranking", "title": "Rank these in order of importance to you", "options": ["Salary", "Learning opportunities", "Work-life balance", "Team culture"]},
        {"type": "yes_no", "title": "Do you have the tools you need to do your job?"},
        {"type": "long_text", "title": "What one change would most improve your work experience?"},
    ],
    "general": [
        {"type": "rating", "title": "Overall, how satisfied are you with {topic}?", "config": {"max": 5}},
        {"type": "single_choice", "title": "How often do you use or take part in {topic}?", "options": ["Daily", "Weekly", "Monthly", "Rarely"]},
        {"type": "multiple_choice", "title": "Which aspects of {topic} matter most to you?", "options": ["Quality", "Cost", "Convenience", "Support"], "config": {"allow_other": True, "max_select": 2}},
        {"type": "linear_scale", "title": "How likely are you to recommend {topic} to others?", "config": {"min": 0, "max": 10, "min_label": "Not likely", "max_label": "Very likely"}},
        {"type": "long_text", "title": "Any other comments about {topic}?"},
    ],
}


def detect_category(text):
    lower = text.lower()
    scores = {cat: sum(1 for word in words if re.search(rf"\b{word}", lower)) for cat, words in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def _finalize(raw_list):
    """Validate suggestions with the same rules as the builder; drop bad ones."""
    clean, used = [], set()
    for number, raw in enumerate(raw_list[:MAX_SUGGESTIONS], start=1):
        if not isinstance(raw, dict) or raw.get("type") not in REGISTRY:
            continue
        raw = dict(raw)
        raw.setdefault("required", False)
        raw["options"] = [{"text": o} if isinstance(o, str) else o for o in raw.get("options", [])]
        try:
            question = normalize_question(raw, number, used)
        except ValidationFailed:
            continue
        if question["title"]:
            question["rationale"] = normalize_text(raw.get("rationale"), 200)
            clean.append(question)
    return clean


def template_suggestions(topic, objective=""):
    topic_text = normalize_text(topic, 80) or "this"
    if not re.match(r"^(the|this|our|my|your|a|an)\b", topic_text, re.I):
        topic_text = f"the {topic_text}"
    category = detect_category(f"{topic} {objective}")
    raw = []
    for item in TEMPLATE_BANK[category]:
        item = dict(item)
        item["title"] = item["title"].replace("{topic}", topic_text)
        item["options"] = list(item.get("options", []))
        raw.append(item)
    return _finalize(raw), category


def suggest_questions(topic, objective="", use_ai=True):
    """Return {"source": "ai" | "template", "questions": [...], "note": str}."""
    topic, objective = normalize_text(topic, 120), normalize_text(objective, 300)
    if len(topic) < 3:
        raise ValidationFailed("Describe the survey topic in at least 3 characters.")
    if use_ai and is_configured():
        type_list = ", ".join(f"{k} ({t.label})" for k, t in REGISTRY.items())
        system = ("You help design clear, neutral survey questions. Avoid leading or double-barrelled wording. "
                  "Reply with JSON only.")
        user = (f"Topic: {topic}\nObjective: {objective or 'not given'}\n"
                f"Suggest 5-7 questions. Allowed types: {type_list}.\n"
                'Format: {"questions": [{"type": "...", "title": "...", "description": "", '
                '"options": ["..."], "config": {}, "rationale": "why this question helps"}]}. '
                "Use options only for single_choice, multiple_choice, dropdown and ranking. "
                'For rating use config {"max": 5}; for linear_scale {"min": 1, "max": 5}.')
        try:
            questions = _finalize(_extract_json(chat(system, user)).get("questions", []))
            if questions:
                return {"source": "ai", "questions": questions, "note": f"Suggested by {settings.AI_MODEL}. Review each question before adding it."}
        except (AIUnavailable, ValueError, AttributeError) as exc:
            logger.info("Falling back to templates: %s", exc)
            questions, category = template_suggestions(topic, objective)
            return {"source": "template", "questions": questions,
                    "note": f"The AI service was unavailable, so these come from the built-in '{category}' templates."}
    questions, category = template_suggestions(topic, objective)
    return {"source": "template", "questions": questions,
            "note": f"Built-in templates for a '{category}' survey (no AI service configured)."}


# --------------------------------------------------------------------------
# Insight rewriting
# --------------------------------------------------------------------------

def rewrite_insights(findings):
    """Ask the AI to turn rule-based findings into one readable paragraph.
    Returns text or None if AI is not available."""
    if not is_configured() or not findings:
        return None
    facts = "\n".join(f"- {f['text']}" for f in findings)
    system = ("You summarise survey results. Use ONLY the facts given. Do not add numbers, causes or "
              "recommendations that are not in the facts. Write one paragraph of at most 120 words.")
    try:
        return normalize_text(chat(system, f"Facts:\n{facts}", max_tokens=300), 1200)
    except AIUnavailable:
        return None
