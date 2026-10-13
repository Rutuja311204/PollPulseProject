"""
Platform-wide settings stored in MongoDB (one document with _id "global").

Administrators change these from the admin panel; every change is audited.
"""
from core import mongo
from core.exceptions import ValidationFailed
from core.utils import normalize_text, utc_now

DEFAULTS = {
    "registration_open": True,          # can new users sign up?
    "announcement": "",                 # banner shown on every creator dashboard
    "max_surveys_per_user": 100,        # protects the platform from abuse
}


def get_settings():
    doc = mongo.db_call(mongo.platform_settings().find_one, {"_id": "global"}) or {}
    merged = dict(DEFAULTS)
    merged.update({key: doc[key] for key in DEFAULTS if key in doc})
    merged["updated_at"] = doc.get("updated_at")
    merged["updated_by"] = doc.get("updated_by", "")
    return merged


def update_settings(registration_open, announcement, max_surveys_per_user, actor):
    announcement = normalize_text(announcement)
    errors = []
    if len(announcement) > 300:
        errors.append("The announcement must be at most 300 characters.")
    try:
        max_surveys = int(max_surveys_per_user)
        if not 1 <= max_surveys <= 10000:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Maximum surveys per user must be a whole number from 1 to 10000.")
        max_surveys = None
    if errors:
        raise ValidationFailed(errors)
    values = {
        "registration_open": bool(registration_open),
        "announcement": announcement,
        "max_surveys_per_user": max_surveys,
    }
    mongo.db_call(
        mongo.platform_settings().update_one,
        {"_id": "global"},
        {"$set": {**values, "updated_at": utc_now(), "updated_by": actor.username}},
        upsert=True,
    )
    return values
