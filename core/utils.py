"""Small helpers used across the project."""
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timezone

from django.conf import settings


def utc_now():
    """Current UTC time (stored in MongoDB as a BSON date)."""
    return datetime.now(timezone.utc)


def as_aware(value):
    """Some drivers return naive UTC datetimes; make them timezone-aware."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def new_id(prefix):
    """Short random id for questions/options, e.g. 'q_3f9a1c2b'."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def new_token(nbytes=9):
    """Unpredictable URL-safe token (9 bytes -> 12 characters).
    Used for public survey links and resume links instead of database ids."""
    return secrets.token_urlsafe(nbytes)


def normalize_text(value, max_length=None):
    """Trim and collapse whitespace: '  a   b ' -> 'a b'."""
    text = " ".join(str(value or "").split())
    return text[:max_length] if max_length else text


def clean_multiline(value, max_length=None):
    """Keep line breaks but trim each end and remove trailing spaces."""
    text = "\n".join(line.rstrip() for line in str(value or "").strip().splitlines())
    return text[:max_length] if max_length else text


def keyed_hash(value):
    """One-way HMAC of a value with the secret key.

    Used for anonymous surveys: we can still recognise "this user already
    answered" without storing who the user is."""
    return hmac.new(settings.SECRET_KEY.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:32]


SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_filename(title, fallback="survey"):
    return SLUG_RE.sub("-", (title or "").lower()).strip("-")[:40] or fallback


def client_ip(request):
    """Best-effort client IP (REMOTE_ADDR). We do not trust X-Forwarded-For
    unless the deployment sets TRUST_X_FORWARDED_FOR behind a known proxy."""
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")
