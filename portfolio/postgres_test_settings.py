"""Explicit opt-in only: Django creates a test database on the supplied test server."""
import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .test_settings import *  # noqa: F403

database_url = os.environ.get('PORTFOLIO_TEST_DATABASE_URL')
if not database_url:
    raise ImproperlyConfigured('Set PORTFOLIO_TEST_DATABASE_URL to a disposable PostgreSQL test server.')
DATABASES = {'default': dj_database_url.parse(database_url)}
if DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
    raise ImproperlyConfigured('The concurrency suite requires PostgreSQL.')
