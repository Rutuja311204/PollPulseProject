# PollPulse REST API

Base URL: `http://127.0.0.1:8000/api/` (JSON in, JSON out). Implemented with Django REST Framework in `surveys/api/` and `adminpanel/api.py`.

## Authentication, CSRF and throttling

- The API uses the same **session login** as the website (`SessionAuthentication`). Sign in through `/accounts/login/` first; the browser then sends the session cookie.
- For `POST`, `PUT`, `PATCH` and `DELETE` by a signed-in user, send the CSRF token from the `csrftoken` cookie in the `X-CSRFToken` header. Requests without it get **403**.
- Endpoints require a signed-in user unless marked **public**. Admin endpoints require an administrator (`is_staff`).
- Throttles (configurable in `.env`): anonymous 120/hour, signed-in 2000/hour, public submissions 30/hour, AI endpoints 30/hour. Exceeding a limit returns **429**.
- In development (`DJANGO_DEBUG=True`) you can open GET endpoints in the browser to use DRF's browsable API.

## Errors

```json
{"detail": "Readable summary", "errors": ["..."], "field_errors": {"q_b1ac794f": ["..."]}, "reason": "closed"}
```

| Status | Meaning |
|---|---|
| 400 | Invalid data (`errors`, and `field_errors` per question or field) |
| 401 / 403 | Not signed in, missing CSRF token, survey not accepting responses (`reason`), or role not allowed |
| 404 | Not found **or not shared with you** (the API does not reveal private surveys) |
| 409 | Conflict: duplicate response, questions locked, wrong state |
| 429 | Rate limit |
| 503 | MongoDB unreachable |

Survey ids are MongoDB ObjectIds (24 hex characters). Public endpoints use the survey's random `public_token` instead.

---

## Surveys (creators)

### `GET /api/surveys/`
Query: `q` (title search), `status` (`draft|published|closed|archived`), `scope` (`all|owned|shared`), `sort` (`newest|oldest|updated|responses|title`), `page`, `page_size` (max 100).

```json
{"count": 4, "results": [{"id": "6acba5e6df8c4eebec9afc8e", "title": "Canteen feedback", "status": "published",
  "role": "owner", "question_count": 2, "response_count": 12, "accepting": true, "public_token": "C8M2D7grTtdZ",
  "created_at": "2026-10-11T20:36:14+05:30", "updated_at": "2026-10-11T20:40:02+05:30"}]}
```

### `POST /api/surveys/` → 201
```json
{"title": "Canteen feedback", "description": "Help us improve"}
```
Response: the full survey (see GET detail), status `draft`.

### `GET /api/surveys/<id>/` (viewer, editor, owner)
```json
{"id": "6acba5e6df8c4eebec9afc8e", "title": "Canteen feedback", "status": "draft", "role": "owner",
 "question_count": 0, "response_count": 0, "accepting": false, "public_token": "C8M2D7grTtdZ",
 "description": "Help us improve",
 "settings": {"access": "public", "identity": "anonymous", "duplicate_protection": "session", "allow_edit": false,
              "allow_save_resume": true, "display_mode": "all", "start_at": null, "end_at": null,
              "max_responses": null, "results_public": false, "thank_you_message": "", "consent_text": ""},
 "questions": [], "collaborators": [], "created_at": "...", "updated_at": "..."}
```

### `PATCH /api/surveys/<id>/` (editor+)
`{"title": "New title", "description": "..."}` (either field).

### `DELETE /api/surveys/<id>/` (owner)
`{"deleted": true, "responses_removed": 12}`

### `PUT /api/surveys/<id>/settings/` (editor+)
All fields required except the optional ones shown with `null`/`""`:
```json
{"access": "login", "identity": "identified", "duplicate_protection": "user", "display_mode": "one_per_page",
 "allow_edit": true, "allow_save_resume": true, "results_public": false,
 "start_at": "2026-11-01T09:00:00+05:30", "end_at": null, "max_responses": 200,
 "thank_you_message": "Thanks!", "consent_text": ""}
```
Combination rules are enforced, e.g. `identity: identified` with `access: public` returns 400 *"Identified responses require respondents to sign in"*.

### Status actions — `POST /api/surveys/<id>/<action>/`
| Action | Role | Notes |
|---|---|---|
| `publish` | editor+ | also reopens a closed survey; **400** with `errors` if the quality checker finds blocking problems |
| `close` | editor+ | only from `published` |
| `archive` / `restore` | owner | restore returns the survey as `closed` (or `draft` if never published) |
| `duplicate` | editor+ | **201** `{"id": "<new draft id>"}` |

Response: `{"id": "...", "status": "published", "accepting": true}`

## Questions (editor+ to change)

Question object:
```json
{"qid": "q_686b32dc", "type": "single_choice", "title": "How often do you eat here?", "description": "",
 "required": true, "config": {"allow_other": false}, "options": [{"oid": "o_a5e4f2c3", "text": "Daily"}]}
```
`type` is one of `single_choice, multiple_choice, short_text, long_text, email, number, rating, linear_scale, dropdown, yes_no, date, ranking`.

| Type | `config` keys |
|---|---|
| single_choice | `allow_other` |
| multiple_choice | `allow_other`, `min_select`, `max_select` |
| short_text / long_text | `min_length`, `max_length` (≤ 500 / ≤ 5000) |
| number | `min_value`, `max_value`, `integer_only` |
| rating | `max` (3–10 stars) |
| linear_scale | `min` (0 or 1), `max` (2–10), `min_label`, `max_label` |
| date | `min_date`, `max_date` (`YYYY-MM-DD`) |
| dropdown, ranking, yes_no, email | — |

### `GET /api/surveys/<id>/questions/`
`{"questions": [...], "locked": false}` — `locked` is true once responses exist.

### `PUT /api/surveys/<id>/questions/` — replace the whole list (used by the builder)
```json
{"questions": [
  {"type": "rating", "title": "Rate the food", "required": true, "config": {"max": 5}},
  {"type": "single_choice", "title": "How often do you eat here?", "required": true,
   "options": [{"text": "Daily"}, {"text": "Weekly"}]}
]}
```
Response: normalised questions with generated `qid`/`oid` plus the quality check:
```json
{"questions": [{"qid": "q_b1ac794f", "type": "rating", "...": "..."}],
 "issues": [{"level": "tip", "code": "vague", "message": "...", "suggestion": "...", "qid": "q_...", "number": 2}]}
```
Send existing `qid`/`oid` values back to keep them. **409** once the survey has responses.

### Other question endpoints
- `POST /api/surveys/<id>/questions/` — add one question (body: a question object) → 201
- `PATCH /api/surveys/<id>/questions/<qid>/` — change some fields → 200
- `DELETE /api/surveys/<id>/questions/<qid>/` → 204
- `POST /api/surveys/<id>/questions/reorder/` — `{"order": ["q_...", "q_..."]}` (every qid exactly once)
- `GET /api/surveys/<id>/quality/` — `{"summary": {"error": 0, "warning": 1, "tip": 2}, "issues": [...]}`

## Analytics and exports (viewer+)

### `GET /api/surveys/<id>/analytics/?start=2026-10-01&end=2026-10-31`
```json
{"survey_id": "...", "title": "Canteen feedback", "total_responses": 38,
 "completion": {"started": 44, "completed": 38, "abandoned": 6, "in_progress": 0, "completion_rate": 86.4, "abandonment_rate": 13.6},
 "timing": {"count": 38, "median_seconds": 214, "mean_seconds": 226},
 "trend": [{"date": "2026-10-11", "label": "11 Oct", "count": 3}],
 "questions": [
   {"qid": "q_b1ac794f", "number": 1, "title": "Rate the food", "type": "rating", "kind": "scale",
    "answered": 38, "missing": 0, "participation_pct": 100.0, "low": 1, "high": 5,
    "options": [{"key": 4, "label": "4", "count": 10, "pct": 26.3}],
    "stats": {"count": 38, "mean": 3.13, "median": 3.0, "min": 1, "max": 5, "stdev": 1.07}, "top_two_pct": 36.8}
 ],
 "insights": [{"kind": "volume", "text": "38 responses were collected between 28 Sep 2026 and 11 Oct 2026."}]}
```
Per-question fields by `kind`: `choice`/`multi` → `options`, `mode`; `scale` → `options`, `stats`, `top_two_pct`; `numeric` → `stats`, `histogram`; `date` → `earliest`, `latest`, `by_month`; `ranking` → `ranking[]` with `avg_rank`, `first_place_pct`, `score`; `text` → `text.keywords`, `phrases`, `topics`, `sentiment`; `email` → `domains`. Individual text answers are not included in the API response.

### `POST /api/surveys/<id>/analytics/ai-summary/`
Only when an AI service is configured. `{"summary": "...", "model": "gpt-4o-mini via https://..."}`, or **400** if AI is not configured, or **503** if the service did not respond.

### `GET /api/surveys/<id>/export/responses.csv` · `GET /api/surveys/<id>/export/analytics.csv`
CSV downloads (UTF-8 with BOM, formula-injection safe). The same optional `start`/`end` filters apply. Each export is written to the audit log.

### `POST /api/ai/suggest-questions/`
```json
{"topic": "college canteen", "objective": "improve service"}
```
```json
{"source": "template", "note": "Built-in templates for a 'service' survey (no AI service configured).",
 "questions": [{"qid": "q_...", "type": "rating", "title": "How satisfied were you with the college canteen?", "config": {"max": 5}, "options": [], "rationale": ""}]}
```
`source` is `"ai"` only when a configured AI service answered with valid questions; every suggestion is validated like builder input.

## Public endpoints (no login)

### `GET /api/public/surveys/<token>/`
Only for published or closed surveys (404 for drafts, archived or suspended ones). It never includes owner, collaborators, counts or settings other than consent text.
```json
{"token": "C8M2D7grTtdZ", "title": "Canteen feedback", "description": "Help us improve",
 "questions": [{"qid": "q_b1ac794f", "type": "rating", "title": "Rate the food", "required": true, "config": {"max": 5}, "options": [], "description": ""}],
 "consent_text": "", "accepting": true}
```

### `POST /api/public/surveys/<token>/responses/`
```json
{"answers": [
   {"qid": "q_b1ac794f", "value": 4},
   {"qid": "q_686b32dc", "value": "o_a5e4f2c3"},
   {"qid": "q_multi001", "value": ["o_x1", "o_x2"]},
   {"qid": "q_rank0001", "value": ["o_c", "o_a", "o_b"]},
   {"qid": "q_other001", "value": "__other__", "other_text": "Instagram"}
 ],
 "consent": true}
```
| Result | Status | Body |
|---|---|---|
| Saved | 201 | `{"response_id": "...", "edited": false, "detail": "Thank you! ..."}` |
| Updated (editing allowed) | 200 | `{"response_id": "...", "edited": true, ...}` |
| Invalid answers | 400 | `{"errors": [...], "field_errors": {"q_b1ac794f": ["Question 1: Choose a value from 1 to 5."]}}` |
| Closed / not started / ended / limit / suspended / login required | 403 | `{"detail": "...", "reason": "closed"}` |
| Already answered | 409 | `{"detail": "A response from you has already been recorded for this survey."}` |

Duplicate protection over the API relies on the session cookie (or the signed-in user). A client that discards cookies is limited only by throttling.

## Administrator endpoints (`is_staff` required)

| Method | Endpoint | Body / query | Response |
|---|---|---|---|
| GET | `/api/admin/stats/?period=day\|week\|month` | — | `{"overview": {"users_total": 3, ...}, "activity": {"labels": [...], "registrations": [...], "surveys": [...], "responses": [...]}}` |
| GET | `/api/admin/users/?q=&status=active\|suspended&role=admin\|creator&page=` | — | `{"count": 3, "results": [{"id": 2, "username": "demo", "is_active": true, "is_admin": false, ...}]}` |
| POST | `/api/admin/users/<id>/status/` | `{"action": "suspend", "reason": "Spam surveys"}` or `{"action": "activate"}` | `{"id": 2, "is_active": false}` |
| POST | `/api/admin/surveys/<id>/moderation/` | `{"action": "suspend", "reason": "Collects personal data"}`, `unsuspend` or `archive` | `{"id": "...", "status": "published", "moderation": {"status": "suspended", ...}}` |
| GET | `/api/admin/audit-logs/?action=&actor=&page=` | — | `{"count": 12, "results": [{"action": "admin.user_suspend", "actor_username": "root", ...}]}` |

Rules enforced on the server: administrators cannot suspend themselves; only superusers can suspend other administrators or change roles; suspension reasons need at least 5 characters.
