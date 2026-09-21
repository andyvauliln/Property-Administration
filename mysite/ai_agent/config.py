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
TEAM_TIMEZONE = os.environ.get('AI_AGENT_TEAM_TIMEZONE', 'America/New_York')


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
