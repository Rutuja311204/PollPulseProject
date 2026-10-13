"""
Audit log: an append-only record of important actions.

Every entry answers: WHO did WHAT to WHICH object, WHEN, and FROM WHERE.
The application never updates or deletes audit entries (there is no UI or
service function for that), so administrators can review history.
"""
import logging

from core import mongo
from core.utils import client_ip, utc_now

logger = logging.getLogger(__name__)

ACTION_LABELS = {
    # survey lifecycle (creators)
    "survey.create": "Created survey",
    "survey.publish": "Published survey",
    "survey.close": "Closed survey",
    "survey.archive": "Archived survey",
    "survey.restore": "Restored survey",
    "survey.delete": "Deleted survey",
    "survey.duplicate": "Duplicated survey",
    "survey.settings": "Changed survey settings",
    "survey.collaborator_add": "Added collaborator",
    "survey.collaborator_remove": "Removed collaborator",
    "survey.export": "Exported data",
    # administrators
    "admin.user_suspend": "Suspended user",
    "admin.user_activate": "Reactivated user",
    "admin.user_grant_admin": "Granted admin role",
    "admin.user_revoke_admin": "Revoked admin role",
    "admin.survey_suspend": "Suspended survey (moderation)",
    "admin.survey_unsuspend": "Lifted survey suspension",
    "admin.survey_archive": "Archived survey (moderation)",
    "admin.settings": "Changed platform settings",
    # accounts
    "account.register": "Registered",
    "account.login_failed": "Failed login",
}

ADMIN_ACTIONS = [key for key in ACTION_LABELS if key.startswith("admin.")]


def log(actor, action, target_type="", target_id="", details=None, request=None):
    """Write one audit entry. Failures are logged but never break the action."""
    entry = {
        "action": action,
        "actor_id": getattr(actor, "id", None),
        "actor_username": getattr(actor, "username", "") or "",
        "actor_is_admin": bool(getattr(actor, "is_staff", False)),
        "target_type": target_type,
        "target_id": str(target_id) if target_id else "",
        "details": details or {},
        "ip": client_ip(request) if request is not None else "",
        "created_at": utc_now(),
    }
    try:
        mongo.db_call(mongo.audit_logs().insert_one, entry)
    except Exception as exc:  # auditing must not block the user's action
        logger.error("Could not write audit log %s: %s", action, exc)


def label(action):
    return ACTION_LABELS.get(action, action)


def search(action="", actor="", admin_only=False, target_id="", page=1, page_size=25):
    """Filtered, paginated audit entries (newest first). Returns (entries, total)."""
    query = {}
    if target_id:
        query["target_id"] = str(target_id)
    if action:
        query["action"] = action
    elif admin_only:
        query["action"] = {"$in": ADMIN_ACTIONS}
    if actor:
        query["actor_username"] = actor
    collection = mongo.audit_logs()
    total = mongo.db_call(collection.count_documents, query)
    cursor = collection.find(query).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size)
    entries = mongo.db_call(list, cursor)
    for entry in entries:
        entry["label"] = label(entry["action"])
    return entries, total


def recent_for_user(user_id, limit=8):
    cursor = mongo.audit_logs().find({"actor_id": user_id}).sort("created_at", -1).limit(limit)
    entries = mongo.db_call(list, cursor)
    for entry in entries:
        entry["label"] = label(entry["action"])
    return entries
