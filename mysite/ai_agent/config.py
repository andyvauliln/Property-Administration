import os
from pathlib import Path

from django.conf import settings

# The only AI backend (the OpenRouter backend and its switch were removed 2026-09-28)
BACKEND_CLAUDE_CLI = 'claude_cli'

# AIManagement.prompt_key values
AI_AGENT_MODEL_KEY = 'ai_agent_model'
AI_AGENT_SYSTEM_KEY = 'ai_agent_system'
AI_CLICKUP_WRITES_KEY = 'ai_clickup_writes'
# "Add KB rule" from the chat page: bullets appended to the agent's system prompt
AI_AGENT_KB_RULES_KEY = 'ai_agent_kb_rules'

DEFAULT_AGENT_MODEL = 'claude-sonnet-5'
# Reads staff replies in Telegram and answers their questions (user request 2026-09-30: Opus 5.5, medium effort)
DEFAULT_REVIEW_MODEL = 'claude-opus-5-5'
DEFAULT_REVIEW_EFFORT = 'medium'

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
    'mcp__crm__get_contract',
)

ASSISTANT_NAME = os.environ.get('AI_AGENT_ASSISTANT_NAME', 'Virtual Assistant')
COMPANY_NAME = os.environ.get('AI_AGENT_COMPANY_NAME', 'the property management company')
# All properties are in Florida (West Palm Beach, Sarasota) = US Eastern Time, the same zone as the team.
# America/New_York is the official name of that zone; it follows daylight saving automatically.
TEAM_TIMEZONE = os.environ.get('AI_AGENT_TEAM_TIMEZONE', 'America/New_York')
PROPERTY_TIMEZONE = os.environ.get('AI_AGENT_PROPERTY_TIMEZONE', TEAM_TIMEZONE)

# StaffMember.ai_name -> other ai_names that are the same person / also act in that role (user, 2026-09-24:
# "Kevin is also Farid"). Whoever is Kevin (ClickUp "Imie Malaay") keeps the role; Farid is Kevin too.
STAFF_ALSO = {'Kevin': ('Farid',)}
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


# Office hours (client spec v4, user decision 2026-09-30): Monday-Friday 09:00-18:00 in TEAM_TIMEZONE. US federal
# holidays are outside office hours, like a weekend.
OFFICE_START_HOUR = int(os.environ.get('AI_AGENT_OFFICE_START', 9))
OFFICE_END_HOUR = int(os.environ.get('AI_AGENT_OFFICE_END', 18))


def _nth_weekday(year, month, weekday, n):
    """n-th weekday (0 = Monday) of a month; n = -1 is the last one."""
    from datetime import date, timedelta
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_federal_holidays(year):
    """{date: name} of the US federal holidays of a year, fixed-date ones moved to Friday / Monday when they fall on a
    weekend (the observed day, as federal offices do)."""
    from datetime import date, timedelta

    def observed(day):
        return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day

    days = {
        observed(date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): 'Martin Luther King Jr. Day',
        _nth_weekday(year, 2, 0, 3): "Washington's Birthday (Presidents Day)",
        _nth_weekday(year, 5, 0, -1): 'Memorial Day',
        observed(date(year, 6, 19)): 'Juneteenth',
        observed(date(year, 7, 4)): 'Independence Day',
        _nth_weekday(year, 9, 0, 1): 'Labor Day',
        _nth_weekday(year, 10, 0, 2): 'Columbus Day',
        observed(date(year, 11, 11)): 'Veterans Day',
        _nth_weekday(year, 11, 3, 4): 'Thanksgiving Day',
        observed(date(year, 12, 25)): 'Christmas Day',
    }
    # New Year's Day of the next year observed on Dec 31 of this one
    if date(year + 1, 1, 1).weekday() == 5:
        days[date(year, 12, 31)] = "New Year's Day (observed)"
    return days


def holiday_name(day):
    """Name of the US federal holiday on this date, or None."""
    return us_federal_holidays(day.year).get(day)


def team_now(now=None):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return (now or datetime.now(ZoneInfo(TEAM_TIMEZONE))).astimezone(ZoneInfo(TEAM_TIMEZONE))


def is_office_hours(now=None):
    """True Monday-Friday OFFICE_START_HOUR-OFFICE_END_HOUR team time, except US federal holidays."""
    local = team_now(now)
    return (local.weekday() < 5 and OFFICE_START_HOUR <= local.hour < OFFICE_END_HOUR
            and not holiday_name(local.date()))


def office_hours_label():
    return f"Monday-Friday {OFFICE_START_HOUR:02d}:00-{OFFICE_END_HOUR:02d}:00 {TIMEZONE_LABEL} (US federal holidays excluded)"


def site_url():
    """Base address of the CRM for links in Telegram / ClickUp messages."""
    return (os.environ.get('AI_AGENT_SITE_URL') or '').rstrip('/')


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def run_timeout_seconds():
    return int(_env_float('AI_AGENT_TIMEOUT_SECONDS', 360))


def max_budget_usd():
    return _env_float('AI_AGENT_MAX_BUDGET_USD', 0.50)


def debounce_seconds():
    """Wait this long after the last tenant message so a burst is answered once."""
    return int(_env_float('AI_AGENT_DEBOUNCE_SECONDS', 60))


def chat_ui_debounce_seconds():
    """Test messages typed by a manager in the CRM chat page are single messages: short wait."""
    return int(_env_float('AI_AGENT_CHAT_UI_DEBOUNCE_SECONDS', 5))


def review_hold_minutes():
    """Live AI answers wait this long for a staff correction in the Telegram AI group (0 = send at once)."""
    return _env_float('AI_AGENT_REVIEW_HOLD_MINUTES', 15)


def review_poll_seconds():
    """How often the worker reads staff replies and button presses from Telegram (it also reads right before
    releasing an answer). Short, so a pressed button reacts at once."""
    return _env_float('AI_AGENT_REVIEW_POLL_SECONDS', 3)


def explicit_approval():
    """
    Client spec v4 (user decision 2026-09-30): nothing an AI run proposes happens until someone presses a button (or
    replies "ok") in Telegram - no timer. AI_AGENT_APPROVAL=timer brings back the old 15-minute "silence = yes" review.
    AI_AGENT_REVIEW_HOLD_MINUTES=0 switches any review off (everything happens at once, as before the review existed).
    """
    if review_hold_minutes() <= 0:
        return False
    return (os.environ.get('AI_AGENT_APPROVAL') or 'explicit').strip().lower() != 'timer'


def alert_style():
    """
    Layout and buttons of the Telegram alerts: 'v5' = the simple alerts of simple_telegram_alerts.md (one block per
    thing, one button per block, reminders created at once), 'v4' = the long approval card (default until v5 is
    deployed). AI_AGENT_ALERT_STYLE=v4 + a worker restart is the rollback.
    """
    return 'v5' if (os.environ.get('AI_AGENT_ALERT_STYLE') or 'v4').strip().lower() == 'v5' else 'v4'


def oneshot_model():
    """Model for the chat-page helpers (rules, KB drafts, explanations) - oneshot.py."""
    return os.environ.get('AI_AGENT_ONESHOT_MODEL') or get_agent_model()


def oneshot_timeout_seconds():
    return int(_env_float('AI_AGENT_ONESHOT_TIMEOUT_SECONDS', 120))


def oneshot_max_budget_usd():
    return _env_float('AI_AGENT_ONESHOT_MAX_BUDGET_USD', 0.30)


def review_model():
    """Model that reads a staff reply to an AI answer (correction / lesson / stop / question)."""
    return os.environ.get('AI_AGENT_REVIEW_MODEL') or DEFAULT_REVIEW_MODEL


def review_effort():
    """claude --effort for the reply interpreter: low | medium | high | xhigh | max."""
    return os.environ.get('AI_AGENT_REVIEW_EFFORT') or DEFAULT_REVIEW_EFFORT


def claude_binary():
    return os.environ.get('AI_AGENT_CLAUDE_BIN', 'claude')


def _management_value(prompt_key):
    from mysite.models import AIManagement
    entry = AIManagement.objects.filter(prompt_key=prompt_key).first()
    if entry and entry.content and entry.content.strip():
        return entry.content.strip()
    return None


def get_ai_backend():
    return BACKEND_CLAUDE_CLI


def is_agent_backend_enabled():
    return True


def get_agent_model():
    try:
        value = _management_value(AI_AGENT_MODEL_KEY)
    except Exception:
        value = None
    return value or os.environ.get('AI_AGENT_MODEL') or DEFAULT_AGENT_MODEL


def clickup_writes_enabled():
    """
    Master switch for changing anything in ClickUp (create / close / delete / update / comment tasks, channel
    messages). Off: the agent still plans the ClickUp changes and the Telegram alerts describe them, but nothing
    is written to ClickUp. Reading tasks keeps working. Apartments with "test" in the name ignore the switch
    and always write (clickup.writes_enabled(apartment)). AIManagement row 'ai_clickup_writes' (on/off) wins,
    then env AI_AGENT_CLICKUP_WRITES, default on.
    """
    try:
        value = _management_value(AI_CLICKUP_WRITES_KEY)
    except Exception:
        value = None
    value = (value or os.environ.get('AI_AGENT_CLICKUP_WRITES') or 'on').strip().lower()
    return value not in ('off', '0', 'false', 'no', 'disabled')
