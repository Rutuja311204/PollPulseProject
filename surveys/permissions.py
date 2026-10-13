"""
Survey-level role-based access control.

A survey has one OWNER and optional COLLABORATORS with the role EDITOR or
VIEWER. The table below is the single source of truth for who may do what;
services call `require(survey, user_id, action)` before every operation.

                    owner  editor  viewer
  view / results      ✓      ✓       ✓
  export              ✓      ✓       ✓
  edit questions      ✓      ✓
  edit settings       ✓      ✓
  publish / close     ✓      ✓
  duplicate           ✓      ✓
  archive / restore   ✓
  delete              ✓
  collaborators       ✓

Platform administrators (Django `is_staff`) are a separate, platform-wide
role handled by the admin panel; they do not automatically get survey roles.
"""
from core.exceptions import NotFound, PermissionDeniedError

OWNER, EDITOR, VIEWER = "owner", "editor", "viewer"
COLLABORATOR_ROLES = (EDITOR, VIEWER)

PERMISSIONS = {
    "view": {OWNER, EDITOR, VIEWER},
    "export": {OWNER, EDITOR, VIEWER},
    "edit": {OWNER, EDITOR},
    "publish": {OWNER, EDITOR},
    "duplicate": {OWNER, EDITOR},
    "archive": {OWNER},
    "delete": {OWNER},
    "manage_collaborators": {OWNER},
}


def role_for(survey, user_id):
    """Return 'owner', 'editor', 'viewer' or None for this user."""
    if survey is None or user_id is None:
        return None
    if survey.get("owner_id") == user_id:
        return OWNER
    for collaborator in survey.get("collaborators", []):
        if collaborator.get("user_id") == user_id:
            return collaborator.get("role")
    return None


def can(survey, user_id, action):
    return role_for(survey, user_id) in PERMISSIONS[action]


def require(survey, user_id, action):
    """Raise if the user may not perform `action`.

    Non-members get NotFound (404) so we do not even reveal that the survey
    exists; members without enough rights get PermissionDeniedError (403).
    """
    role = role_for(survey, user_id)
    if role is None:
        raise NotFound("Survey not found.")
    if role not in PERMISSIONS[action]:
        raise PermissionDeniedError(f"Your role on this survey ({role}) does not allow this action.")
    return role


def membership_filter(user_id):
    """MongoDB filter matching every survey this user owns or collaborates on."""
    return {"$or": [{"owner_id": user_id}, {"collaborators.user_id": user_id}]}
