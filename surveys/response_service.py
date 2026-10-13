"""
ResponseService — what respondents do: open a survey, save a draft, submit.

Response document (collection `responses`):

    {
      "_id": ObjectId,
      "survey_id": ObjectId,
      "dedupe_key": "9f2c...",          # see "duplicate protection" below
      "respondent": {"user_id": 7, "username": "asha"} | None,   # identified surveys only
      "answers": [ {"qid": "q_...", "type": "rating", "value": 4}, ... ],
      "consent": true,
      "source": "web" | "api",
      "started_at": datetime | None,    # when the respondent opened the survey
      "submitted_at": datetime,
      "duration_seconds": 83 | None,
      "edited_at": datetime | None
    }

Attempt document (collection `response_sessions`) — created when a
respondent opens a survey. It stores save-and-resume drafts and lets us
calculate completion rate (completed attempts / started attempts) and time.

Duplicate protection
--------------------
* "session": the browser session gets a random id; we store an HMAC hash of it.
* "user":    respondents must sign in; we store an HMAC hash of the user id.
* "none":    every submission gets a random dedupe_key, so nothing is blocked.
A unique index on (survey_id, dedupe_key) makes MongoDB reject a second
response with the same key even if two requests arrive at the same moment.
The hash means anonymous surveys never store who the respondent is.
"""
import uuid
from dataclasses import dataclass

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from core import mongo
from core.exceptions import DuplicateResponse, NotFound, SurveyUnavailable, ValidationFailed
from core.utils import as_aware, keyed_hash, new_token, utc_now

from .question_types import get_type
from .survey_service import availability, release_response_slot, reserve_response_slot

DRAFT_MAX_STRING = 5000
DRAFT_MAX_ITEMS = 60


@dataclass
class Respondent:
    base_key: str            # "user:7" or "session:<uuid>"
    user_id: int = None
    username: str = ""

    @property
    def key(self):
        """Hashed identifier stored in MongoDB (never the raw id)."""
        return keyed_hash(self.base_key)


def respondent_for(request, survey):
    """Work out who is answering, based on the survey's settings."""
    settings = survey["settings"]
    user = request.user
    if settings["access"] == "login" and not user.is_authenticated:
        raise SurveyUnavailable("login_required", "Please sign in to answer this survey.")
    if settings["duplicate_protection"] == "user" or (settings["access"] == "login" and user.is_authenticated):
        return Respondent(f"user:{user.id}", user.id, user.username)
    session_id = request.session.get("pp_respondent")
    if not session_id:
        session_id = uuid.uuid4().hex
        request.session["pp_respondent"] = session_id
    respondent = Respondent(f"session:{session_id}")
    if user.is_authenticated:
        respondent.user_id, respondent.username = user.id, user.username
    return respondent


# --------------------------------------------------------------------------
# Answer validation
# --------------------------------------------------------------------------

def validate_answers(survey, raw_by_qid, reject_unknown=True):
    """Validate every answer against the survey's real questions.

    raw_by_qid: {qid: raw value} from the HTML form or the API.
    Returns the list of answers to store (skipped optional questions omitted).
    Raises ValidationFailed whose field_errors maps qid -> [messages].
    """
    if not isinstance(raw_by_qid, dict):
        raise ValidationFailed("Answers have an invalid format.")
    questions = survey["questions"]
    known = {q["qid"] for q in questions}
    if reject_unknown and any(qid not in known for qid in raw_by_qid):
        raise ValidationFailed("The response contains a question that is not part of this survey.")

    answers, field_errors = [], {}
    for number, question in enumerate(questions, start=1):
        qtype = get_type(question["type"])
        try:
            payload = qtype.clean_answer(question, raw_by_qid.get(question["qid"]))
        except ValidationFailed as exc:
            field_errors[question["qid"]] = [f"Question {number}: {msg}" for msg in exc.errors]
            continue
        if payload is not None:
            answers.append({"qid": question["qid"], "type": question["type"], **payload})
    if field_errors:
        raise ValidationFailed(field_errors=field_errors)
    return answers


def parse_form_answers(survey, post):
    """Read every question's raw value out of request.POST."""
    return {q["qid"]: get_type(q["type"]).parse_form(post, q["qid"]) for q in survey["questions"]}


def answer_to_raw(question, answer):
    """Convert a stored answer back into the shape the form templates use
    (for 'edit your response' and for resuming drafts)."""
    if answer is None:
        return None
    if question["type"] == "ranking":
        return {"ranks": {oid: str(i) for i, oid in enumerate(answer.get("value", []), start=1)}}
    if get_type(question["type"]).has_options:
        return {"value": answer.get("value"), "other_text": answer.get("other_text", "")}
    return answer.get("value")


def _sanitize(value, depth=0):
    """Limit the size of draft data before storing it (drafts are not validated)."""
    if depth > 3:
        return None
    if isinstance(value, str):
        return value[:DRAFT_MAX_STRING]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize(v, depth + 1) for v in list(value)[:DRAFT_MAX_ITEMS]]
    if isinstance(value, dict):
        return {str(k)[:40]: _sanitize(v, depth + 1) for k, v in list(value.items())[:DRAFT_MAX_ITEMS]}
    return None


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------

class ResponseService:
    def __init__(self, survey):
        self.survey = survey
        self.survey_oid = survey["_id"]
        self._responses = mongo.responses()
        self._sessions = mongo.sessions()

    # ---------- attempts and drafts ----------

    def start_attempt(self, respondent):
        """Find this respondent's open attempt or create one (atomic upsert).
        The first visit sets started_at, which later gives completion time."""
        now = utc_now()
        return mongo.db_call(
            self._sessions.find_one_and_update,
            {"survey_id": self.survey_oid, "respondent_key": respondent.key, "status": "in_progress"},
            {"$setOnInsert": {"resume_token": new_token(16), "started_at": now, "draft": {}, "last_saved_at": None},
             "$set": {"last_seen_at": now}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )

    def attempt_by_resume_token(self, token):
        if not isinstance(token, str) or len(token) > 40:
            return None
        return mongo.db_call(self._sessions.find_one, {"survey_id": self.survey_oid, "resume_token": token,
                                                       "status": "in_progress"})

    def save_draft(self, respondent, raw_by_qid, resume_token=None):
        if not self.survey["settings"]["allow_save_resume"]:
            raise ValidationFailed("This survey does not allow saving a draft.")
        known = {q["qid"] for q in self.survey["questions"]}
        draft = {qid: _sanitize(value) for qid, value in raw_by_qid.items() if qid in known}
        attempt = self.attempt_by_resume_token(resume_token) if resume_token else None
        attempt = attempt or self.start_attempt(respondent)
        mongo.db_call(self._sessions.update_one, {"_id": attempt["_id"]},
                      {"$set": {"draft": draft, "last_saved_at": utc_now()}})
        return attempt["resume_token"]

    # ---------- existing responses ----------

    def protection_enabled(self):
        return self.survey["settings"]["duplicate_protection"] != "none"

    def existing_response(self, respondent):
        if not self.protection_enabled() or respondent is None:
            return None
        return mongo.db_call(self._responses.find_one, {"survey_id": self.survey_oid, "dedupe_key": respondent.key})

    # ---------- submit ----------

    def submit(self, respondent, raw_by_qid, consent=False, source="web", resume_token=None):
        """Validate and store a response (or update it when editing is allowed).

        Order of checks:
          1. survey availability (status, dates, moderation, limit)
          2. consent, if the survey asks for it
          3. every answer against its question type
          4. duplicate protection (or edit of the existing response)
          5. atomic response-limit reservation, then insert
        Returns {"response_id": str, "edited": bool}.
        """
        survey = self.survey
        existing = self.existing_response(respondent)
        editing = existing is not None and survey["settings"]["allow_edit"]

        accepting, reason, message = availability(survey)
        if not accepting and not (editing and reason == "limit_reached"):
            raise SurveyUnavailable(reason, message)
        if survey["settings"]["consent_text"] and not consent:
            raise ValidationFailed(field_errors={"consent": ["Please confirm that you agree before submitting."]})

        answers = validate_answers(survey, raw_by_qid)

        if existing is not None:
            if not editing:
                raise DuplicateResponse()
            mongo.db_call(self._responses.update_one, {"_id": existing["_id"]},
                          {"$set": {"answers": answers, "edited_at": utc_now(), "consent": bool(consent)}})
            self._complete_attempt(respondent, resume_token, existing["_id"])
            return {"response_id": str(existing["_id"]), "edited": True}

        attempt = self._find_attempt(respondent, resume_token)
        now = utc_now()
        started_at = as_aware(attempt["started_at"]) if attempt else None
        identified = survey["settings"]["identity"] == "identified"
        document = {
            "survey_id": self.survey_oid,
            "dedupe_key": respondent.key if self.protection_enabled() else f"free:{uuid.uuid4().hex}",
            "respondent": {"user_id": respondent.user_id, "username": respondent.username} if identified else None,
            "answers": answers,
            "consent": bool(consent),
            "source": source,
            "started_at": started_at,
            "submitted_at": now,
            "duration_seconds": int((now - started_at).total_seconds()) if started_at else None,
            "edited_at": None,
        }

        if not reserve_response_slot(self.survey_oid, survey["settings"]["max_responses"]):
            raise SurveyUnavailable("limit_reached", "This survey has reached its maximum number of responses.")
        try:
            result = mongo.db_call(self._responses.insert_one, document)
        except DuplicateKeyError:
            release_response_slot(self.survey_oid)  # give the reserved slot back
            raise DuplicateResponse()
        except Exception:
            release_response_slot(self.survey_oid)
            raise
        self._complete_attempt(respondent, resume_token, result.inserted_id, attempt)
        return {"response_id": str(result.inserted_id), "edited": False}

    def _find_attempt(self, respondent, resume_token):
        if resume_token:
            attempt = self.attempt_by_resume_token(resume_token)
            if attempt:
                return attempt
        if respondent is None:
            return None
        return mongo.db_call(self._sessions.find_one, {"survey_id": self.survey_oid,
                                                       "respondent_key": respondent.key, "status": "in_progress"})

    def _complete_attempt(self, respondent, resume_token, response_id, attempt=None):
        attempt = attempt or self._find_attempt(respondent, resume_token)
        if attempt:
            mongo.db_call(self._sessions.update_one, {"_id": attempt["_id"]},
                          {"$set": {"status": "completed", "completed_at": utc_now(), "response_id": response_id,
                                    "draft": {}}})


def get_response_for_owner(survey, response_id):
    oid = mongo.to_object_id(response_id, "Response not found.")
    doc = mongo.db_call(mongo.responses().find_one, {"_id": oid, "survey_id": survey["_id"]})
    if doc is None:
        raise NotFound("Response not found.")
    return doc
