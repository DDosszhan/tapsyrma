import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-production-secret")
os.environ.setdefault("POSTGRES_PASSWORD", "test-only")
os.environ.setdefault("STUDENT_INITIAL_PASSWORD", "test-initial-password")
os.environ["DJANGO_DEBUG"] = "1"

from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
