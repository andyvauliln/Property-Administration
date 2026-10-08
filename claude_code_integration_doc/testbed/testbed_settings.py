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

# Safety net: tests can never reach the real Telegram bot or ClickUp, whatever a test forgets to fake.
import os  # noqa: E402
os.environ['TELEGRAM_TOKEN'] = ''
# ... nor Twilio: no test may send an SMS or DIAL A PHONE. On 2026-10-06 the emergency scenario of e2e_answer_review.py
# phoned the owner three times for real (run after e2e_sandbox_runner.py, which leaves a staff phone in the test DB).
os.environ['TWILIO_ACCOUNT_SID'] = ''
os.environ['TWILIO_AUTH_TOKEN'] = ''
os.environ.pop('CLICKUP_API_TOKEN', None)
for _name in [n for n in os.environ if n.startswith('AI_AGENT_')]:   # production AI settings must not leak into tests
    os.environ.pop(_name)
os.environ['AI_AGENT_CLAUDE_BIN'] = '/nonexistent/claude-testbed'   # a Claude call a test forgot to fake fails, never runs
os.environ['AI_AGENT_SITE_URL'] = 'http://crm.test'
