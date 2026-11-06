"""Offline test settings: never connect to DATABASE_URL or external mail/providers."""
from trading.settings import *  # noqa: F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
SECRET_KEY = 'portfolio-isolated-tests-only-not-a-production-key'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
# Legacy transactions.0002 creates a varchar(None), which SQLite cannot replay.
# Sync its current models; portfolio/users/stocks migrations still run normally.
MIGRATION_MODULES = {'transactions': None}
