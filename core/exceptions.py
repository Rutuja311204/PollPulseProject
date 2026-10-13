"""
Custom exceptions shared by all PollPulse apps.

The service layer raises these; views and API views translate them into
HTTP responses (404 page, 403 page, form error, JSON error ...). This keeps
business rules independent of HTTP.
"""


class PollPulseError(Exception):
    """Base class: catch every project error with one `except PollPulseError`."""

    default_message = "Something went wrong."
    status_code = 400  # used by the API layer

    def __init__(self, message=None):
        self.message = message or self.default_message
        super().__init__(self.message)


class ValidationFailed(PollPulseError):
    """Input is invalid. Carries every problem so the user can fix all at once.

    `errors` is a list of messages; `field_errors` optionally maps a field or
    question id to its own messages (used to highlight inputs).
    """

    default_message = "Please correct the highlighted problems."

    def __init__(self, errors=None, field_errors=None):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors or [])
        self.field_errors = dict(field_errors or {})
        if not self.errors:
            self.errors = [msg for msgs in self.field_errors.values() for msg in msgs]
        super().__init__("; ".join(self.errors) or self.default_message)


class NotFound(PollPulseError):
    default_message = "Not found."
    status_code = 404


class PermissionDeniedError(PollPulseError):
    default_message = "You don't have permission to do that."
    status_code = 403


class ConflictError(PollPulseError):
    """The action is valid in general but not in the current state
    (e.g. editing questions after responses arrived)."""

    default_message = "This action conflicts with the current state."
    status_code = 409


class SurveyUnavailable(PollPulseError):
    """The survey exists but cannot accept responses right now.
    `reason` is a short code: draft, not_started, ended, closed, archived,
    limit_reached, suspended, login_required."""

    status_code = 403

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class DuplicateResponse(PollPulseError):
    default_message = "A response from you has already been recorded for this survey."
    status_code = 409


class RateLimited(PollPulseError):
    default_message = "Too many attempts. Please wait a few minutes and try again."
    status_code = 429


class DatabaseUnavailable(PollPulseError):
    default_message = "The database is not reachable. Please make sure MongoDB is running."
    status_code = 503
