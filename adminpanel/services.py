"""
Administrator operations: platform statistics, user management, survey
moderation and health. Every state-changing method writes an audit entry.
Access is checked by the views (core.roles.admin_required / IsPlatformAdmin).
"""
import re
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from core import audit, mongo
from core.exceptions import NotFound, PermissionDeniedError, ValidationFailed
from core.utils import as_aware, normalize_text, utc_now
from surveys.survey_service import prepare

User = get_user_model()
GRANULARITIES = {"day": 30, "week": 12, "month": 12}


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def overview():
    surveys, responses = mongo.surveys(), mongo.responses()
    status_rows = mongo.db_call(surveys.aggregate, [{"$group": {"_id": "$status", "count": {"$sum": 1}}}])
    by_status = {row["_id"]: row["count"] for row in status_rows}
    return {
        "users_total": User.objects.count(),
        "users_active": User.objects.filter(is_active=True).count(),
        "users_suspended": User.objects.filter(is_active=False).count(),
        "admins": User.objects.filter(is_staff=True, is_active=True).count(),
        "surveys_total": sum(by_status.values()),
        "surveys_published": by_status.get("published", 0),
        "surveys_draft": by_status.get("draft", 0),
        "surveys_closed": by_status.get("closed", 0),
        "surveys_archived": by_status.get("archived", 0),
        "surveys_suspended": mongo.db_call(surveys.count_documents, {"moderation.status": "suspended"}),
        "responses_total": mongo.db_call(responses.count_documents, {}),
        "responses_24h": mongo.db_call(responses.count_documents, {"submitted_at": {"$gte": utc_now() - timedelta(hours=24)}}),
    }


def _bucket(day, granularity):
    if granularity == "week":
        return day - timedelta(days=day.weekday())      # Monday of that week
    if granularity == "month":
        return day.replace(day=1)
    return day


def _bucket_starts(granularity, today):
    count = GRANULARITIES[granularity]
    if granularity == "day":
        return [today - timedelta(days=count - 1 - i) for i in range(count)]
    if granularity == "week":
        monday = _bucket(today, "week")
        return [monday - timedelta(weeks=count - 1 - i) for i in range(count)]
    starts, current = [], today.replace(day=1)
    for _ in range(count):
        starts.append(current)
        current = (current - timedelta(days=1)).replace(day=1)
    return starts[::-1]


def activity(granularity="day"):
    """New users, new surveys and responses per day / week / month.

    Timestamps since the first bucket are fetched (projection only) and
    counted in Python after converting to the local time zone."""
    if granularity not in GRANULARITIES:
        granularity = "day"
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    starts = _bucket_starts(granularity, today)
    since = datetime.combine(starts[0], time.min, tzinfo=tz)

    def count(timestamps):
        counts = {}
        for ts in timestamps:
            key = _bucket(as_aware(ts).astimezone(tz).date(), granularity)
            counts[key] = counts.get(key, 0) + 1
        return [counts.get(start, 0) for start in starts]

    users = User.objects.filter(date_joined__gte=since).values_list("date_joined", flat=True)
    surveys = mongo.db_call(list, mongo.surveys().find({"created_at": {"$gte": since}}, {"created_at": 1}))
    responses = mongo.db_call(list, mongo.responses().find({"submitted_at": {"$gte": since}}, {"submitted_at": 1}))
    label = {"day": "%d %b", "week": "w/c %d %b", "month": "%b %Y"}[granularity]
    return {
        "granularity": granularity,
        "labels": [s.strftime(label) for s in starts],
        "registrations": count(users),
        "surveys": count(d["created_at"] for d in surveys),
        "responses": count(d["submitted_at"] for d in responses),
    }


def recent_registrations(limit=6):
    return list(User.objects.order_by("-date_joined")[:limit])


def recent_publications(limit=6):
    cursor = mongo.surveys().find({"published_at": {"$ne": None}}, {"questions": 0}).sort("published_at", -1).limit(limit)
    surveys = [prepare(d) for d in mongo.db_call(list, cursor)]
    attach_owner_names(surveys)
    return surveys


def attach_owner_names(surveys):
    names = dict(User.objects.filter(id__in={s["owner_id"] for s in surveys}).values_list("id", "username"))
    for survey in surveys:
        survey["owner_username"] = names.get(survey["owner_id"], "(deleted user)")


def health():
    ok = mongo.ping()
    info = {"mongodb": ok, "collections": {}}
    if ok:
        for name in ("surveys", "responses", "response_sessions", "audit_logs"):
            info["collections"][name] = mongo.db_call(mongo.collection(name).count_documents, {})
        try:
            info["mongodb_version"] = mongo.get_db().command("buildInfo").get("version", "")
        except Exception:  # not available in every environment (e.g. tests)
            info["mongodb_version"] = ""
    return info


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

def search_users(q="", status="", role=""):
    users = User.objects.all().order_by("-date_joined")
    q = normalize_text(q)[:100]
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q))
    if status == "active":
        users = users.filter(is_active=True)
    elif status == "suspended":
        users = users.filter(is_active=False)
    if role == "admin":
        users = users.filter(is_staff=True)
    elif role == "creator":
        users = users.filter(is_staff=False)
    return users


def survey_counts(user_ids):
    rows = mongo.db_call(mongo.surveys().aggregate, [
        {"$match": {"owner_id": {"$in": list(user_ids)}}},
        {"$group": {"_id": "$owner_id", "count": {"$sum": 1}, "responses": {"$sum": "$response_count"}}},
    ])
    return {row["_id"]: row for row in rows}


def get_user(user_id):
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        raise NotFound("User not found.")


def set_user_status(actor, user_id, action, reason="", request=None):
    """Suspend (is_active=False) or reactivate a user.

    Rules: you cannot suspend yourself; only a superuser can suspend another
    administrator or a superuser."""
    target = get_user(user_id)
    if action not in ("suspend", "activate"):
        raise ValidationFailed("Unknown action.")
    if target.pk == actor.pk:
        raise PermissionDeniedError("You cannot change the status of your own account.")
    if (target.is_staff or target.is_superuser) and not actor.is_superuser:
        raise PermissionDeniedError("Only a superuser can suspend or reactivate an administrator.")
    reason = normalize_text(reason, 300)
    if action == "suspend" and len(reason) < 5:
        raise ValidationFailed("Give a reason (at least 5 characters) for suspending this account.")
    target.is_active = action == "activate"
    target.save(update_fields=["is_active"])
    audit.log(actor, "admin.user_suspend" if action == "suspend" else "admin.user_activate",
              "user", target.pk, {"username": target.username, "reason": reason}, request)
    return target


def set_admin_role(actor, user_id, grant, request=None):
    if not actor.is_superuser:
        raise PermissionDeniedError("Only a superuser can grant or revoke the administrator role.")
    target = get_user(user_id)
    if target.pk == actor.pk:
        raise PermissionDeniedError("You cannot change your own role.")
    if target.is_superuser and not grant:
        raise PermissionDeniedError("Superusers keep administrator access; remove superuser status from the command line.")
    target.is_staff = bool(grant)
    target.save(update_fields=["is_staff"])
    audit.log(actor, "admin.user_grant_admin" if grant else "admin.user_revoke_admin", "user", target.pk,
              {"username": target.username}, request)
    return target


# --------------------------------------------------------------------------
# Survey moderation
# --------------------------------------------------------------------------

def search_surveys(q="", status="", moderation="", page=1, page_size=15):
    query = {}
    term = normalize_text(q)[:100]
    if term:
        query["title"] = {"$regex": re.escape(term), "$options": "i"}
    if status in ("draft", "published", "closed", "archived"):
        query["status"] = status
    if moderation == "suspended":
        query["moderation.status"] = "suspended"
    total = mongo.db_call(mongo.surveys().count_documents, query)
    cursor = (mongo.surveys().find(query, {"questions": 0}).sort("created_at", -1)
              .skip((page - 1) * page_size).limit(page_size))
    surveys = [prepare(d) for d in mongo.db_call(list, cursor)]
    attach_owner_names(surveys)
    return surveys, total


def surveys_of_user(user_id, limit=50):
    cursor = mongo.surveys().find({"owner_id": user_id}, {"questions": 0}).sort("created_at", -1).limit(limit)
    return [prepare(d) for d in mongo.db_call(list, cursor)]


def get_survey(survey_id):
    doc = mongo.db_call(mongo.surveys().find_one, {"_id": mongo.to_object_id(survey_id, "Survey not found.")})
    if doc is None:
        raise NotFound("Survey not found.")
    survey = prepare(doc)
    attach_owner_names([survey])
    return survey


def moderate_survey(actor, survey_id, action, reason="", request=None):
    """suspend: hide from respondents and block publishing (reason required)
    unsuspend: lift the suspension
    archive: move to archived (stops responses, hidden from the owner's list)"""
    survey = get_survey(survey_id)
    reason = normalize_text(reason, 300)
    now = utc_now()
    if action == "suspend":
        if len(reason) < 5:
            raise ValidationFailed("Give a reason (at least 5 characters). The owner will see it.")
        update = {"moderation": {"status": "suspended", "reason": reason, "by": actor.username, "at": now}}
        audit_action = "admin.survey_suspend"
    elif action == "unsuspend":
        update = {"moderation": {"status": "ok", "reason": "", "by": actor.username, "at": now}}
        audit_action = "admin.survey_unsuspend"
    elif action == "archive":
        update = {"status": "archived", "archived_at": now, "status_before_archive": survey["status"]}
        audit_action = "admin.survey_archive"
    else:
        raise ValidationFailed("Unknown moderation action.")
    mongo.db_call(mongo.surveys().update_one, {"_id": survey["_id"]}, {"$set": {**update, "updated_at": now}})
    audit.log(actor, audit_action, "survey", survey["_id"],
              {"title": survey["title"], "owner": survey["owner_username"], "reason": reason}, request)

