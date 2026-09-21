"""Throwaway SQLite settings for the AI agent tests. Never touches the production database."""
import tempfile
from pathlib import Path

from mysite.settings import *  # noqa

TESTBED_DIR = Path(tempfile.gettempdir()) / 'ai_agent_testbed'
TESTBED_DIR.mkdir(exist_ok=True)
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(TESTBED_DIR / 'test.sqlite3')}}
# Build tables straight from models.py (the historical data migrations need Postgres data)
MIGRATION_MODULES = {'mysite': None}
ALLOWED_HOSTS = ['*']
