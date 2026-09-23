"""
One Telegram chat for everything the AI agent has to tell people: staff alerts, test-mode
alerts, failed runs, failed SMS deliveries, worker crashes.

Chat: env AI_AGENT_ALERT_CHAT_ID. Until it is set, everything goes to the existing error chat
(TELEGRAM_ERROR_CHAT_ID), so nothing is ever lost.
"""
import os

import requests

from mysite.unified_logger import log_error, logger


def ai_chat_id():
    configured = (os.environ.get('AI_AGENT_ALERT_CHAT_ID') or '').split(',')[0].strip()
    return configured or (os.environ.get('TELEGRAM_ERROR_CHAT_ID') or '').strip()


def notify_ai_chat(text):
    """Returns (ok, note)."""
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = ai_chat_id()
    if not token or not chat_id:
        return False, "no Telegram chat configured (TELEGRAM_TOKEN + AI_AGENT_ALERT_CHAT_ID)"
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={'chat_id': chat_id, 'text': text[:3900]},
            timeout=10,
        )
        response.raise_for_status()
        return True, f"sent to Telegram chat {chat_id}"
    except Exception as e:
        logger.error(f"AI agent Telegram notification failed: {e}")
        return False, f"Telegram send failed: {e}"


def report_error(error, context, info=None, source='task'):
    """ErrorLog row (no Telegram from the generic logger) + one message in the AI chat."""
    log_error(error, context, severity='high', source=source, additional_info=info, send_telegram=False)
    lines = [f"🚨 AI agent: {context}", str(error)[:500]]
    for key, value in (info or {}).items():
        if value:
            lines.append(f"{key}: {str(value)[:1500]}")
    return notify_ai_chat("\n".join(lines))


def activity_enabled():
    """AI_AGENT_TELEGRAM_ACTIVITY: all (default for the test period) | off"""
    return (os.environ.get('AI_AGENT_TELEGRAM_ACTIVITY') or 'all').strip().lower() != 'off'


def _local_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from mysite.ai_agent import config
    return f"{datetime.now(ZoneInfo(config.TEAM_TIMEZONE)).strftime('%b %d %H:%M')} {config.TIMEZONE_LABEL}"


def notify_knowledge_review(entry, decision, reviewer):
    if not activity_enabled():
        return
    notify_ai_chat(
        f"📚 Knowledge {decision} by {reviewer} · {_local_now()}\n"
        f"[{entry.scope_label}] {entry.key} = {entry.value[:300]}"
    )
