"""
Question types — one class per type (polymorphism).

Every question stored in MongoDB looks like:

    {
      "qid": "q_3f9a1c2b",
      "type": "rating",
      "title": "How satisfied are you?",
      "description": "1 = very unhappy, 5 = very happy",
      "required": true,
      "config": {"max": 5},                       # type-specific settings
      "options": []                               # only for option-based types
    }

and every stored answer looks like:

    {"qid": "q_3f9a1c2b", "type": "rating", "value": 4}

Each QuestionType subclass knows four things about its type:

  clean_config(raw)          validate builder settings (min/max, labels ...)
  publish_errors(question)   is the question complete enough to publish?
  parse_form(post, qid)      read the raw answer from an HTML form POST
  clean_answer(question, raw)  validate one answer -> stored payload or None

The rest of the code never uses `if type == ...` chains: it looks the type up
in REGISTRY and calls these methods. Adding a new type means adding a class.
"""
import re
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email

from core.exceptions import ValidationFailed
from core.utils import clean_multiline, new_id, normalize_text

OTHER = "__other__"          # value stored when the respondent picks "Other"
MAX_OPTION_LENGTH = 150
MAX_OTHER_TEXT = 200
QID_RE = re.compile(r"^q_[a-z0-9]{8}$")
OID_RE = re.compile(r"^o_[a-z0-9]{8}$")


def _int(value, name, low, high, errors, allow_none=False):
    """Parse an integer config value within [low, high]; record an error if invalid."""
    if value in (None, ""):
        if allow_none:
            return None
        errors.append(f"{name} is required.")
        return None
    try:
        number = int(value)
        if str(value).strip() not in (str(number), f"{number}.0"):
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"{name} must be a whole number.")
        return None
    if not low <= number <= high:
        errors.append(f"{name} must be between {low} and {high}.")
        return None
    return number


def _float(value, name, errors):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{name} must be a number.")
        return None
    if number != number or abs(number) > 1e12:  # NaN or absurd
        errors.append(f"{name} is out of range.")
        return None
    return int(number) if number.is_integer() else number


def _bool(value):
    return value in (True, "true", "True", "1", 1, "on", "yes")


def _iso_date(value, name, errors):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        errors.append(f"{name} must be a date in YYYY-MM-DD format.")
        return None


class QuestionType:
    key = ""
    label = ""
    icon = "bi-question"
    has_options = False
    analysis = "none"          # which analytics routine applies (see analytics.py)
    min_options = 0
    max_options = 0

    # ---------- builder side ----------

    def default_config(self):
        return {}

    def clean_config(self, raw):
        """Return a clean config dict or raise ValidationFailed."""
        return {}

    def publish_errors(self, question):
        errors = []
        if not question.get("title"):
            errors.append("The question text is empty.")
        if self.has_options:
            count = len(question.get("options", []))
            if count < self.min_options:
                errors.append(f"Add at least {self.min_options} answer options.")
            if count > self.max_options:
                errors.append(f"Use at most {self.max_options} answer options.")
            texts = [o["text"].casefold() for o in question.get("options", [])]
            if len(texts) != len(set(texts)):
                errors.append("Two answer options have the same text.")
        return errors

    # ---------- respondent side ----------

    def parse_form(self, post, qid):
        """Raw value from request.POST (string by default)."""
        return post.get(f"q_{qid}", "")

    def is_empty(self, raw):
        return raw in (None, "", [], {}) or (isinstance(raw, str) and not raw.strip())

    def clean_answer(self, question, raw):
        """Validate one answer.

        Returns the payload to store (a dict with at least "value"), or None if
        the optional question was skipped. Raises ValidationFailed otherwise.
        """
        if self.is_empty(raw) or (isinstance(raw, dict) and self.is_empty(raw.get("value")) and "ranks" not in raw):
            if question.get("required"):
                raise ValidationFailed("This question is required.")
            return None
        return self._clean(question, raw)

    def _clean(self, question, raw):
        raise NotImplementedError

    def display(self, question, answer):
        """Human-readable answer text (used in CSV exports)."""
        value = answer.get("value")
        return "" if value is None else str(value)

    def describe_rules(self, question):
        """Short hint shown to respondents, e.g. 'Choose up to 3'."""
        return ""


# ---------------------------------------------------------------------------
# Option-based types
# ---------------------------------------------------------------------------

class OptionQuestion(QuestionType):
    has_options = True
    min_options = 2
    max_options = 20
    supports_other = False

    def default_config(self):
        return {"allow_other": False} if self.supports_other else {}

    def clean_config(self, raw):
        if self.supports_other:
            return {"allow_other": _bool(raw.get("allow_other"))}
        return {}

    def option_map(self, question):
        return {o["oid"]: o["text"] for o in question.get("options", [])}

    def _check_choice(self, question, value):
        if value == OTHER and question["config"].get("allow_other"):
            return
        if value not in self.option_map(question):
            raise ValidationFailed("The selected option is not valid for this question.")

    def _other_text(self, raw):
        text = normalize_text(raw.get("other_text") if isinstance(raw, dict) else "", MAX_OTHER_TEXT + 1)
        if not text:
            raise ValidationFailed("Please describe your 'Other' answer.")
        if len(text) > MAX_OTHER_TEXT:
            raise ValidationFailed(f"'Other' text must be at most {MAX_OTHER_TEXT} characters.")
        return text

    def parse_form(self, post, qid):
        return {"value": post.get(f"q_{qid}", ""), "other_text": post.get(f"q_{qid}_other", "")}

    def _clean(self, question, raw):
        value = raw.get("value") if isinstance(raw, dict) else raw
        if not isinstance(value, str):
            raise ValidationFailed("Choose one option.")
        self._check_choice(question, value)
        payload = {"value": value}
        if value == OTHER:
            payload["other_text"] = self._other_text(raw)
        return payload

    def display(self, question, answer):
        value = answer.get("value")
        if value == OTHER:
            return f"Other: {answer.get('other_text', '')}"
        return self.option_map(question).get(value, "")


class SingleChoice(OptionQuestion):
    key, label, icon = "single_choice", "Single choice", "bi-ui-radios"
    analysis = "choice"
    supports_other = True


class Dropdown(OptionQuestion):
    key, label, icon = "dropdown", "Dropdown", "bi-menu-button-wide"
    analysis = "choice"
    max_options = 50


class MultipleChoice(OptionQuestion):
    key, label, icon = "multiple_choice", "Multiple choice", "bi-ui-checks"
    analysis = "multi"
    supports_other = True

    def default_config(self):
        return {"allow_other": False, "min_select": 0, "max_select": None}

    def clean_config(self, raw):
        errors = []
        config = {
            "allow_other": _bool(raw.get("allow_other")),
            "min_select": _int(raw.get("min_select", 0) or 0, "Minimum selections", 0, 50, errors),
            "max_select": _int(raw.get("max_select"), "Maximum selections", 1, 50, errors, allow_none=True),
        }
        if not errors and config["max_select"] is not None and config["min_select"] > config["max_select"]:
            errors.append("Minimum selections cannot be greater than maximum selections.")
        if errors:
            raise ValidationFailed(errors)
        return config

    def publish_errors(self, question):
        errors = super().publish_errors(question)
        available = len(question.get("options", [])) + (1 if question["config"].get("allow_other") else 0)
        if question["config"].get("min_select", 0) > available:
            errors.append("Minimum selections is larger than the number of options.")
        high = question["config"].get("max_select")
        if high and high > available:
            errors.append("Maximum selections is larger than the number of options.")
        return errors

    def parse_form(self, post, qid):
        return {"value": post.getlist(f"q_{qid}"), "other_text": post.get(f"q_{qid}_other", "")}

    def _clean(self, question, raw):
        values = raw.get("value") if isinstance(raw, dict) else raw
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValidationFailed("Choose one or more options.")
        if len(values) != len(set(values)):
            raise ValidationFailed("The same option was selected twice.")
        for value in values:
            self._check_choice(question, value)
        config = question["config"]
        low, high = config.get("min_select") or 0, config.get("max_select")
        if len(values) < max(low, 1):
            raise ValidationFailed(f"Select at least {max(low, 1)} option{'s' if max(low, 1) > 1 else ''}.")
        if high and len(values) > high:
            raise ValidationFailed(f"Select at most {high} option{'s' if high > 1 else ''}.")
        # store in the survey's option order so exports are consistent
        order = [o["oid"] for o in question["options"]] + [OTHER]
        payload = {"value": sorted(values, key=order.index)}
        if OTHER in values:
            payload["other_text"] = self._other_text(raw)
        return payload

    def display(self, question, answer):
        names = self.option_map(question)
        parts = [f"Other: {answer.get('other_text', '')}" if v == OTHER else names.get(v, "") for v in answer.get("value", [])]
        return "; ".join(parts)

    def describe_rules(self, question):
        config = question["config"]
        low, high = config.get("min_select") or 0, config.get("max_select")
        if low and high:
            return f"Choose {low}" if low == high else f"Choose {low} to {high}"
        if high:
            return f"Choose up to {high}"
        if low > 1:
            return f"Choose at least {low}"
        return "Choose all that apply"


class Ranking(OptionQuestion):
    key, label, icon = "ranking", "Ranking", "bi-sort-numeric-down"
    analysis = "ranking"
    max_options = 10

    def parse_form(self, post, qid):
        """The form has one <select> per option: q_<qid>_rank_<oid> = 1..n."""
        prefix = f"q_{qid}_rank_"
        ranks = {key[len(prefix):]: value for key, value in post.items() if key.startswith(prefix)}
        if all(not str(v).strip() for v in ranks.values()):
            return None
        return {"ranks": ranks}

    def is_empty(self, raw):
        return raw in (None, "", [], {})

    def _clean(self, question, raw):
        option_ids = [o["oid"] for o in question["options"]]
        if isinstance(raw, dict) and "ranks" in raw:
            # Form input: {oid: "1", oid2: "2"} -> ordered list of oids
            ranks = raw["ranks"]
            if set(ranks) != set(option_ids):
                raise ValidationFailed("Rank every option.")
            try:
                numbers = {oid: int(value) for oid, value in ranks.items()}
            except (TypeError, ValueError):
                raise ValidationFailed("Rank every option.")
            if sorted(numbers.values()) != list(range(1, len(option_ids) + 1)):
                raise ValidationFailed("Each rank (1, 2, 3 ...) must be used exactly once.")
            order = sorted(option_ids, key=lambda oid: numbers[oid])
        else:
            order = raw.get("value") if isinstance(raw, dict) else raw
            if not isinstance(order, list) or not all(isinstance(v, str) for v in order):
                raise ValidationFailed("Ranking must be a list of options.")
            if len(order) != len(set(order)):
                raise ValidationFailed("An option appears more than once in the ranking.")
            if sorted(order) != sorted(option_ids):
                raise ValidationFailed("The ranking must include every option exactly once.")
        return {"value": order}

    def display(self, question, answer):
        names = self.option_map(question)
        return " > ".join(names.get(v, "") for v in answer.get("value", []))

    def describe_rules(self, question):
        return "Put the options in order, 1 = most preferred"


class YesNo(QuestionType):
    key, label, icon = "yes_no", "Yes / No", "bi-toggle-on"
    analysis = "choice"
    CHOICES = (("yes", "Yes"), ("no", "No"))

    def _clean(self, question, raw):
        value = raw.get("value") if isinstance(raw, dict) else raw
        value = str(value).strip().lower()
        if value not in dict(self.CHOICES):
            raise ValidationFailed("Answer Yes or No.")
        return {"value": value}

    def display(self, question, answer):
        return dict(self.CHOICES).get(answer.get("value"), "")


# ---------------------------------------------------------------------------
# Text types
# ---------------------------------------------------------------------------

class TextQuestion(QuestionType):
    analysis = "text"
    hard_limit = 500
    default_max = 200
    multiline = False

    def default_config(self):
        return {"min_length": 0, "max_length": self.default_max}

    def clean_config(self, raw):
        errors = []
        config = {
            "min_length": _int(raw.get("min_length", 0) or 0, "Minimum length", 0, self.hard_limit, errors),
            "max_length": _int(raw.get("max_length") or self.default_max, "Maximum length", 1, self.hard_limit, errors),
        }
        if not errors and config["min_length"] > config["max_length"]:
            errors.append("Minimum length cannot be greater than maximum length.")
        if errors:
            raise ValidationFailed(errors)
        return config

    def _clean(self, question, raw):
        value = raw.get("value") if isinstance(raw, dict) else raw
        if not isinstance(value, str):
            raise ValidationFailed("Enter text.")
        value = clean_multiline(value) if self.multiline else normalize_text(value)
        config = question["config"]
        if len(value) < config.get("min_length", 0):
            raise ValidationFailed(f"Write at least {config['min_length']} characters.")
        if len(value) > config.get("max_length", self.default_max):
            raise ValidationFailed(f"Write at most {config['max_length']} characters.")
        return {"value": value}

    def describe_rules(self, question):
        config = question["config"]
        if config.get("min_length"):
            return f"{config['min_length']}–{config['max_length']} characters"
        return f"Up to {config.get('max_length', self.default_max)} characters"


class ShortText(TextQuestion):
    key, label, icon = "short_text", "Short text", "bi-input-cursor-text"
    hard_limit, default_max = 500, 200


class LongText(TextQuestion):
    key, label, icon = "long_text", "Long text", "bi-text-paragraph"
    hard_limit, default_max = 5000, 2000
    multiline = True


class Email(QuestionType):
    key, label, icon = "email", "Email", "bi-envelope-at"
    analysis = "email"

    def _clean(self, question, raw):
        value = raw.get("value") if isinstance(raw, dict) else raw
        value = normalize_text(value).lower()
        if len(value) > 254:
            raise ValidationFailed("Enter a valid email address.")
        try:
            validate_email(value)  # Django's RFC-based validator
        except DjangoValidationError:
            raise ValidationFailed("Enter a valid email address, like name@example.com.")
        return {"value": value}


# ---------------------------------------------------------------------------
# Number, scale and date types
# ---------------------------------------------------------------------------

class Number(QuestionType):
    key, label, icon = "number", "Number", "bi-123"
    analysis = "numeric"

    def default_config(self):
        return {"min_value": None, "max_value": None, "integer_only": False}

    def clean_config(self, raw):
        errors = []
        config = {
            "min_value": _float(raw.get("min_value"), "Minimum value", errors),
            "max_value": _float(raw.get("max_value"), "Maximum value", errors),
            "integer_only": _bool(raw.get("integer_only")),
        }
        if not errors and None not in (config["min_value"], config["max_value"]) \
                and config["min_value"] > config["max_value"]:
            errors.append("Minimum value cannot be greater than maximum value.")
        if errors:
            raise ValidationFailed(errors)
        return config

    def _clean(self, question, raw):
        value = raw.get("value") if isinstance(raw, dict) else raw
        if isinstance(value, bool):
            raise ValidationFailed("Enter a number.")
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            raise ValidationFailed("Enter a number.")
        if number != number or abs(number) > 1e12:
            raise ValidationFailed("Enter a realistic number.")
        config = question["config"]
        if config.get("integer_only") and not number.is_integer():
            raise ValidationFailed("Enter a whole number.")
        low, high = config.get("min_value"), config.get("max_value")
        if low is not None and number < low:
            raise ValidationFailed(f"Enter a number of at least {low}.")
        if high is not None and number > high:
            raise ValidationFailed(f"Enter a number of at most {high}.")
        return {"value": int(number) if number.is_integer() else round(number, 6)}

    def describe_rules(self, question):
        config = question["config"]
        low, high = config.get("min_value"), config.get("max_value")
        kind = "Whole number" if config.get("integer_only") else "Number"
        if low is not None and high is not None:
            return f"{kind} from {low} to {high}"
        if low is not None:
            return f"{kind}, at least {low}"
        if high is not None:
            return f"{kind}, at most {high}"
        return kind if config.get("integer_only") else ""


class ScaleQuestion(QuestionType):
    analysis = "scale"

    def bounds(self, question):
        raise NotImplementedError

    def _clean(self, question, raw):
        value = raw.get("value") if isinstance(raw, dict) else raw
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValidationFailed("Choose a value on the scale.")
        low, high = self.bounds(question)
        if not low <= number <= high:
            raise ValidationFailed(f"Choose a value from {low} to {high}.")
        return {"value": number}


class Rating(ScaleQuestion):
    """Star rating from 1 to max (3..10)."""

    key, label, icon = "rating", "Rating", "bi-star-half"

    def default_config(self):
        return {"max": 5}

    def clean_config(self, raw):
        errors = []
        config = {"max": _int(raw.get("max") or 5, "Number of stars", 3, 10, errors)}
        if errors:
            raise ValidationFailed(errors)
        return config

    def bounds(self, question):
        return 1, question["config"].get("max", 5)


class LinearScale(ScaleQuestion):
    """Numbered scale, e.g. 0–10 'How likely are you to recommend us?'."""

    key, label, icon = "linear_scale", "Linear scale", "bi-sliders"

    def default_config(self):
        return {"min": 1, "max": 5, "min_label": "", "max_label": ""}

    def clean_config(self, raw):
        errors = []
        config = {
            "min": _int(raw.get("min", 1) if raw.get("min") not in (None, "") else 1, "Scale start", 0, 1, errors),
            "max": _int(raw.get("max") or 5, "Scale end", 2, 10, errors),
            "min_label": normalize_text(raw.get("min_label"), 40),
            "max_label": normalize_text(raw.get("max_label"), 40),
        }
        if errors:
            raise ValidationFailed(errors)
        return config

    def bounds(self, question):
        return question["config"].get("min", 1), question["config"].get("max", 5)


class DateQuestion(QuestionType):
    key, label, icon = "date", "Date", "bi-calendar-event"
    analysis = "date"

    def default_config(self):
        return {"min_date": None, "max_date": None}

    def clean_config(self, raw):
        errors = []
        config = {
            "min_date": _iso_date(raw.get("min_date"), "Earliest date", errors),
            "max_date": _iso_date(raw.get("max_date"), "Latest date", errors),
        }
        if not errors and config["min_date"] and config["max_date"] and config["min_date"] > config["max_date"]:
            errors.append("Earliest date must be before latest date.")
        if errors:
            raise ValidationFailed(errors)
        return config

    def _clean(self, question, raw):
        value = raw.get("value") if isinstance(raw, dict) else raw
        try:
            day = date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            raise ValidationFailed("Enter a valid date.")
        config = question["config"]
        if config.get("min_date") and day < config["min_date"]:
            raise ValidationFailed(f"Choose a date on or after {config['min_date']}.")
        if config.get("max_date") and day > config["max_date"]:
            raise ValidationFailed(f"Choose a date on or before {config['max_date']}.")
        return {"value": day}

    def describe_rules(self, question):
        config = question["config"]
        if config.get("min_date") and config.get("max_date"):
            return f"Between {config['min_date']} and {config['max_date']}"
        return ""


# ---------------------------------------------------------------------------
# Registry and builder normalisation
# ---------------------------------------------------------------------------

REGISTRY = {cls.key: cls() for cls in (
    SingleChoice, MultipleChoice, ShortText, LongText, Email, Number,
    Rating, LinearScale, Dropdown, YesNo, DateQuestion, Ranking,
)}

TYPE_CHOICES = [(key, qtype.label) for key, qtype in REGISTRY.items()]
CHOICE_LIKE = {"single_choice", "dropdown", "yes_no"}   # usable in cross-tabulation
MAX_QUESTIONS = 50
MAX_TITLE = 300
MAX_DESCRIPTION = 500


def get_type(key):
    try:
        return REGISTRY[key]
    except KeyError:
        raise ValidationFailed(f"Unknown question type '{key}'.")


def option_labels(question):
    """[(oid, text)] including 'Yes'/'No' for yes_no and 'Other' when enabled."""
    if question["type"] == "yes_no":
        return list(YesNo.CHOICES)
    labels = [(o["oid"], o["text"]) for o in question.get("options", [])]
    if question.get("config", {}).get("allow_other"):
        labels.append((OTHER, "Other"))
    return labels


def normalize_question(raw, number, used_qids):
    """Turn builder input into a clean stored question (raises ValidationFailed).

    Drafts may be incomplete (empty text, too few options); completeness is
    checked when publishing. Here we enforce types, lengths and config ranges.
    """
    if not isinstance(raw, dict):
        raise ValidationFailed(f"Question {number}: invalid data.")
    qtype = get_type(raw.get("type"))
    prefix = f"Question {number}"

    qid = raw.get("qid") if isinstance(raw.get("qid"), str) and QID_RE.match(raw.get("qid", "")) else None
    if not qid or qid in used_qids:
        qid = new_id("q")
    used_qids.add(qid)

    errors = []
    title = normalize_text(raw.get("title"))
    if len(title) > MAX_TITLE:
        errors.append(f"{prefix}: question text must be at most {MAX_TITLE} characters.")
    description = clean_multiline(raw.get("description"))
    if len(description) > MAX_DESCRIPTION:
        errors.append(f"{prefix}: description must be at most {MAX_DESCRIPTION} characters.")

    try:
        config = qtype.clean_config(raw.get("config") or {})
    except ValidationFailed as exc:
        errors.extend(f"{prefix}: {e}" for e in exc.errors)
        config = qtype.default_config()

    options = []
    if qtype.has_options:
        raw_options = raw.get("options") or []
        if not isinstance(raw_options, list):
            raw_options = []
        if len(raw_options) > qtype.max_options:
            errors.append(f"{prefix}: at most {qtype.max_options} options are allowed.")
        used_oids = set()
        for item in raw_options[: qtype.max_options]:
            text = normalize_text(item.get("text") if isinstance(item, dict) else item)
            if not text:
                continue  # empty option rows from the builder are ignored
            if len(text) > MAX_OPTION_LENGTH:
                errors.append(f"{prefix}: options must be at most {MAX_OPTION_LENGTH} characters.")
                continue
            oid = item.get("oid") if isinstance(item, dict) else None
            if not (isinstance(oid, str) and OID_RE.match(oid)) or oid in used_oids:
                oid = new_id("o")
            used_oids.add(oid)
            options.append({"oid": oid, "text": text})

    if errors:
        raise ValidationFailed(errors)
    return {
        "qid": qid,
        "type": qtype.key,
        "title": title,
        "description": description,
        "required": _bool(raw.get("required")),
        "config": config,
        "options": options,
    }


def normalize_questions(raw_list):
    if not isinstance(raw_list, list):
        raise ValidationFailed("Questions must be a list.")
    if len(raw_list) > MAX_QUESTIONS:
        raise ValidationFailed(f"A survey can have at most {MAX_QUESTIONS} questions.")
    errors, clean, used = [], [], set()
    for number, raw in enumerate(raw_list, start=1):
        try:
            clean.append(normalize_question(raw, number, used))
        except ValidationFailed as exc:
            errors.extend(exc.errors)
    if errors:
        raise ValidationFailed(errors)
    return clean


def builder_type_catalog():
    """Data the builder JavaScript needs about each type."""
    return [
        {
            "key": key,
            "label": qtype.label,
            "icon": qtype.icon,
            "hasOptions": qtype.has_options,
            "supportsOther": getattr(qtype, "supports_other", False),
            "minOptions": qtype.min_options,
            "maxOptions": qtype.max_options,
            "defaultConfig": qtype.default_config(),
        }
        for key, qtype in REGISTRY.items()
    ]
