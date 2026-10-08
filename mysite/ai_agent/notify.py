"""
One Telegram chat for everything the AI agent has to tell people: staff alerts, test-mode
alerts, failed runs, failed SMS deliveries, worker crashes.

Chat: env AI_AGENT_ALERT_CHAT_ID. Until it is set, everything goes to the existing error chat
(TELEGRAM_ERROR_CHAT_ID), so nothing is ever lost.
"""
import json
import os

import requests

from mysite.unified_logger import log_error, logger


def ai_chat_id():
    configured = (os.environ.get('AI_AGENT_ALERT_CHAT_ID') or '').split(',')[0].strip()
    return configured or (os.environ.get('TELEGRAM_ERROR_CHAT_ID') or '').strip()


def _post(token, chat_id, text, reply_to=None, reply_markup=None, silent=False, parse_mode=None):
    """Returns (ok, note, message_id). The note never contains the bot token. silent: posted without sound.
    parse_mode 'HTML': the text is Telegram HTML (the simple alerts) - never cut, a cut could break a tag."""
    data = {'chat_id': chat_id, 'text': text if parse_mode else text[:3900]}
    if parse_mode:
        data['parse_mode'] = parse_mode
    if silent:
        data['disable_notification'] = 'true'
    if reply_to:
        data['reply_to_message_id'] = reply_to
        data['allow_sending_without_reply'] = 'true'
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10)
        body = response.json() if response.content else {}
    except Exception as e:
        return False, f"Telegram not reachable: {type(e).__name__}", None
    if response.status_code >= 300 or not body.get('ok'):
        new_id = (body.get('parameters') or {}).get('migrate_to_chat_id')
        if new_id and str(new_id) != str(chat_id):
            # The group became a supergroup (happens e.g. when the bot is made admin): its id changed
            logger.error(f"Telegram chat {chat_id} is now {new_id} - update AI_AGENT_ALERT_CHAT_ID in .env")
            ok, note, message_id = _post(token, new_id, text, reply_to, reply_markup, silent, parse_mode)
            return ok, f"{note} (group was upgraded: set AI_AGENT_ALERT_CHAT_ID={new_id} in .env)", message_id
        return False, f"Telegram refused ({response.status_code}): {body.get('description') or 'no reason given'}", None
    return True, f"sent to Telegram chat {chat_id}", (body.get('result') or {}).get('message_id')


def send_ai_chat(text, reply_to=None, reply_markup=None, silent=False, parse_mode=None):
    """
    Returns (ok, note, telegram_message_id). When the AI group refuses the message (e.g. members may not
    send messages there), it goes to the error chat (TELEGRAM_ERROR_CHAT_ID) instead so it is not lost;
    ok stays False and no message id is returned, because nobody can reply to it in the AI group.
    """
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = ai_chat_id()
    if not token or not chat_id:
        return False, "no Telegram chat configured (TELEGRAM_TOKEN + AI_AGENT_ALERT_CHAT_ID)", None
    ok, note, message_id = _post(token, chat_id, text, reply_to, reply_markup, silent, parse_mode)
    if ok:
        return ok, note, message_id
    logger.error(f"AI agent Telegram notification failed: {note}")
    fallback = (os.environ.get('TELEGRAM_ERROR_CHAT_ID') or '').strip()
    if fallback and fallback != str(chat_id):
        warning = f"⚠ Could not post to the AI group ({note}). Message:\n\n"
        if parse_mode == 'HTML':
            import html
            warning = html.escape(warning, quote=False)
        copied, _, _ = _post(token, fallback, warning + text, parse_mode=parse_mode)
        if copied:
            note += f"; copy sent to the error chat {fallback}"
    return False, note, None


def _call(method, data):
    """One Telegram Bot API call on the AI chat. Returns (ok, description). Never raises, never logs the token."""
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        return False, 'no TELEGRAM_TOKEN'
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=10)
        body = response.json() if response.content else {}
    except Exception as e:
        return False, f"Telegram not reachable: {type(e).__name__}"
    return bool(body.get('ok')), body.get('description') or ''


def edit_reply_markup(message_id, reply_markup=None):
    """Replaces (None: removes) the buttons under one of the bot's messages in the AI chat."""
    return _call('editMessageReplyMarkup', {'chat_id': ai_chat_id(), 'message_id': message_id,
                                            'reply_markup': json.dumps(reply_markup or {'inline_keyboard': []})})


def edit_message_text(message_id, text, reply_markup=None, parse_mode=None):
    """Replaces the text (and the buttons) of one of the bot's messages in the AI chat (parse_mode 'HTML': not cut)."""
    data = {'chat_id': ai_chat_id(), 'message_id': message_id, 'text': text if parse_mode else text[:4096],
            'reply_markup': json.dumps(reply_markup or {'inline_keyboard': []})}
    if parse_mode:
        data['parse_mode'] = parse_mode
    return _call('editMessageText', data)


def answer_callback(callback_id, text='', alert=False):
    """The small notice Telegram shows to the person who pressed a button (must be answered, or it keeps spinning)."""
    return _call('answerCallbackQuery', {'callback_query_id': callback_id, 'text': text[:190],
                                         'show_alert': 'true' if alert else 'false'})


def notify_ai_chat(text):
    """Returns (ok, note)."""
    ok, note, _ = send_ai_chat(text)
    return ok, note


def report_error(error, context, info=None, source='task'):
    """ErrorLog row (no Telegram from the generic logger) + one message in the AI chat."""
    log_error(error, context, severity='high', source=source, additional_info=info, send_telegram=False)
    lines = [f"🚨 AI agent: {context}", str(error)[:500]]
    for key, value in (info or {}).items():
        if value:
            lines.append(f"{key}: {str(value)[:1500]}")
    return notify_ai_chat("\n".join(lines))


def _local_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from mysite.ai_agent import config
    return f"{datetime.now(ZoneInfo(config.TEAM_TIMEZONE)).strftime('%b %d %H:%M')} {config.TIMEZONE_LABEL}"
