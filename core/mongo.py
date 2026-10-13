"""
MongoDB connection layer.

* One shared MongoClient for the whole process (it is thread-safe and keeps a
  connection pool, so we must not create one per request).
* Helper functions return each collection.
* Indexes are created once per process (create_index is idempotent).
* `db_call()` turns low-level driver errors into DatabaseUnavailable.
* Tests swap in an in-memory database with `use_database()`.

Collections
-----------
surveys            one document per survey; questions and options embedded
responses          one document per submitted response; answers embedded
response_sessions  one document per "attempt" (opened the survey); used for
                   save-and-resume drafts, completion rate and timing
audit_logs         append-only log of important actions
platform_settings  a single document with platform-wide switches
"""
import logging

from bson import ObjectId
from bson.errors import InvalidId
from django.conf import settings
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from .exceptions import DatabaseUnavailable, NotFound

logger = logging.getLogger(__name__)

_client = None
_db_override = None
_indexes_ready = False

INDEXES = {
    "surveys": [
        ([("public_token", ASCENDING)], {"unique": True, "name": "public_token_unique"}),
        ([("owner_id", ASCENDING), ("created_at", DESCENDING)], {"name": "owner_created"}),
        ([("collaborators.user_id", ASCENDING)], {"name": "collaborator_user"}),
        ([("status", ASCENDING), ("created_at", DESCENDING)], {"name": "status_created"}),
    ],
    "responses": [
        # Duplicate protection: the same dedupe_key can exist only once per survey.
        ([("survey_id", ASCENDING), ("dedupe_key", ASCENDING)], {"unique": True, "name": "survey_dedupe_unique"}),
        ([("survey_id", ASCENDING), ("submitted_at", DESCENDING)], {"name": "survey_submitted"}),
        ([("submitted_at", DESCENDING)], {"name": "submitted"}),
    ],
    "response_sessions": [
        ([("resume_token", ASCENDING)], {"unique": True, "name": "resume_token_unique"}),
        ([("survey_id", ASCENDING), ("respondent_key", ASCENDING), ("status", ASCENDING)], {"name": "survey_respondent_status"}),
        ([("survey_id", ASCENDING), ("started_at", DESCENDING)], {"name": "survey_started"}),
    ],
    "audit_logs": [
        ([("created_at", DESCENDING)], {"name": "created"}),
        ([("actor_id", ASCENDING), ("created_at", DESCENDING)], {"name": "actor_created"}),
        ([("action", ASCENDING), ("created_at", DESCENDING)], {"name": "action_created"}),
        ([("target_id", ASCENDING), ("created_at", DESCENDING)], {"name": "target_created"}),
    ],
}


def get_client():
    global _client
    if _client is None:
        _client = MongoClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=settings.MONGO_TIMEOUT_MS,
            tz_aware=True,  # datetimes come back timezone-aware (UTC)
        )
    return _client


def get_db():
    db = _db_override if _db_override is not None else get_client()[settings.MONGO_DB_NAME]
    _ensure_indexes(db)
    return db


def use_database(db):
    """Point the whole app at another database object (tests use mongomock)."""
    global _db_override, _indexes_ready
    _db_override = db
    _indexes_ready = False


def collection(name):
    return get_db()[name]


def surveys():
    return collection("surveys")


def responses():
    return collection("responses")


def sessions():
    return collection("response_sessions")


def audit_logs():
    return collection("audit_logs")


def platform_settings():
    return collection("platform_settings")


def _ensure_indexes(db):
    global _indexes_ready
    if _indexes_ready:
        return
    try:
        for name, specs in INDEXES.items():
            for keys, options in specs:
                db[name].create_index(keys, **options)
    except PyMongoError as exc:
        logger.error("Could not create MongoDB indexes: %s", exc)
        raise DatabaseUnavailable() from exc
    _indexes_ready = True


def db_call(func, *args, **kwargs):
    """Run a PyMongo call; convert connection/server errors to DatabaseUnavailable.

    DuplicateKeyError is re-raised unchanged because callers use it as a
    business signal (duplicate response, token collision)."""
    try:
        return func(*args, **kwargs)
    except DuplicateKeyError:
        raise
    except PyMongoError as exc:
        logger.error("MongoDB error: %s", exc)
        raise DatabaseUnavailable() from exc


def ping():
    try:
        get_db().command("ping")
        return True
    except (PyMongoError, DatabaseUnavailable) as exc:
        logger.warning("MongoDB ping failed: %s", exc)
        return False


def to_object_id(value, not_found_message="Not found."):
    """Convert a URL string into an ObjectId; a malformed value becomes NotFound."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError) as exc:
        raise NotFound(not_found_message) from exc
