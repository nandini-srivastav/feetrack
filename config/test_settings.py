"""Settings for the test run: a throwaway key and in-memory-friendly options."""
import os

os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("DEMO_MODE", "1")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")

from config.settings import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405
