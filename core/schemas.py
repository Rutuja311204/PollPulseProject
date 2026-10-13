"""
MongoDB JSON-Schema validators (applied by `python manage.py init_mongo`).

The application validates everything in Python first; these database-level
rules are a second safety net that also documents the document structure.
They are deliberately permissive about optional fields.

validationAction comes from MONGO_SCHEMA_ACTION:
  "warn"  (default) – invalid writes are accepted but logged by MongoDB
  "error"           – invalid writes are rejected
"""
NUMBER = ["int", "long", "double", "decimal"]

SURVEYS = {
    "bsonType": "object",
    "required": ["title", "owner_id", "status", "settings", "questions", "public_token", "created_at"],
    "properties": {
        "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
        "owner_id": {"bsonType": NUMBER},
        "public_token": {"bsonType": "string", "minLength": 8},
        "status": {"enum": ["draft", "published", "closed", "archived"]},
        "settings": {"bsonType": "object"},
        "response_count": {"bsonType": NUMBER, "minimum": 0},
        "questions": {
            "bsonType": "array",
            "maxItems": 50,
            "items": {
                "bsonType": "object",
                "required": ["qid", "type", "title", "required", "config", "options"],
                "properties": {
                    "qid": {"bsonType": "string"},
                    "type": {"enum": ["single_choice", "multiple_choice", "short_text", "long_text", "email", "number",
                                      "rating", "linear_scale", "dropdown", "yes_no", "date", "ranking"]},
                    "title": {"bsonType": "string"},
                    "required": {"bsonType": "bool"},
                    "config": {"bsonType": "object"},
                    "options": {"bsonType": "array"},
                },
            },
        },
        "collaborators": {"bsonType": "array"},
        "created_at": {"bsonType": "date"},
    },
}

RESPONSES = {
    "bsonType": "object",
    "required": ["survey_id", "dedupe_key", "answers", "submitted_at"],
    "properties": {
        "survey_id": {"bsonType": "objectId"},
        "dedupe_key": {"bsonType": "string"},
        "answers": {
            "bsonType": "array",
            "items": {"bsonType": "object", "required": ["qid", "type", "value"]},
        },
        "submitted_at": {"bsonType": "date"},
        "duration_seconds": {"bsonType": NUMBER + ["null"]},
    },
}

SESSIONS = {
    "bsonType": "object",
    "required": ["survey_id", "respondent_key", "status", "resume_token", "started_at"],
    "properties": {
        "survey_id": {"bsonType": "objectId"},
        "status": {"enum": ["in_progress", "completed"]},
        "started_at": {"bsonType": "date"},
    },
}

AUDIT_LOGS = {
    "bsonType": "object",
    "required": ["action", "created_at"],
    "properties": {"action": {"bsonType": "string"}, "created_at": {"bsonType": "date"}},
}

VALIDATORS = {"surveys": SURVEYS, "responses": RESPONSES, "response_sessions": SESSIONS, "audit_logs": AUDIT_LOGS}
