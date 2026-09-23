import os
from pathlib import Path

from django.conf import settings

BACKEND_OPENROUTER = 'openrouter'
BACKEND_CLAUDE_CLI = 'claude_cli'

# AIManagement.prompt_key values
AI_BACKEND_KEY = 'ai_backend'
AI_AGENT_MODEL_KEY = 'ai_agent_model'
AI_AGENT_SYSTEM_KEY = 'ai_agent_system'

DEFAULT_AGENT_MODEL = 'claude-sonnet-5'

PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_DIR / 'schema.json'
DEFAULT_SYSTEM_PROMPT_PATH = PACKAGE_DIR / 'default_system_prompt.md'
MCP_TOOLS_PATH = PACKAGE_DIR / 'mcp_tools.py'

RUNS_DIR = Path(settings.BASE_DIR) / 'logs' / 'ai_runs'
# claude runs from an empty directory so it has no project files to look at
WORK_DIR = RUNS_DIR / '_cwd'

MCP_SERVER_NAME = 'crm'
ALLOWED_MCP_TOOLS = (
    'mcp__crm__get_chat_history',
    'mcp__crm__search_chat_history',
)

ASSISTANT_NAME = os.environ.get('AI_AGENT_ASSISTANT_NAME', 'Virtual Assistant')
COMPANY_NAME = os.environ.get('AI_AGENT_COMPANY_NAME', 'the property management company')
# All properties are in Florida (West Palm Beach, Sarasota) = US Eastern Time, the same zone as the team.
# America/New_York is the official name of that zone; it follows daylight saving automatically.
TEAM_TIMEZONE = os.environ.get('AI_AGENT_TEAM_TIMEZONE', 'America/New_York')
PROPERTY_TIMEZONE = os.environ.get('AI_AGENT_PROPERTY_TIMEZONE', TEAM_TIMEZONE)
TIMEZONE_LABEL = os.environ.get('AI_AGENT_TIMEZONE_LABEL', 'ET, Florida')

# Tenant-facing SMS (AI answers, welcome/contract messages, reminders) only actually go out in this window
# of PROPERTY_TIMEZONE hours; outside it they are held and sent at the next window open (user request 2026-09-22).
NOTIFICATION_WINDOW_START_HOUR = int(os.environ.get('AI_AGENT_NOTIFY_WINDOW_START', 8))
NOTIFICATION_WINDOW_END_HOUR = int(os.environ.get('AI_AGENT_NOTIFY_WINDOW_END', 21))


def is_within_notification_window(now=None):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo(PROPERTY_TIMEZONE))
    return NOTIFICATION_WINDOW_START_HOUR <= now.hour < NOTIFICATION_WINDOW_END_HOUR


def next_notification_window_start(now=None):
    """The next moment `is_within_notification_window` becomes true at or after `now`."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo(PROPERTY_TIMEZONE))
    today_start = now.replace(hour=NOTIFICATION_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    return today_start if now < today_start else today_start + timedelta(days=1)


def site_url():
    """Base address of the CRM for links in Telegram / ClickUp messages."""
    return (os.environ.get('AI_AGENT_SITE_URL') or '').rstrip('/')


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def run_timeout_seconds():
    return int(_env_float('AI_AGENT_TIMEOUT_SECONDS', 180))


def max_budget_usd():
    return _env_float('AI_AGENT_MAX_BUDGET_USD', 0.50)


def debounce_seconds():
    """Wait this long after the last tenant message so a burst is answered once."""
    return int(_env_float('AI_AGENT_DEBOUNCE_SECONDS', 60))


def chat_ui_debounce_seconds():
    """Test messages typed by a manager in the CRM chat page are single messages: short wait."""
    return int(_env_float('AI_AGENT_CHAT_UI_DEBOUNCE_SECONDS', 5))


def claude_binary():
    return os.environ.get('AI_AGENT_CLAUDE_BIN', 'claude')


def _management_value(prompt_key):
    from mysite.models import AIManagement
    entry = AIManagement.objects.filter(prompt_key=prompt_key).first()
    if entry and entry.content and entry.content.strip():
        return entry.content.strip()
    return None


def get_ai_backend():
    """AIManagement row 'ai_backend' wins, then env AI_BACKEND, default openrouter."""
    try:
        value = _management_value(AI_BACKEND_KEY)
    except Exception:
        value = None
    value = (value or os.environ.get('AI_BACKEND') or BACKEND_OPENROUTER).strip().lower()
    return BACKEND_CLAUDE_CLI if value == BACKEND_CLAUDE_CLI else BACKEND_OPENROUTER


def is_agent_backend_enabled():
    return get_ai_backend() == BACKEND_CLAUDE_CLI


def get_agent_model():
    try:
        value = _management_value(AI_AGENT_MODEL_KEY)
    except Exception:
        value = None
    return value or os.environ.get('AI_AGENT_MODEL') or DEFAULT_AGENT_MODEL
