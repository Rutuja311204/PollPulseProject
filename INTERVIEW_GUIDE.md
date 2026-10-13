# PollPulse — Interview and Viva Guide

Everything here describes the code in this repository. File names are given so you can open the code while you study.

---

## 0. Introductions

**30 seconds.** "PollPulse is a survey platform built with Django, Django REST Framework and MongoDB. Creators build surveys from twelve question types, a rule-based checker reviews the wording before publishing, and respondents answer through a public link. Every answer is validated on the server by its question type, and the analytics page calculates completion rate, averages, rankings, cross-tabulations and text summaries from the stored responses, explaining how each number is calculated. It has creator, respondent and administrator roles, with an audited admin panel."

**One minute.** Add: "Users and sessions are in SQLite through Django's authentication; surveys, responses, visit records and audit logs are in MongoDB through PyMongo. Each question type is a Python class with its own validation, so the code never uses long if-else chains on the type. Duplicate responses are blocked by a unique MongoDB index on survey and a hashed respondent key, and response limits use an atomic conditional increment. Security includes hashed passwords, CSRF, server-side role checks that return 404 to outsiders, rate limiting, a Content-Security-Policy and formula-safe CSV exports. There are 113 automated tests."

---

## 1. Why PollPulse is better than a basic form builder

| Stage | Basic form builder | PollPulse (where in code) |
|---|---|---|
| Writing | Publishes whatever you type | Quality checker flags leading, double-barrelled, vague and absolute wording, near-duplicate questions and options, missing "Other", long questions and mixed scales; blocks publishing only for real errors (`surveys/quality.py`) |
| Question types | Mostly text and choice | 12 types, each a class with its own config, input and validation (`surveys/question_types.py`) |
| Collecting | Open until closed | Start/end dates, response limit, duplicate protection modes, consent, save-and-resume, editing, one-question-at-a-time (`survey_service.py`, `response_service.py`) |
| Results | Counts and pies | Completion and abandonment rates, timing, distributions, Borda ranking scores, histograms, chi-square crosstab, survey comparison, keyword/topic/tone analysis (`analytics.py`, `text_analysis.py`) |
| Trust | Undefined percentages | Each metric is defined on the page; the summary is produced by fixed rules from those numbers (`insights.py`) |
| Teams | Shared edit link | Owner/editor/viewer roles, admin panel, moderation, audit log (`permissions.py`, `adminpanel/`, `core/audit.py`) |

## 2. The real-world problem

Colleges, small organisations and researchers run many surveys (course feedback, event feedback, canteen or facility surveys). Their data is often unreliable because questions are leading or ask two things at once, people answer twice, surveys stay open forever, and results are reported as percentages without saying what they are a percentage of. PollPulse addresses the whole loop: better questions, controlled collection, and explainable results.

## 3. System architecture

- **Apps:** `core` (MongoDB connection, audit, rate limiting, platform settings, roles, middleware, error pages, commands), `accounts` (authentication), `surveys` (builder, responses, analytics, API), `adminpanel`.
- **Layers:** URL → view (thin) → service class/module (business rules) → `core/mongo.py` → MongoDB. Users and sessions go through the Django ORM to SQLite.
- **Request example (submitting a response):** `POST /s/<token>/` → CSRF and session middleware → `surveys/views/public.py: respond` → `respondent_for()` decides who is answering → `ResponseService.submit()` checks availability, consent, answers, duplicates and the limit → `insert_one` → redirect to the thank-you page.
- **Middleware:** `SecurityHeadersMiddleware` adds the CSP; `MongoErrorMiddleware` turns MongoDB connection errors into a 503 page (or JSON for `/api/`).

## 4. Why Django and MongoDB

**Django:** authentication with password hashing, sessions, CSRF protection, forms, templates with auto-escaping and URL routing are built in and well tested, so effort goes into survey logic. DRF adds serializers, permission classes and throttling for the API.

**MongoDB:** a survey is naturally a document — a survey has questions, a question has options, and each type has different settings (`config`). A response is a list of answers whose value types differ (number, string, list). Storing these in tables would need many joins or generic "value" columns. In MongoDB one survey or one response is one document, and questions of different types can sit in the same array.

## 5. MongoDB for flexible questions and answers — and the trade-offs

**Fits well:**
- Embedded questions/options are read in one query, and the builder saves the whole list in one `update_one` with `$set`.
- A response with all its answers is one document, so writing it is atomic.
- `config` can differ per type without schema migrations.

**Trade-offs, and how the code handles them:**
- *No enforced schema by default* → validation in Python before every write, plus JSON-Schema validators applied by `init_mongo` (`core/schemas.py`).
- *No joins* → answers reference `qid`/`oid` inside the same survey; usernames are looked up from SQLite when needed (`adminpanel/services.attach_owner_names`).
- *No multi-document transactions on a standalone server* → operations are ordered so a failure leaves harmless leftovers (delete survey first, then responses); counters use atomic operators.
- *Two databases* → cross-database work is not atomic, and the README says so.

## 6. How question types are represented and validated

`surveys/question_types.py` has a base class `QuestionType` and one subclass per type (`SingleChoice`, `MultipleChoice`, `Ranking`, `Rating`, `LinearScale`, `Number`, `DateQuestion`, `Email`, `ShortText`, `LongText`, `Dropdown`, `YesNo`). Each class implements:

- `clean_config(raw)` — validates builder settings (e.g. min ≤ max, rating 3–10 stars).
- `publish_errors(question)` — completeness (enough options, no duplicate option text).
- `parse_form(post, qid)` — reads its own HTML inputs (a ranking reads one `<select>` per option).
- `clean_answer(question, raw)` — validates one answer and returns what to store.

`REGISTRY` maps type keys to instances; code calls `get_type(q["type"]).clean_answer(...)`. That is **polymorphism**: adding a type means adding a class.

Examples of rules: single choice accepts exactly one option id that belongs to this question; multiple choice enforces `min_select`/`max_select` and rejects repeated ids; ranking must be a permutation of all option ids (or ranks 1..n each used once); email uses Django's `validate_email`; numbers respect min/max and `integer_only`; dates respect the window; text respects length limits; required questions reject empty answers; optional skipped questions are simply not stored.

`ResponseService.validate_answers()` (`response_service.py`) runs every question, rejects unknown question ids, and collects errors per question (`field_errors`) so the form highlights each one.

## 7. How duplicate submissions are prevented

1. **Who is answering** (`respondent_for`): signed-in surveys use `user:<id>`; public surveys use a random id stored in the Django session (`session:<uuid>`).
2. **What is stored:** `dedupe_key = HMAC-SHA256(SECRET_KEY, base_key)` (`core/utils.keyed_hash`), so anonymous surveys never store the user id or session id.
3. **Check:** `existing_response()` looks for the key → `DuplicateResponse` (or update, if editing is allowed).
4. **Guarantee:** a **unique index** on `(survey_id, dedupe_key)`. If two requests pass step 3 at the same moment, MongoDB rejects the second insert with `DuplicateKeyError`, which becomes `DuplicateResponse`; the reserved limit slot is released. A test simulates this race.
5. **Modes:** `session` (one per browser), `user` (one per account; requires sign-in), `none` (kiosk use: the key is random, so nothing is blocked).

Limitation: session protection can be bypassed by clearing cookies or switching browser; "one per account" is the strong option.

## 8. How role-based access control works

- **Platform roles** (`core/roles.py`): any active user is a creator; an active user with `is_staff` is an administrator. `@admin_required` and the DRF permission `IsPlatformAdmin` check this **on the server** for every admin request; hiding the link is only cosmetic. Suspension sets `is_active=False`, which Django's authentication backend refuses at login and for existing sessions.
- **Survey roles** (`surveys/permissions.py`): owner, editor, viewer in one `PERMISSIONS` table (e.g. `"delete": {OWNER}`, `"edit": {OWNER, EDITOR}`). Every `SurveyService` method calls `require(survey, user_id, action)`.
- **Insecure direct object references:** a user who is not a member gets **404** (we do not reveal the survey exists); a member without the right gets **403**. Public pages use the random `public_token`, not the ObjectId.

## 9. How analytics are calculated

`analytics.survey_report()` loads the responses in the date range and analyses each question with a function chosen by the question type's `analysis` kind:

- **Choice / dropdown / yes-no:** `Counter` of values; percent = count / respondents who answered that question × 100 (`pct()` returns 0.0 instead of dividing by zero). Options with zero votes are included. Mode = most common label(s); ties are reported as ties.
- **Multiple choice:** same counts, but percentages add up to more than 100% because each person can choose several; the page says so. Average selections per respondent.
- **Rating / linear scale:** distribution over every value in the range, mean, median, min, max, sample standard deviation (`statistics` module), and "top two values" share.
- **Number:** descriptive statistics and an equal-width histogram (the maximum goes into the last bin).
- **Ranking:** average rank (lower is better), first-place share, and a Borda score — with n items, rank 1 earns n points, rank 2 earns n−1 …, total ÷ (n × answers) × 100.
- **Text:** see section 13.
- **Participation:** answered / responses; missing = responses − answered.
- **Completion rate** = completed attempts / started attempts (from `response_sessions`, created when the page is opened). **Abandoned** = unfinished and inactive for 30 minutes. Shown as "—" when there are no attempts, never as a made-up number.
- **Completion time:** `submitted_at − started_at`; values over 24 hours (resumed drafts) are excluded; median and mean are reported.
- **Trend:** responses per local calendar day, including zero days.
- **Cross-tabulation:** counts for every pair of answers to two categorical questions, row percentages, and a **chi-square test of independence**: expected = row total × column total / n; χ² = Σ (observed − expected)² / expected; df = (r−1)(c−1); p-value from the regularised upper incomplete gamma function (`_gamma_q`, implemented with `math`); Cramér's V for strength. If any expected count is below 5, the page warns that the test is not reliable.
- **Comparison:** one question from each of two surveys; choice options are matched by text and the difference is shown in percentage points; scales and numbers compare means (descriptive only).

Complexity: one pass over responses × questions, O(R × Q); the chi-square is O(r × c).

## 10. How response data is exported safely

`surveys/exporters.py`:
- **Authorization first:** the view calls `SurveyService.get(survey_id, "export")` (owner, editor or viewer only; others get 404). Anonymous users are redirected to login.
- **Formula injection:** `safe_cell()` prefixes text starting with `=`, `+`, `-`, `@`, tab or carriage return with an apostrophe, so Excel shows it as text instead of running it. Numbers we computed stay numbers.
- **Encoding and quoting:** UTF-8 with a byte-order mark for Excel; `csv.writer` quotes commas, quotes and line breaks.
- **Privacy:** respondent usernames appear only for "identified" surveys; every export is written to the audit log.
- Two files: responses (one row per response) and analytics (counts, percentages and statistics plus the summary). The printable report is an HTML page the browser can save as PDF.

## 11. How the admin panel works

`adminpanel/` — every view has `@admin_required`, every change is POST with CSRF and is audited.
- **Overview:** users (active/suspended), surveys by status, responses (total and last 24 hours), suspended surveys, activity chart by day/week/month (`services.activity` buckets timestamps from SQLite and MongoDB), recent registrations and publications, MongoDB ping and collection counts.
- **Users:** search, filters and Django `Paginator`; suspend/reactivate with a reason; superusers grant/revoke the admin role. Rules: no self-suspension; only superusers act on other admins.
- **Survey moderation:** read-only content, suspend with a reason (respondents cannot open it and the owner cannot publish it), lift the suspension, archive.
- **Audit log:** filter by action and user. Entries are append-only in the application: there is no update or delete function for them.
- **Platform settings:** registration switch, announcement banner, maximum surveys per user (stored in `platform_settings`).

## 12. How survey quality checks operate

`quality.check_survey()` runs rules and returns `Issue(level, code, message, suggestion, question number)`:

| Rule | How it is detected | Level |
|---|---|---|
| Incomplete question | empty text, too few/many options, identical option text, impossible selection limits | error (blocks publish) |
| Leading wording | regex patterns: "don't you agree", "agree that", "how great/amazing…", "obviously", "why do you like" | warning |
| Double-barrelled | "and"/"or" in a choice or rating question that asks "how/do/rate…" | tip |
| Vague words | "regularly", "sometimes", "recently"…; "often/many/few" unless preceded by "how" | tip |
| Absolute words | "always", "never", "every"… in yes/no or single choice | tip |
| Double negative | a negation followed closely by another or by un-/dis- | warning |
| Too long | over 150 characters or 25 words | warning |
| Missing "Other" | 3+ options, no "Other/None/Prefer not to say" and "Other" not enabled | tip |
| Near-duplicate options | same text after removing punctuation and case | warning |
| Duplicate questions | Jaccard similarity of word sets ≥ 0.8 | warning |
| Inconsistent scales | star ratings with different ranges, or linear scales with different ranges | warning |

They are heuristics: the interface labels warnings and tips as recommendations, and only errors stop publishing.

## 13. AI-assisted features and their limits

- **Question suggestions** (`ai.suggest_questions`): with `AI_API_BASE` and `AI_MODEL` set, PollPulse calls any OpenAI-compatible Chat Completions API with Python's `urllib`, asks for JSON, and passes every suggestion through `normalize_question` (unknown types and invalid configs are dropped). Without AI, or if the call fails, it uses **template banks** chosen by keywords in the topic (event, course, product, service, workplace, general) and says which source was used.
- **Text analysis** (`text_analysis.py`, always rule-based): keywords counted per answer after removing stop words and simple plurals; repeated two-word phrases; topic tagging with a small built-in lexicon; sentiment by a positive/negative word list, flipping a word's sign when "not/never/…" appears within the two previous words. Limits: English only, small lexicons, no sarcasm or context. The page labels it an estimate.
- **Insight summary** (`insights.py`): deterministic sentences from the computed numbers (volume, busiest day, completion, timing, each question's main result, low participation, small-sample caution). With AI configured, "Rewrite with AI" sends only these sentences and instructs the model to add nothing. Without data it says there is no data; it never invents findings.
- **Honesty:** the AI client was tested with mocked replies only; no live service was available during development.

## 14. Security measures

Password hashing (PBKDF2), Django password validators, CSRF on all forms and on the API for session users, POST-only state changes (including logout), server-side role checks with 404/403, unguessable public tokens, server-side validation plus database validators, rate limits (login, registration, submissions, API, AI), template auto-escaping and no `innerHTML` with user text in JavaScript, a Content-Security-Policy that allows only same-origin scripts (all libraries are bundled locally, no inline scripts), X-Frame-Options DENY, HttpOnly/SameSite cookies (Secure with HTTPS), secrets only from `.env`, formula-safe CSV, HMAC-hashed respondent keys, friendly error pages without tracebacks in production, and an audit log. See README §14 and `docs/DEPLOYMENT.md`.

## 15. Testing strategy

- **113 tests** with Django's test runner. Each test gets a fresh in-memory MongoDB (`mongomock`) via `core/testing.MongoTestCase` and a temporary SQLite database, so tests are isolated and never touch real data. `POLLPULSE_TEST_REAL_MONGO=1` runs the same tests on a real MongoDB test database.
- **Unit tests** for pure logic (question types, statistics, chi-square against known values, text analysis, quality rules).
- **Service tests** for business rules (life cycle, roles, dates, limits, duplicates, drafts).
- **Integration tests** with Django's test client for pages, the respondent flow, exports, the API (status codes and CSRF) and the admin panel.
- **Failure tests:** MongoDB outage (mocked `ServerSelectionTimeoutError` → 503 page), the unique-index race, atomic limit reservation, AI service failure → template fallback.
- The JavaScript flows (builder save/publish, one-question-at-a-time, ranking, client validation) were checked in a headless browser during development.

## 16. Technical challenges and solutions

| Challenge | Solution |
|---|---|
| Different answer types in one store | Typed `value` per answer + one validation class per type |
| Editing questions after answers exist would corrupt results | Stable `qid`/`oid`; lock questions once `response_count > 0`; "Duplicate as new draft" |
| Two people taking the last response slot | `find_one_and_update` with `{"response_count": {"$lt": limit}}` + `$inc` (atomic) |
| Double submissions at the same instant | Unique compound index; `DuplicateKeyError` → friendly message; slot released |
| Measuring completion honestly | Record an attempt when the page opens; define and show completion/abandonment; show "—" if no data |
| Django auth needs SQL, survey data suits documents | SQLite for auth only; `owner_id` reference; documented non-atomic cross-database operations |
| Avoiding XSS with a JavaScript builder | DOM built with `textContent`/`value`; CSP forbids inline and third-party scripts |
| Working offline in a viva | Bootstrap, icons, Chart.js and fonts bundled in `static/vendor/` |
| Ranking without JavaScript | Rank `<select>` per option as fallback; JS adds move-up/down buttons that keep the selects in sync |

## 17. Limitations and future enhancements

**Limitations:** session duplicate protection can be bypassed; no transactions on standalone MongoDB; questions lock after responses; rate limits are per process with the local-memory cache; English-only lexicon-based text analysis; chi-square unreliable for small samples (warned); analytics read responses into Python (fine for thousands, not millions); AI integration untested against a live service; not run against a real MongoDB in the development environment (tests used mongomock).

**Future:** conditional/skip logic, question versioning, invitation tokens, scheduled report emails, multilingual analysis, pre-aggregated statistics with aggregation pipelines, server-side PDF, replica-set transactions.

---

## Interview questions and answers

1. **What is PollPulse?** A survey platform where creators build surveys from twelve question types, respondents answer through a public link, and creators get explainable analytics, with an admin panel for the platform.
2. **Why Django?** Built-in authentication, password hashing, sessions, CSRF, forms, auto-escaping templates and an ORM for users; DRF adds serializers, permissions and throttling. I could focus on survey logic instead of security plumbing.
3. **Why MongoDB?** Surveys and responses are nested and vary by question type. A survey with embedded questions, or a response with typed answers, is one document, read or written in one operation.
4. **Why are users in SQLite?** Django's authentication runs on its ORM, which needs a relational database without third-party adapters. SQLite holds only users and sessions; surveys store the owner's user id.
5. **Why are responses not embedded inside the survey?** They grow without limit (16 MB document cap), every survey read would load all answers, and many respondents would write to the same document.
6. **Why are answers embedded inside the response?** A response is always written and read as a whole, and a single-document insert is atomic, so a response can never be half saved.
7. **What is a `qid`/`oid` and why not use positions?** Random stable ids for questions and options. If questions are reordered, answers still point to the right question.
8. **How do the twelve question types differ in code?** Each is a subclass of `QuestionType` with its own config validation, form parsing and answer validation; the registry picks the class by type key (polymorphism).
9. **How is a ranking validated?** The answer must contain every option id exactly once, or the ranks 1..n must each be used once; otherwise it is rejected.
10. **What happens if a required question is skipped?** `clean_answer` raises a validation error for that question; nothing is saved and the form shows the message next to the question.
11. **How do you prevent duplicate responses?** A hashed respondent key per survey, checked before saving and enforced by a unique index on (survey_id, dedupe_key), so even simultaneous requests cannot both succeed.
12. **Is that fraud-proof?** No. Browser-based protection can be bypassed by clearing cookies; "one response per account" with sign-in is stronger.
13. **How is the response limit enforced?** An atomic `find_one_and_update` that increments `response_count` only while it is below the limit. If the insert then fails, the slot is released.
14. **What happens before the start date or after the end date?** `availability()` returns "not_started" or "ended"; the public page explains it and the API returns 403 with that reason.
15. **Authentication vs authorization?** Authentication proves who you are (login). Authorization decides what you may do: survey roles in `permissions.py`, the admin role in `core/roles.py`.
16. **Why return 404 instead of 403 for other people's surveys?** So outsiders cannot even find out that a survey id exists (protection against insecure direct object references).
17. **What can an editor do that a viewer cannot?** Change questions and settings, publish/close and duplicate. Viewers can view results and export. Only owners archive, delete and manage collaborators.
18. **How is the completion rate calculated?** Completed attempts ÷ attempts started (recorded when the survey page is opened) × 100. With no attempts it shows "—".
19. **How is average completion time calculated?** Submission time minus start time for responses with a recorded start, excluding values over 24 hours; we show the median and the mean.
20. **Why does multiple-choice add up to more than 100%?** Percentages are of respondents who answered, and each can choose several options.
21. **What is the difference between "responses" and "percentage of respondents"?** Responses are submitted documents; an option's percentage divides its count by the respondents who answered that question, not by all responses.
22. **Explain the chi-square test in your crosstab.** It compares observed counts with the counts expected if the two answers were independent. A p-value below 0.05 suggests an association, not a cause; with expected counts under 5 the page warns the test is unreliable.
23. **What is the Borda score?** Points by position: first place gets n points, second n−1 and so on, scaled to 0–100.
24. **How does sentiment analysis work?** It counts positive and negative words from a small English list and flips a word's sign after "not" or "never". It is an estimate and is labelled as such.
25. **Does the app require an AI key?** No. Without one it uses topic-based templates and rule-based summaries, and the UI says so.
26. **How do you stop the AI from inventing findings?** It receives only the rule-based sentences built from real numbers, with an instruction not to add facts, and the original sentences stay visible.
27. **What does the quality checker do?** It runs rules for incomplete, leading, double-barrelled, vague, absolute, double-negative and long questions, missing "Other", duplicate questions/options and inconsistent scales. Only completeness errors block publishing.
28. **How is CSV formula injection prevented?** Text cells starting with =, +, -, @, tab or carriage return get an apostrophe prefix so spreadsheet programs treat them as text.
29. **What is CSRF and how is it handled?** A forged request from another site using your cookies. Every POST form has a token, state-changing actions are POST-only, and DRF checks the token for session users.
30. **How do you prevent XSS?** Templates auto-escape; the builder sets `textContent`/`value`, never `innerHTML` with user text; the CSP allows scripts only from our own static files.
31. **How is rate limiting implemented?** A fixed-window counter in Django's cache (`core/ratelimit.py`) for login, registration and submissions, plus DRF throttle classes for the API.
32. **How does an administrator suspend a user, and what happens?** POST with a reason sets `is_active=False`, writes an audit entry, and Django then refuses that user's login and existing sessions.
33. **Can a normal user open the admin panel by typing the URL?** No: `@admin_required` returns 403 for non-admins and redirects anonymous users to login. A test checks every admin URL.
34. **How is the initial administrator created?** With `python manage.py createsuperuser`. No credentials exist in the code.
35. **What is the audit log?** A MongoDB collection of who did what to which object, when and from which IP. The app has no function to edit or delete entries.
36. **What happens if MongoDB is down?** PyMongo times out after `MONGO_TIMEOUT_MS`; the middleware shows a 503 page with fixing steps (JSON for the API), and the landing page still works.
37. **How did you test without a MongoDB server?** With mongomock, an in-memory imitation, injected through `mongo.use_database()`. The same tests can run against real MongoDB with an environment variable.
38. **What is the save-and-resume feature?** Unvalidated draft answers are stored on the attempt document with a random resume token; the link restores the form on any device.
39. **What is a serializer in your API?** A DRF class that checks the shape of incoming JSON and controls which fields leave the server — the public survey serializer omits owner, counts and private settings.
40. **What are the main limitations?** Browser-based duplicate protection, no transactions on standalone MongoDB, questions locked after responses, per-process rate limits, English-only text analysis, and AI tested only with mocks.
41. **How would you scale analytics?** Move per-question counting into MongoDB aggregation pipelines or maintain pre-aggregated counters, use a shared cache, and run a replica set.
42. **Where is threading used?** Only in `demos/threading_demo.py`, a separate Unit 3 demonstration; the web app relies on the server handling requests independently and on MongoDB's atomic operations.

---

## Demonstration plan

Before the review: start MongoDB, then run `python manage.py seed_demo` and `python manage.py runserver`, and have an admin account ready (`createsuperuser`). Use one normal window and one private window.

| # | Action | What to point out |
|---|---|---|
| 1 | Landing page; tap the sample poll | Differentiators table; the poll is labelled as browser-only sample data |
| 2 | Sign in as `demo` → Dashboard | Metrics strip and 30-day trend from MongoDB; quick actions |
| 3 | Surveys → search "canteen", filter Closed, sort by responses | Server-side search with escaped regex, pagination |
| 4 | Open **Library Opening Hours** → Questions | Quality panel: leading, absolute, double-barrelled, inconsistent scales |
| 5 | Add a ranking question, leave one option, click Publish | Blocking error shown; add an option, publish succeeds |
| 6 | Suggest questions → topic "college canteen" | Template source is stated; Add inserts into the builder |
| 7 | Settings: set max responses = 1, end date tomorrow | Combination rules (try "identified" + public → error) |
| 8 | Copy the link; answer in the private window (one question at a time) | Progress bar, required check, star rating, ranking buttons, email validation |
| 9 | Open the link again | "You have already responded" (duplicate protection) |
| 10 | Try a third browser profile | "Response limit reached" |
| 11 | Python Workshop Feedback → Analytics | Completion rate, median time, per-type charts, Borda table, text tone with method note |
| 12 | Crosstab: Q2 × Q6 | Row %, chi-square, reliability warning |
| 13 | Reports → download both CSVs, open in Excel; open the printable report | UTF-8, formula-safe; Save as PDF |
| 14 | Compare: workshop Q1 vs canteen Q1 (both 1–5 ratings) | Side-by-side averages and the difference |
| 15 | Sign in as `reviewer` (viewer on Canteen) | Can view and export; Settings returns 403; other surveys return 404 |
| 16 | Admin: overview → users → suspend `reviewer` with a reason | Login now refused; audit entry recorded |
| 17 | Moderation: suspend a survey | Public link shows "suspended"; owner cannot publish |
| 18 | Platform settings: close registration | Sign-up page explains it is closed |
| 19 | Stop the MongoDB service and reload a page | Friendly 503 page; start it again |
| 20 | `python manage.py test` | 113 tests OK |
