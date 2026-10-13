# PollPulse — Survey Intelligence and Analytics Platform

PollPulse is a web platform for creating surveys with twelve question types, collecting validated responses, and analysing them with statistics that explain how they were calculated. It has three roles: survey creators, respondents and platform administrators.

| | |
|---|---|
| **Student** | Rutuja Yashwant Divate |
| **Course** | MCA |
| **Roll number** | 15 |
| **Project** | PollPulse (Python full-stack case study) |

---

## 1. Problem statement

Basic form builders make it easy to collect answers but leave the hard parts to the user:

- Badly worded questions (leading, double-barrelled or vague) are published without warning, so the data is biased before the first response.
- Most question types are free text or plain multiple choice, so answers such as ratings, rankings or numbers are not validated.
- Results stop at counts and pie charts. They do not show completion rate, time to complete, or relationships between answers, and they do not explain what a percentage is a percentage *of*.
- There is little control over who may answer, when, how often, or who in a team may edit or only view.

## 2. Objectives

1. Provide a survey builder with genuinely different question types, each with its own data structure, interface and server-side validation.
2. Check survey quality with transparent rules before publishing.
3. Enforce survey settings on the server: schedule, response limits, duplicate protection, consent, editing and save-and-resume.
4. Produce analytics from stored responses with every metric defined, plus cross-tabulation, comparison and text analysis.
5. Separate creator, respondent and administrator roles, with survey-level collaborator roles and an audited admin panel.
6. Follow secure practices: hashed passwords, CSRF protection, ownership checks, rate limits, a Content-Security-Policy and safe CSV exports.

## 3. Main features

**Creators**
- Dashboard with totals, a 30-day response trend, recently updated and most-answered surveys, recent activity and quick actions.
- Survey list with search, status and ownership filters, five sort orders and pagination.
- Builder with a live preview. You can add, duplicate, delete and reorder questions and options, set required and help text, and configure type-specific rules. It also has template or AI question suggestions and a quality-check panel.
- Life cycle: draft → published → closed → reopened → archived → restored, plus duplicate and delete. Deleting requires typing the survey title.
- Settings: access (public or signed-in), anonymous or identified, duplicate protection, response limit, start and end dates, display mode, save-and-resume, response editing, public results, consent text and thank-you message.
- Collaborators with editor or viewer roles.
- Analytics: metrics strip, daily trend, per-question charts and statistics, text analysis, cross-tabulation with a chi-square test, a rule-based summary and an optional AI rewrite.
- Responses browser, CSV exports (responses and analytics), a printable report, and comparison between two surveys.

**Respondents**
- Public link uses a random 12-character token, not the database id. No account is needed for public surveys.
- All-questions or one-question-at-a-time layout with a progress bar and required markers.
- Accessible controls for every type: stars, numbered scales, ranking with move buttons and a no-JavaScript fallback, and "Other" with text.
- Client-side hints, with full server-side validation.
- Save and finish later, using a private resume link that works on another device.
- Confirmation page with a custom message. Respondents can edit their response if the creator allows it, and see public results if enabled.

**Administrators** (`/admin-panel/`)
- Platform overview: users, surveys, responses, daily, weekly and monthly activity charts, recent registrations and publications, and MongoDB health.
- User management: search, filters, pagination, suspend and reactivate with a reason, grant and revoke the admin role (superusers only).
- Survey moderation: search and filters, read-only content view, suspend with a reason, lift a suspension, archive.
- Audit log viewer with action and user filters.
- Platform settings: open or close registration, an announcement banner, and the maximum surveys per user.

## 4. Technology stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 / 3.13 |
| Web framework | Django 5.2 LTS |
| REST API | Django REST Framework 3.16 |
| Survey data | MongoDB 7 or 8 via PyMongo 4.17+ |
| Users and sessions | SQLite through Django's ORM (`auth_user`, `django_session` only) |
| Frontend | Django templates, custom CSS on Bootstrap 5.3 (grid, modal, dropdown), vanilla JavaScript |
| Charts | Chart.js 4.5 |
| Fonts and icons | Bricolage Grotesque, Instrument Sans, Bootstrap Icons — all bundled in `static/vendor/`, so the app works offline |
| Tests | Django test runner + mongomock (in-memory MongoDB) |

No package was added compared with v1 apart from what was already listed. The optional AI client uses Python's built-in `urllib`.

## 5. System architecture

```
Browser ──HTTP──► Django middleware (security headers + CSP, sessions, CSRF, auth, MongoDB-error page)
                     │
                     ├─ core/        landing, health, error pages, Mongo connection, audit log,
                     │               rate limiting, platform settings, roles, management commands
                     ├─ accounts/    register, login (rate limited), logout, profile, password reset
                     ├─ surveys/     creator pages, builder, public respondent pages, REST API
                     └─ adminpanel/  admin dashboard, users, moderation, audit, settings
                            │
            views (thin) ───┴──► services (business rules)
                                   survey_service.py    life cycle, settings, collaborators, listing
                                   response_service.py  attempts, drafts, validation, duplicates, limits
                                   question_types.py    one class per question type
                                   permissions.py       owner / editor / viewer matrix
                                   analytics.py         metrics, trend, crosstab + chi-square, comparison
                                   text_analysis.py     keywords, phrases, topics, sentiment
                                   quality.py           survey quality rules
                                   insights.py          rule-based summary sentences
                                   ai.py                optional OpenAI-compatible client + templates
                                   exporters.py         safe CSV writers
                                   │
                         core/mongo.py (one MongoClient, indexes) ──► MongoDB
                         Django ORM ──► SQLite (users, sessions)
```

Views only read the request, call a service and render. The same services are used by HTML views, the REST API, management commands and tests.

## 6. Database design

### Why two databases

Django's authentication (password hashing, sessions, permissions) runs on the Django ORM, which needs a relational database without third-party adapters. SQLite therefore stores **only** users and sessions. Everything about surveys lives in MongoDB. A survey stores its owner's Django user id (`owner_id`).

A workflow that touches both (for example, a user's surveys in the admin panel) is **not** an atomic cross-database transaction. Usernames are looked up from SQLite when needed rather than copied into MongoDB.

### MongoDB collections

| Collection | One document per | Purpose |
|---|---|---|
| `surveys` | survey | title, settings, status, **embedded questions and options**, collaborators, response counter, moderation state |
| `responses` | submitted response | **embedded typed answers**, timing, dedupe key, optional identity |
| `response_sessions` | visit ("attempt") | started/completed status, save-and-resume draft, resume token → completion rate, abandonment, timing |
| `audit_logs` | important action | who, what, target, details, IP, time (append-only) |
| `platform_settings` | (single doc `_id: "global"`) | registration switch, announcement, survey limit |

**Survey**

```json
{
  "_id": ObjectId("..."), "public_token": "O2hFJfs1vlHs",
  "owner_id": 7, "title": "Python Workshop Feedback", "description": "...",
  "status": "published",
  "settings": {"access": "public", "identity": "anonymous", "duplicate_protection": "session",
               "allow_edit": false, "allow_save_resume": true, "display_mode": "one_per_page",
               "start_at": null, "end_at": null, "max_responses": 200, "results_public": true,
               "thank_you_message": "...", "consent_text": ""},
  "questions": [
    {"qid": "q_3f9a1c2b", "type": "multiple_choice", "title": "Which sessions were useful?",
     "description": "", "required": true,
     "config": {"allow_other": false, "min_select": 0, "max_select": 3},
     "options": [{"oid": "o_1a2b3c4d", "text": "REST APIs"}, {"oid": "o_5e6f7a8b", "text": "Django models"}]},
    {"qid": "q_77aa01bc", "type": "rating", "title": "Overall rating", "required": true,
     "config": {"max": 5}, "options": []}
  ],
  "collaborators": [{"user_id": 9, "username": "reviewer", "role": "viewer", "added_at": ISODate("...")}],
  "response_count": 64, "moderation": {"status": "ok"},
  "created_at": ISODate("..."), "updated_at": ISODate("..."), "published_at": ISODate("..."),
  "closed_at": null, "archived_at": null
}
```

**Response** (one answer object per answered question; the `value` type depends on the question type)

```json
{
  "_id": ObjectId("..."), "survey_id": ObjectId("..."),
  "dedupe_key": "9f2c4e...", "respondent": null,
  "answers": [
    {"qid": "q_3f9a1c2b", "type": "multiple_choice", "value": ["o_1a2b3c4d", "o_5e6f7a8b"]},
    {"qid": "q_77aa01bc", "type": "rating", "value": 4},
    {"qid": "q_aa11bb22", "type": "single_choice", "value": "__other__", "other_text": "Instagram"},
    {"qid": "q_cc33dd44", "type": "ranking", "value": ["o_b...", "o_a...", "o_c..."]}
  ],
  "consent": false, "source": "web",
  "started_at": ISODate("..."), "submitted_at": ISODate("..."), "duration_seconds": 83, "edited_at": null
}
```

| Question type | Stored `value` |
|---|---|
| single_choice, dropdown | option id (or `__other__` + `other_text`) |
| multiple_choice | list of option ids |
| yes_no | `"yes"` / `"no"` |
| short_text, long_text, email | string |
| number | int or float |
| rating, linear_scale | int |
| date | `"YYYY-MM-DD"` |
| ranking | full ordered list of option ids |

### Design decisions

- **Questions and options are embedded in the survey.** They are always read together and are bounded (at most 50 questions × 20 options), so one read gives the whole structure. They are still separate structures with their own ids (`qid`, `oid`) and schema.
- **Responses are a separate collection with embedded answers.** Responses grow without limit, so embedding them in the survey would approach MongoDB's 16 MB document limit. Embedding answers *in the response* means a response is written with one single-document insert, which is atomic, so a half-saved response is impossible.
- **Stable ids instead of positions.** Answers reference `qid`/`oid`, so reordering questions never changes the meaning of stored answers. Questions are locked once responses exist; to change them you duplicate the survey.
- **`response_count` is an atomic counter.** It is increased with a conditional `find_one_and_update({"response_count": {"$lt": limit}}, {"$inc": ...})`, so two people cannot both take the last slot.

### Indexes (created automatically by `core/mongo.py`)

| Collection | Index | Purpose |
|---|---|---|
| surveys | `public_token` **unique** | public link lookup |
| surveys | `owner_id, created_at` | "my surveys" listing |
| surveys | `collaborators.user_id` | "shared with me" |
| surveys | `status, created_at` | admin moderation list |
| responses | `survey_id, dedupe_key` **unique** | duplicate protection enforced by the database |
| responses | `survey_id, submitted_at` | analytics by date range |
| responses | `submitted_at` | platform activity |
| response_sessions | `resume_token` **unique** | resume links |
| response_sessions | `survey_id, respondent_key, status` | find a respondent's open attempt |
| response_sessions | `survey_id, started_at` | completion statistics |
| audit_logs | `created_at`; `actor_id, created_at`; `action, created_at`; `target_id, created_at` | audit filters |

### Validation rules

1. **Application level (primary):** each question type's `clean_answer`, the settings validator and the builder normaliser in `surveys/`.
2. **Database level (safety net):** `python manage.py init_mongo` applies MongoDB JSON-Schema validators (`core/schemas.py`) to the four main collections. They use `validationAction: warn` by default. Set `MONGO_SCHEMA_ACTION=error` in `.env` to make MongoDB reject invalid writes, once you have confirmed everything works on your MongoDB version.

## 7. Installation on Windows

### Prerequisites

1. **Python 3.12 or 3.13** from python.org. Tick **"Add python.exe to PATH"** during setup.
2. **MongoDB Community Server** 7 or 8 (Windows MSI) from mongodb.com. Choose **Complete** and keep **"Install MongoD as a Service"** ticked. MongoDB Compass (optional) lets you browse the data.
3. Check MongoDB is running: Win+R → `services.msc` → **MongoDB Server** should say *Running*.

### Quick setup (script)

Extract the ZIP, open **Command Prompt** in the `pollpulse` folder and run:

```bat
setup_windows.bat
```

It creates `venv`, installs packages, creates `.env` with a random secret key, creates the SQLite tables and prepares MongoDB. Then continue at step 5 below.

### Manual setup

```bat
:: 1. Virtual environment and packages
py -3.12 -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

:: 2. Environment file
copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
::    paste the printed value after DJANGO_SECRET_KEY= in .env (Notepad)

:: 3. Django tables for users and sessions (SQLite)
python manage.py migrate

:: 4. MongoDB: check the connection, apply validators, create indexes
python manage.py init_mongo

:: 5. Create the first administrator (prompts for username, email, password)
python manage.py createsuperuser

:: 6. Optional sample data (demo creator accounts, 4 surveys, ~120 responses)
python manage.py seed_demo

:: 7. Start
python manage.py runserver
```

Open **http://127.0.0.1:8000/**. Next time, double-click `run_windows.bat` or run `venv\Scripts\activate` then `python manage.py runserver`.

If PowerShell refuses to run `activate`, use Command Prompt, or run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Expected output of `init_mongo`

```
Connecting to mongodb://localhost:27017 (database 'pollpulse') ...
MongoDB connection OK.
  validator applied to surveys (action: warn)
  ...
  surveys                 0 documents   indexes: _id_, collaborator_user, owner_created, public_token_unique, status_created
  responses               0 documents   indexes: _id_, submitted, survey_dedupe_unique, survey_submitted
  ...
MongoDB is ready.
```

## 8. Environment variables

All settings are read from `.env` (see `.env.example`, which contains no secrets).

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev fallback (refused when DEBUG is off) | signs sessions, CSRF tokens, reset links |
| `DJANGO_DEBUG` | `True` | must be `False` in production |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | host names the site answers to |
| `MONGO_URI` / `MONGO_DB_NAME` | `mongodb://localhost:27017` / `pollpulse` | MongoDB connection |
| `MONGO_TIMEOUT_MS` | `3000` | wait before showing the "database unavailable" page |
| `MONGO_SCHEMA_ACTION` | `warn` | JSON-Schema validator action (`warn` or `error`) |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | console backend | password-reset email; by default printed in the terminal |
| `AI_API_BASE`, `AI_API_KEY`, `AI_MODEL`, `AI_TIMEOUT` | empty | optional AI service (see below) |
| `TIME_ZONE` | `Asia/Kolkata` | dates shown and used for daily charts |
| `THROTTLE_*` | see settings | API rate limits |

### MongoDB configuration

- **Local (recommended for demos):** keep `MONGO_URI=mongodb://localhost:27017`.
- **MongoDB Atlas:** set `MONGO_URI` to the `mongodb+srv://...` string, run `pip install dnspython`, and allow your IP in Atlas → Network Access.
- **Browse data:** connect MongoDB Compass to the same URI and open database `pollpulse`.

## 9. Creating the initial administrator

Administrators are ordinary Django users with `is_staff = True`. No administrator account or password is stored in the code.

```bat
python manage.py createsuperuser
```

This prompts for the details and stores a hashed password. For unattended setup, Django also supports:

```bat
set DJANGO_SUPERUSER_USERNAME=admin
set DJANGO_SUPERUSER_EMAIL=admin@example.com
set DJANGO_SUPERUSER_PASSWORD=<choose a strong password>
python manage.py createsuperuser --noinput
```

Sign in normally; the sidebar shows **Admin panel**. A superuser can grant the admin role to other users under Admin panel → Users.

Sample accounts from `seed_demo` (creators, not admins): `demo` and `reviewer`, password `PollPulse@2026`, or another password set with `--password`. Use them for demonstrations only.

## 10. Running the tests

```bat
venv\Scripts\activate
python manage.py test
```

Expected: `Ran 113 tests ... OK`. The tests use an in-memory MongoDB (mongomock) and a temporary SQLite database, so they never touch your data and do not need MongoDB running.

To run the same suite against your **real local MongoDB** (it uses a separate `pollpulse_test` database and drops it after each test):

```bat
set POLLPULSE_TEST_REAL_MONGO=1
python manage.py test
set POLLPULSE_TEST_REAL_MONGO=
```

| Test module | Covers |
|---|---|
| `surveys/tests/test_question_types.py` | every type's answer validation, required/optional, config errors, id handling |
| `surveys/tests/test_surveys.py` | settings rules, life cycle, publish blocking, duplicate, locking, question operations, delete cascade, limits, owner/editor/viewer/stranger rules, search/filter/sort/pagination, dashboard counts |
| `surveys/tests/test_responses.py` | draft/closed/suspended, start/end dates, response limit (incl. atomic reservation), duplicates per session, unique-index race, no-protection mode, editing, consent, identified responses, drafts and resume, timing |
| `surveys/tests/test_analytics.py` | percentages, statistics, histogram, chi-square against known values, sentiment negation, keywords, every per-type analysis, crosstab, date filter, completion/abandonment, timing outliers, insights, comparison, quality rules |
| `surveys/tests/test_web.py` | all creator pages, login required, settings form, POST-only actions, delete confirmation, 404/403 rules, MongoDB outage page, security headers, full respondent HTML flow, escaping, drafts across devices, unavailable states, login-only surveys, preview, public results, CSV safety and permissions, REST API (CRUD, builder, publish, public submit status codes, CSRF), AI parsing and fallback |
| `adminpanel/tests.py` | admin-only routes and API, suspend/reactivate (blocks login), protection rules, role changes, moderation, registration switch, audit filters |
| `accounts/tests.py` | registration and hashing, weak passwords, duplicate email, login/logout, login rate limit, profile, password change, password-reset email |

The separate threading demonstration from v1 is still available: `python demos\threading_demo.py`.

## 11. API documentation

The full reference with sample requests and responses is in **[docs/API.md](docs/API.md)**. Summary:

| Method | Endpoint | Access |
|---|---|---|
| GET, POST | `/api/surveys/` | signed in |
| GET, PATCH, DELETE | `/api/surveys/<id>/` | role-based |
| PUT | `/api/surveys/<id>/settings/` | editor+ |
| POST | `/api/surveys/<id>/publish/` · `close/` · `archive/` · `restore/` · `duplicate/` | role-based |
| GET, PUT, POST | `/api/surveys/<id>/questions/` | view / editor+ |
| PATCH, DELETE | `/api/surveys/<id>/questions/<qid>/` | editor+ |
| POST | `/api/surveys/<id>/questions/reorder/` | editor+ |
| GET | `/api/surveys/<id>/quality/` | viewer+ |
| GET | `/api/surveys/<id>/analytics/?start=&end=` | viewer+ |
| POST | `/api/surveys/<id>/analytics/ai-summary/` | viewer+, AI configured |
| GET | `/api/surveys/<id>/export/responses.csv` · `analytics.csv` | viewer+ |
| POST | `/api/ai/suggest-questions/` | signed in |
| GET | `/api/public/surveys/<token>/` | public |
| POST | `/api/public/surveys/<token>/responses/` | public (throttled) |
| GET | `/api/admin/stats/` · `admin/users/` · `admin/audit-logs/` | admin |
| POST | `/api/admin/users/<id>/status/` · `admin/surveys/<id>/moderation/` | admin |

## 12. Optional integrations

| Integration | How to enable | Without it |
|---|---|---|
| **AI suggestions and summary rewrite** | Set `AI_API_BASE`, `AI_MODEL` (and `AI_API_KEY` if needed) for any OpenAI-compatible Chat Completions API, e.g. OpenAI or a local Ollama server (`http://localhost:11434/v1`, no key). | Built-in question templates (detected from the topic) and the rule-based summary. The UI says which source was used. |
| **Real email** | Set `EMAIL_BACKEND` to SMTP and fill the `EMAIL_*` values. | Password-reset emails are printed in the `runserver` terminal. |
| **PDF report** | Open *Reports → Printable report* and use the browser's Print → Save as PDF. | — |
| **MongoDB Atlas** | `MONGO_URI=mongodb+srv://...` plus `pip install dnspython`. | Local MongoDB. |

The AI client was tested with mocked responses only; no live AI service was available while building the project. It sends only the topic you type, or the aggregated summary sentences, never individual respondent answers.

## 13. Screenshots

Add your own screenshots after running the project with `seed_demo`. Save them in `docs/screenshots/` and link them here, for example `![Dashboard](docs/screenshots/03-dashboard.png)`. Suggested set:

1. Landing page
2. Sign in / registration
3. Creator dashboard
4. Survey builder with live preview and quality check
5. Survey settings
6. Respondent page on a phone (one question at a time)
7. Thank-you page
8. Analytics page (charts, summary, crosstab)
9. Reports and exports
10. Admin overview
11. Admin users and moderation
12. Audit log

## 14. Security considerations

| Measure | Where |
|---|---|
| Password hashing (PBKDF2-SHA256, salted) and Django's password validators | `accounts/forms.py`, settings |
| CSRF tokens on every form; logout and all state changes are POST-only; DRF enforces CSRF for session users | templates, views, settings |
| Role checks on the server: survey roles (`surveys/permissions.py`), admin role (`core/roles.py`) | every view and API |
| Insecure direct object references prevented: non-members get **404**, members without rights get **403**; public links use unguessable tokens | `permissions.require`, `find_by_token` |
| Server-side validation of every answer and setting; MongoDB JSON-Schema validators as a second layer | `question_types.py`, `survey_service.py`, `core/schemas.py` |
| Rate limiting: login (10 / 5 min / IP), registration (5 / hour / IP), public submissions (20 / 10 min / IP / survey), DRF throttles for the API and AI endpoints | `core/ratelimit.py`, settings |
| Output escaping by Django templates; builder JavaScript never uses `innerHTML` with user text | templates, `builder.js` |
| Content-Security-Policy (`script-src 'self'`, no inline scripts, no CDNs), X-Frame-Options DENY, nosniff, referrer policy | `core/middleware.py`, settings |
| Cookies: HttpOnly session cookie, SameSite=Lax, Secure when HTTPS is enabled | settings |
| CSV formula-injection protection, UTF-8 with BOM, permission check and audit entry for every export | `exporters.py`, `insights_views.py` |
| Anonymous surveys store only an HMAC hash of the respondent identifier | `response_service.py` |
| Secrets from environment variables only; `.env` git-ignored; the app refuses to start without a secret key when DEBUG is off | settings, `.gitignore` |
| Friendly 403/404/500/503 pages; tracebacks are never shown when DEBUG is off | `core/views.py`, `templates/errors/` |
| Audit log of administrative and important creator actions (append-only in the app) | `core/audit.py` |

A production checklist is in **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

## 15. Known limitations

- **Duplicate protection per browser** can be bypassed by clearing cookies or switching browsers or devices. Anonymous API clients without cookies get a new session on every call, so they are limited only by throttling. "One response per account" is the strong option.
- **No multi-document transactions.** A standalone MongoDB has none, so deleting a survey removes the survey, then its responses, then its attempts in three steps, and creating a survey is a single insert. Workflows that span SQLite and MongoDB are not atomic.
- **Questions are locked after the first response.** Making a new version means duplicating the survey; there is no answer migration.
- **Rate limits use Django's local-memory cache**, which is per process. Use Redis or Memcached when running several server processes.
- **Text analysis is English-only and lexicon-based.** Sentiment is an estimate that misses sarcasm and context; topics are limited to the built-in list.
- **Chi-square results are shown with a warning** when expected counts are below 5. Comparison between surveys is descriptive, with no significance test.
- **Analytics read the period's responses into Python.** This is fine for thousands of responses; very large surveys would need pre-aggregated statistics.
- **Live AI was not tested** (mocked only). The PDF report relies on the browser's Save as PDF.
- **Not verified on a real MongoDB server here.** The automated tests were run against mongomock. Run them once with `POLLPULSE_TEST_REAL_MONGO=1` on your machine.

## 16. Future scope

- Conditional logic (skip questions based on earlier answers) and question piping.
- Question versioning so published surveys can be edited without losing comparability.
- Email invitations with single-use tokens for strong duplicate protection.
- Scheduled report emails and webhooks.
- Multilingual surveys and multilingual text analysis.
- Pre-aggregated statistics (MongoDB aggregation pipelines or materialised views) for very large surveys.
- Server-side PDF generation and charts inside exports.
- Replica-set deployment to use MongoDB transactions for multi-step operations.

## 17. Troubleshooting

| Symptom | Fix |
|---|---|
| "The database is not reachable" page | Start the **MongoDB Server** service (`services.msc`) and check `MONGO_URI`. |
| `init_mongo` says validators could not be applied | Your MongoDB user lacks `collMod` rights. The app still works; validators are a safety net. |
| CSS missing with `DJANGO_DEBUG=False` | `runserver` serves static files only in debug mode. Use `runserver --insecure` for a quick test, or see docs/DEPLOYMENT.md. |
| Password-reset email not received | By default it is printed in the terminal running `runserver`. Configure SMTP for real email. |
| "Too many attempts" on login | Wait 5 minutes (rate limit), or restart the server in development. |

## 18. Author

**Rutuja Yashwant Divate** — MCA, Roll No. 15. Project: *PollPulse — Survey Intelligence and Analytics Platform*.

Bundled third-party assets keep their licences: Bootstrap, Bootstrap Icons, Chart.js (MIT); Bricolage Grotesque and Instrument Sans (SIL Open Font License). See `static/vendor/`.
