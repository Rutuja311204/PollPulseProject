# Production deployment checklist

PollPulse runs with `python manage.py runserver` for development and demonstrations. Before putting it on a public server, work through this list.

## Configuration

- [ ] `DJANGO_DEBUG=False`. The app refuses to start without `DJANGO_SECRET_KEY` in this mode.
- [ ] A new, long, random `DJANGO_SECRET_KEY` that is never committed: `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
- [ ] `DJANGO_ALLOWED_HOSTS` lists only your domain(s); `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain`.
- [ ] HTTPS in front of the app (Nginx, IIS or a hosting platform), then `DJANGO_USE_HTTPS=True` (secure cookies), `DJANGO_SSL_REDIRECT=True` and `DJANGO_HSTS_SECONDS=31536000`. Add `DJANGO_HSTS_INCLUDE_SUBDOMAINS` / `DJANGO_HSTS_PRELOAD` only if every subdomain is HTTPS.
- [ ] If a reverse proxy sets `X-Forwarded-For`, set `TRUST_X_FORWARDED_FOR=True` so rate limits see real client IPs. Never set it without a proxy.
- [ ] Run `python manage.py check --deploy` and resolve every warning that applies.

## Data

- [ ] MongoDB with authentication enabled, a dedicated user with read/write rights on the `pollpulse` database only, and `MONGO_URI` containing those credentials.
- [ ] MongoDB not reachable from the internet (bind to localhost or a private network, or use Atlas IP allow-lists).
- [ ] `python manage.py init_mongo` run once; consider `MONGO_SCHEMA_ACTION=error` after verifying writes succeed.
- [ ] Back up MongoDB (`mongodump`) and the SQLite file (or move users to PostgreSQL by changing `DATABASES`) on a schedule, and test a restore.
- [ ] Run the test suite against the real database once: `POLLPULSE_TEST_REAL_MONGO=1 python manage.py test`.

## Serving

- [ ] Use a production WSGI server (e.g. `waitress-serve config.wsgi:application` on Windows, or Gunicorn on Linux), not `runserver`.
- [ ] `python manage.py collectstatic` and serve `staticfiles/` from the web server (or add WhiteNoise).
- [ ] Use a shared cache (Redis or Memcached) in `CACHES` when running more than one process, so login and submission rate limits are shared.
- [ ] Configure SMTP (`EMAIL_BACKEND` and `EMAIL_*`) so password-reset emails are delivered.

## Accounts and operations

- [ ] Create administrators with `python manage.py createsuperuser`; do not run `seed_demo` in production (its accounts use a known password).
- [ ] Review the audit log regularly (Admin panel → Audit log).
- [ ] Keep Django, DRF and PyMongo updated within their supported series (`pip list --outdated`).
- [ ] If an AI service is configured, keep its key only in the server's environment and review the provider's data-handling terms. PollPulse sends only survey topics and aggregated summary sentences.
