"""
SurveyService — everything a signed-in creator does with surveys.

Survey document (collection `surveys`):

    {
      "_id": ObjectId, "public_token": "x7Kp2QmZr4aB",   # used in public links
      "owner_id": 7,                                     # Django user id
      "title": "...", "description": "...",
      "status": "draft" | "published" | "closed" | "archived",
      "settings": {...see DEFAULT_SETTINGS...},
      "questions": [ {qid, type, title, description, required, config, options[]} ],
      "collaborators": [ {user_id, username, role, added_at} ],
      "response_count": 12,                              # maintained with atomic $inc
      "moderation": {"status": "ok" | "suspended", "reason": "...", ...},
      "created_at", "updated_at", "published_at", "closed_at", "archived_at"
    }

Status life cycle:

    draft --publish--> published --close--> closed --reopen--> published
      \\                    \\                  \\
       +-------------------- archive ----------> archived --restore--> closed
"""
import re
from datetime import datetime

from django.contrib.auth import get_user_model
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from core import audit, mongo
from core.exceptions import ConflictError, NotFound, ValidationFailed
from core.platform import get_settings as platform_settings
from core.utils import as_aware, clean_multiline, new_id, new_token, normalize_text, utc_now

from . import permissions
from .question_types import normalize_questions

TITLE_MIN, TITLE_MAX = 3, 150
DESCRIPTION_MAX = 1000
STATUSES = ("draft", "published", "closed", "archived")
MAX_COLLABORATORS = 20

DEFAULT_SETTINGS = {
    "access": "public",                  # public | login
    "identity": "anonymous",             # anonymous | identified
    "duplicate_protection": "session",   # none | session | user
    "allow_edit": False,                 # respondents may change their submission
    "allow_save_resume": True,           # "Save and continue later"
    "display_mode": "all",               # all | one_per_page
    "start_at": None,
    "end_at": None,
    "max_responses": None,
    "results_public": False,             # aggregated charts visible via public link
    "thank_you_message": "",
    "consent_text": "",
}

SORTS = {
    "newest": [("created_at", DESCENDING), ("_id", DESCENDING)],
    "oldest": [("created_at", ASCENDING), ("_id", ASCENDING)],
    "updated": [("updated_at", DESCENDING), ("_id", DESCENDING)],
    "responses": [("response_count", DESCENDING), ("created_at", DESCENDING)],
    "title": [("title", ASCENDING), ("_id", ASCENDING)],
}


# --------------------------------------------------------------------------
# Validation helpers (pure functions)
# --------------------------------------------------------------------------

def validate_title(title):
    title = normalize_text(title)
    if len(title) < TITLE_MIN:
        raise ValidationFailed(f"Title must be at least {TITLE_MIN} characters.")
    if len(title) > TITLE_MAX:
        raise ValidationFailed(f"Title must be at most {TITLE_MAX} characters.")
    return title


def validate_description(description):
    description = clean_multiline(description)
    if len(description) > DESCRIPTION_MAX:
        raise ValidationFailed(f"Description must be at most {DESCRIPTION_MAX} characters.")
    return description


def validate_settings(raw):
    """Check survey settings and their combinations. Returns a clean dict.

    Rules that involve two settings (e.g. 'identified responses need login')
    are checked here on the server, never only in JavaScript.
    """
    s = dict(DEFAULT_SETTINGS)
    s.update({k: raw[k] for k in DEFAULT_SETTINGS if k in raw})
    errors = []

    for key, allowed in (("access", ("public", "login")), ("identity", ("anonymous", "identified")),
                         ("duplicate_protection", ("none", "session", "user")),
                         ("display_mode", ("all", "one_per_page"))):
        if s[key] not in allowed:
            errors.append(f"Invalid value for {key.replace('_', ' ')}.")

    for key in ("allow_edit", "allow_save_resume", "results_public"):
        s[key] = bool(s[key])

    if s["identity"] == "identified" and s["access"] != "login":
        errors.append("Identified responses require respondents to sign in (set access to 'Signed-in users only').")
    if s["duplicate_protection"] == "user" and s["access"] != "login":
        errors.append("'One response per account' requires respondents to sign in.")
    if s["allow_edit"] and s["duplicate_protection"] == "none":
        errors.append("Letting respondents edit their answer needs duplicate protection, so we can recognise them.")

    for key in ("start_at", "end_at"):
        value = s[key]
        if value is not None and not isinstance(value, datetime):
            errors.append(f"{key.replace('_', ' ').capitalize()} must be a date and time.")
            s[key] = None
        elif value is not None:
            s[key] = as_aware(value)
    if s["start_at"] and s["end_at"] and s["end_at"] <= s["start_at"]:
        errors.append("The end date must be after the start date.")

    if s["max_responses"] not in (None, ""):
        try:
            s["max_responses"] = int(s["max_responses"])
            if not 1 <= s["max_responses"] <= 1_000_000:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Maximum responses must be a whole number from 1 to 1,000,000.")
    else:
        s["max_responses"] = None

    s["thank_you_message"] = clean_multiline(s["thank_you_message"])
    s["consent_text"] = clean_multiline(s["consent_text"])
    if len(s["thank_you_message"]) > 1000:
        errors.append("The thank-you message must be at most 1000 characters.")
    if len(s["consent_text"]) > 2000:
        errors.append("The consent text must be at most 2000 characters.")

    if errors:
        raise ValidationFailed(errors)
    return s


def availability(survey, now=None):
    """Can this survey accept a NEW response right now?

    Returns (True, "", "") or (False, reason_code, message). The checks run in
    a fixed order so the respondent sees the most relevant explanation.
    """
    now = now or utc_now()
    settings = survey["settings"]
    if survey.get("moderation", {}).get("status") == "suspended":
        return False, "suspended", "This survey has been suspended by the platform administrators."
    status = survey["status"]
    if status == "draft":
        return False, "draft", "This survey has not been published yet."
    if status == "archived":
        return False, "archived", "This survey has been archived and no longer accepts responses."
    if status == "closed":
        return False, "closed", "This survey is closed and no longer accepts responses."
    if settings.get("start_at") and now < as_aware(settings["start_at"]):
        return False, "not_started", "This survey has not opened yet."
    if settings.get("end_at") and now >= as_aware(settings["end_at"]):
        return False, "ended", "This survey's response period has ended."
    limit = settings.get("max_responses")
    if limit and survey.get("response_count", 0) >= limit:
        return False, "limit_reached", "This survey has reached its maximum number of responses."
    return True, "", ""


def prepare(doc, user_id=None):
    """Add template-friendly fields (Django templates cannot read '_id')."""
    if doc is None:
        return None
    survey = dict(doc)
    survey["id"] = str(doc["_id"])
    for key in ("created_at", "updated_at", "published_at", "closed_at", "archived_at"):
        survey[key] = as_aware(doc.get(key))
    survey["settings"] = {**DEFAULT_SETTINGS, **doc.get("settings", {})}
    for key in ("start_at", "end_at"):
        survey["settings"][key] = as_aware(survey["settings"][key])
    survey["question_count"] = len(doc.get("questions", []))
    survey["response_count"] = doc.get("response_count", 0)
    survey["locked"] = survey["response_count"] > 0
    survey["suspended"] = doc.get("moderation", {}).get("status") == "suspended"
    accepting, reason, message = availability(survey)
    survey["accepting"], survey["closed_reason"], survey["closed_message"] = accepting, reason, message
    if user_id is not None:
        survey["role"] = permissions.role_for(doc, user_id)
        survey["can"] = {action: survey["role"] in roles for action, roles in permissions.PERMISSIONS.items()}
    return survey


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------

class SurveyService:
    """Survey operations performed by one signed-in user."""

    def __init__(self, user):
        self.user = user
        self.user_id = user.id
        self._surveys = mongo.surveys()
        self._responses = mongo.responses()
        self._sessions = mongo.sessions()

    # ---------- loading with permission checks ----------

    def _load(self, survey_id, action):
        oid = mongo.to_object_id(survey_id, "Survey not found.")
        doc = mongo.db_call(self._surveys.find_one, {"_id": oid})
        permissions.require(doc, self.user_id, action)  # NotFound / PermissionDenied
        return doc

    def get(self, survey_id, action="view"):
        return prepare(self._load(survey_id, action), self.user_id)

    def _update(self, doc, changes, extra=None):
        changes = {**changes, "updated_at": utc_now()}
        update = {"$set": changes}
        if extra:
            update.update(extra)
        mongo.db_call(self._surveys.update_one, {"_id": doc["_id"]}, update)

    # ---------- create ----------

    def create(self, title, description="", questions=None, settings=None, request=None):
        errors = []
        try:
            title = validate_title(title)
        except ValidationFailed as exc:
            errors += exc.errors
        try:
            description = validate_description(description)
        except ValidationFailed as exc:
            errors += exc.errors
        try:
            questions = normalize_questions(questions or [])
        except ValidationFailed as exc:
            errors += exc.errors
        try:
            clean_settings = validate_settings(settings or {})
        except ValidationFailed as exc:
            errors += exc.errors
        if errors:
            raise ValidationFailed(errors)

        limit = platform_settings()["max_surveys_per_user"]
        owned = mongo.db_call(self._surveys.count_documents, {"owner_id": self.user_id})
        if owned >= limit:
            raise ConflictError(f"You have reached the limit of {limit} surveys. Delete or archive old ones first.")

        now = utc_now()
        document = {
            "title": title,
            "description": description,
            "owner_id": self.user_id,
            "status": "draft",
            "settings": clean_settings,
            "questions": questions,
            "collaborators": [],
            "response_count": 0,
            "moderation": {"status": "ok"},
            "created_at": now,
            "updated_at": now,
            "published_at": None,
            "closed_at": None,
            "archived_at": None,
        }
        survey_id = self._insert_with_token(document)
        audit.log(self.user, "survey.create", "survey", survey_id, {"title": title}, request)
        return survey_id

    def _insert_with_token(self, document):
        for _ in range(5):  # token collisions are astronomically rare; retry anyway
            document["public_token"] = new_token()
            document.pop("_id", None)
            try:
                return str(mongo.db_call(self._surveys.insert_one, document).inserted_id)
            except DuplicateKeyError:
                continue
        raise ConflictError("Could not generate a unique survey link. Please try again.")

    def duplicate(self, survey_id, request=None):
        """Copy questions and settings into a new draft owned by the current user.
        New ids are generated so the copy is fully independent."""
        doc = self._load(survey_id, "duplicate")
        questions = []
        for question in doc.get("questions", []):
            copy = dict(question, qid=new_id("q"))
            copy["options"] = [dict(o, oid=new_id("o")) for o in question.get("options", [])]
            questions.append(copy)
        settings = {**doc.get("settings", {}), "start_at": None, "end_at": None}
        title = f"Copy of {doc['title']}"[:TITLE_MAX]
        new_survey = self.create(title, doc.get("description", ""), questions, settings, request)
        audit.log(self.user, "survey.duplicate", "survey", new_survey, {"source": str(doc["_id"])}, request)
        return new_survey

    # ---------- read ----------

    def list_surveys(self, q="", status="", scope="all", sort="newest", page=1, page_size=10):
        """Search, filter, sort and paginate the surveys this user can see.

        Returns (surveys, total_count). Archived surveys are hidden unless the
        status filter asks for them.
        """
        if scope == "owned":
            query = {"owner_id": self.user_id}
        elif scope == "shared":
            query = {"collaborators.user_id": self.user_id}
        else:
            query = permissions.membership_filter(self.user_id)
        if status in STATUSES:
            query["status"] = status
        else:
            query["status"] = {"$ne": "archived"}
        term = normalize_text(q)[:100]
        if term:
            query["title"] = {"$regex": re.escape(term), "$options": "i"}

        total = mongo.db_call(self._surveys.count_documents, query)
        cursor = (self._surveys.find(query)
                  .sort(SORTS.get(sort, SORTS["newest"]))
                  .skip(max(page - 1, 0) * page_size).limit(page_size))
        return [prepare(d, self.user_id) for d in mongo.db_call(list, cursor)], total

    def all_ids(self, include_archived=True):
        query = permissions.membership_filter(self.user_id)
        if not include_archived:
            query["status"] = {"$ne": "archived"}
        cursor = self._surveys.find(query, {"_id": 1})
        return [d["_id"] for d in mongo.db_call(list, cursor)]

    def accessible_choices(self):
        """[(id, title)] for drop-downs (comparison page, reports page)."""
        cursor = self._surveys.find(permissions.membership_filter(self.user_id), {"title": 1}).sort("created_at", -1)
        return [(str(d["_id"]), d["title"]) for d in mongo.db_call(list, cursor)]

    def dashboard(self):
        """Creator dashboard numbers — all counted from MongoDB."""
        base = permissions.membership_filter(self.user_id)
        pipeline = [
            {"$match": base},
            {"$group": {"_id": "$status", "count": {"$sum": 1}, "responses": {"$sum": "$response_count"}}},
        ]
        by_status = {row["_id"]: row for row in mongo.db_call(self._surveys.aggregate, pipeline)}
        count = lambda s: by_status.get(s, {}).get("count", 0)  # noqa: E731
        stats = {
            "total": sum(r["count"] for r in by_status.values()),
            "published": count("published"),
            "draft": count("draft"),
            "closed": count("closed"),
            "archived": count("archived"),
            "responses": sum(r["responses"] for r in by_status.values()),
        }
        recent, _ = self.list_surveys(sort="updated", page_size=5)
        top, _ = self.list_surveys(sort="responses", page_size=5)
        stats["recent"] = recent
        stats["top"] = [s for s in top if s["response_count"] > 0]
        return stats

    # ---------- update ----------

    def update_details(self, survey_id, title, description, request=None):
        doc = self._load(survey_id, "edit")
        errors = []
        try:
            title = validate_title(title)
        except ValidationFailed as exc:
            errors += exc.errors
        try:
            description = validate_description(description)
        except ValidationFailed as exc:
            errors += exc.errors
        if errors:
            raise ValidationFailed(errors)
        self._update(doc, {"title": title, "description": description})

    def update_settings(self, survey_id, raw_settings, request=None):
        doc = self._load(survey_id, "edit")
        if doc["status"] == "archived":
            raise ConflictError("Restore the survey before changing its settings.")
        clean = validate_settings(raw_settings)
        self._update(doc, {"settings": clean})
        audit.log(self.user, "survey.settings", "survey", doc["_id"], {}, request)
        return clean

    def save_questions(self, survey_id, raw_questions):
        """Replace the whole question list (used by the builder).

        Questions are locked once responses exist, because stored answers
        reference question and option ids; changing them would corrupt results.
        """
        doc = self._load(survey_id, "edit")
        if doc.get("response_count", 0) > 0:
            raise ConflictError("Questions are locked because this survey already has responses. Duplicate it to make a new version.")
        if doc["status"] == "archived":
            raise ConflictError("Archived surveys cannot be edited.")
        questions = normalize_questions(raw_questions)
        self._update(doc, {"questions": questions})
        return questions

    # ---------- question-level operations (used by the REST API) ----------

    def _questions_for_edit(self, survey_id):
        doc = self._load(survey_id, "edit")
        return doc, [dict(q) for q in doc.get("questions", [])]

    def add_question(self, survey_id, raw):
        doc, questions = self._questions_for_edit(survey_id)
        questions.append(raw)
        saved = self.save_questions(survey_id, questions)
        return saved[-1]

    def update_question(self, survey_id, qid, raw):
        doc, questions = self._questions_for_edit(survey_id)
        index = next((i for i, q in enumerate(questions) if q["qid"] == qid), None)
        if index is None:
            raise NotFound("Question not found.")
        questions[index] = {**questions[index], **raw, "qid": qid}
        return self.save_questions(survey_id, questions)[index]

    def delete_question(self, survey_id, qid):
        doc, questions = self._questions_for_edit(survey_id)
        remaining = [q for q in questions if q["qid"] != qid]
        if len(remaining) == len(questions):
            raise NotFound("Question not found.")
        self.save_questions(survey_id, remaining)

    def reorder_questions(self, survey_id, qid_order):
        doc, questions = self._questions_for_edit(survey_id)
        by_id = {q["qid"]: q for q in questions}
        if not isinstance(qid_order, list) or sorted(qid_order) != sorted(by_id):
            raise ValidationFailed("The new order must list every question exactly once.")
        return self.save_questions(survey_id, [by_id[qid] for qid in qid_order])

    # ---------- status changes ----------

    def publish(self, survey_id, request=None):
        """Publish (or reopen) after the quality checker finds no blocking errors."""
        from .quality import blocking_errors

        doc = self._load(survey_id, "publish")
        if doc["status"] == "archived":
            raise ConflictError("Restore the survey before publishing it.")
        if doc.get("moderation", {}).get("status") == "suspended":
            raise ConflictError("This survey is suspended by an administrator and cannot be published.")
        problems = blocking_errors(doc)
        if problems:
            raise ValidationFailed(problems)
        changes = {"status": "published"}
        if not doc.get("published_at"):
            changes["published_at"] = utc_now()
        self._update(doc, changes)
        audit.log(self.user, "survey.publish", "survey", doc["_id"], {"title": doc["title"]}, request)

    def close(self, survey_id, request=None):
        doc = self._load(survey_id, "publish")
        if doc["status"] != "published":
            raise ConflictError("Only published surveys can be closed.")
        self._update(doc, {"status": "closed", "closed_at": utc_now()})
        audit.log(self.user, "survey.close", "survey", doc["_id"], {}, request)

    def archive(self, survey_id, request=None):
        doc = self._load(survey_id, "archive")
        if doc["status"] == "archived":
            return
        self._update(doc, {"status": "archived", "archived_at": utc_now(), "status_before_archive": doc["status"]})
        audit.log(self.user, "survey.archive", "survey", doc["_id"], {}, request)

    def restore(self, survey_id, request=None):
        """Archived surveys come back as 'closed' (or 'draft' if never published)."""
        doc = self._load(survey_id, "archive")
        if doc["status"] != "archived":
            raise ConflictError("Only archived surveys can be restored.")
        new_status = "draft" if not doc.get("published_at") else "closed"
        self._update(doc, {"status": new_status, "archived_at": None})
        audit.log(self.user, "survey.restore", "survey", doc["_id"], {}, request)

    # ---------- delete ----------

    def delete(self, survey_id, request=None):
        """Delete the survey, then its responses and attempts.

        MongoDB without a replica set has no multi-document transactions, so
        this is three separate operations. The survey goes first: after that
        no new response can reference it, and leftovers would be harmless.
        """
        doc = self._load(survey_id, "delete")
        mongo.db_call(self._surveys.delete_one, {"_id": doc["_id"]})
        removed = mongo.db_call(self._responses.delete_many, {"survey_id": doc["_id"]}).deleted_count
        mongo.db_call(self._sessions.delete_many, {"survey_id": doc["_id"]})
        audit.log(self.user, "survey.delete", "survey", doc["_id"], {"title": doc["title"], "responses": removed}, request)
        return removed

    # ---------- collaborators ----------

    def add_collaborator(self, survey_id, username, role, request=None):
        doc = self._load(survey_id, "manage_collaborators")
        if role not in permissions.COLLABORATOR_ROLES:
            raise ValidationFailed("Role must be editor or viewer.")
        User = get_user_model()
        user = User.objects.filter(username__iexact=normalize_text(username), is_active=True).first()
        if user is None:
            raise ValidationFailed("No active user with that username.")
        if user.id == doc["owner_id"]:
            raise ValidationFailed("The owner already has full access.")
        collaborators = [c for c in doc.get("collaborators", []) if c["user_id"] != user.id]
        if len(collaborators) >= MAX_COLLABORATORS:
            raise ValidationFailed(f"A survey can have at most {MAX_COLLABORATORS} collaborators.")
        collaborators.append({"user_id": user.id, "username": user.username, "role": role, "added_at": utc_now()})
        self._update(doc, {"collaborators": collaborators})
        audit.log(self.user, "survey.collaborator_add", "survey", doc["_id"], {"user": user.username, "role": role}, request)
        return user

    def remove_collaborator(self, survey_id, user_id, request=None):
        doc = self._load(survey_id, "manage_collaborators")
        remaining = [c for c in doc.get("collaborators", []) if c["user_id"] != int(user_id)]
        if len(remaining) == len(doc.get("collaborators", [])):
            raise NotFound("Collaborator not found.")
        self._update(doc, {"collaborators": remaining})
        audit.log(self.user, "survey.collaborator_remove", "survey", doc["_id"], {"user_id": int(user_id)}, request)


def find_by_token(token):
    """Public lookup by the unguessable token in the share link."""
    if not isinstance(token, str) or not 8 <= len(token) <= 32:
        raise NotFound("Survey not found.")
    doc = mongo.db_call(mongo.surveys().find_one, {"public_token": token})
    if doc is None:
        raise NotFound("Survey not found.")
    return prepare(doc)


def reserve_response_slot(survey_oid, limit):
    """Atomically increase response_count, but only while it is below `limit`.

    find_one_and_update with the condition {"response_count": {"$lt": limit}}
    is a single atomic operation in MongoDB, so two respondents cannot both
    take the last slot. Returns False when the limit is already reached.
    """
    query = {"_id": survey_oid}
    if limit:
        query["response_count"] = {"$lt": limit}
    doc = mongo.db_call(
        mongo.surveys().find_one_and_update, query, {"$inc": {"response_count": 1}},
        projection={"_id": 1}, return_document=ReturnDocument.AFTER,
    )
    return doc is not None


def release_response_slot(survey_oid):
    mongo.db_call(mongo.surveys().update_one, {"_id": survey_oid, "response_count": {"$gt": 0}},
                  {"$inc": {"response_count": -1}})
