"""
python manage.py init_mongo

1. Connects to MongoDB (MONGO_URI) and pings it.
2. Creates the collections with JSON-Schema validators (see core/schemas.py).
3. Creates all indexes (see core/mongo.py).
4. Prints a short summary. Safe to run any number of times.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pymongo.errors import OperationFailure, PyMongoError

from core import mongo
from core.exceptions import DatabaseUnavailable
from core.schemas import VALIDATORS


class Command(BaseCommand):
    help = "Check the MongoDB connection, apply schema validators and create indexes."

    def add_arguments(self, parser):
        parser.add_argument("--skip-validators", action="store_true", help="Do not apply JSON-Schema validators.")

    def handle(self, *args, **options):
        self.stdout.write(f"Connecting to {settings.MONGO_URI} (database '{settings.MONGO_DB_NAME}') ...")
        try:
            db = mongo.get_db()
            db.command("ping")
        except (DatabaseUnavailable, PyMongoError) as exc:
            raise CommandError(f"Could not connect to MongoDB: {exc}\nOn Windows, check that the 'MongoDB Server' "
                               "service is running (Win+R -> services.msc).")
        self.stdout.write(self.style.SUCCESS("MongoDB connection OK."))

        if not options["skip_validators"]:
            action = settings.MONGO_SCHEMA_ACTION if settings.MONGO_SCHEMA_ACTION in ("warn", "error") else "warn"
            existing = set(db.list_collection_names())
            for name, schema in VALIDATORS.items():
                spec = {"validator": {"$jsonSchema": schema}, "validationLevel": "moderate", "validationAction": action}
                try:
                    if name in existing:
                        db.command({"collMod": name, **spec})
                    else:
                        db.create_collection(name, **spec)
                    self.stdout.write(f"  validator applied to {name} (action: {action})")
                except (OperationFailure, NotImplementedError, TypeError) as exc:
                    self.stdout.write(self.style.WARNING(f"  could not apply validator to {name}: {exc}"))

        for name in ("surveys", "responses", "response_sessions", "audit_logs", "platform_settings"):
            indexes = sorted(db[name].index_information())
            count = db[name].count_documents({})
            self.stdout.write(f"  {name:<18} {count:>6} documents   indexes: {', '.join(indexes)}")
        self.stdout.write(self.style.SUCCESS("MongoDB is ready."))
