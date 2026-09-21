"""
Follow-up timing. The AI only says WHAT follow-up it wants (kind + reason); this file decides WHEN.
All values are the defaults from Farid's prompt; change them here, not in the prompt.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from mysite.ai_agent import config

DELAYS = {
    'escalation_check': timedelta(minutes=30),
    'tenant_nudge': timedelta(hours=3),
    'second_tenant_nudge': timedelta(hours=24),
    ('staff_reminder', 'routine'): timedelta(hours=24),
    ('staff_reminder', 'urgent'): timedelta(hours=1),
    ('staff_reminder', 'emergency'): timedelta(minutes=30),
}

# Routine tenant nudges only inside this window (tenant local time); otherwise next day at TENANT_RESUME
TENANT_WINDOW = (time(9, 0), time(20, 0))
TENANT_RESUME = time(10, 0)

# Non-urgent reminders to staff only inside this window (team time); otherwise next STAFF_WINDOW start
STAFF_WINDOW = (time(10, 0), time(18, 0))

TENANT_KINDS = ('tenant_nudge', 'second_tenant_nudge')

# Safety cap: an issue can never produce more follow-ups than this (stops endless reminder loops)
MAX_FOLLOWUPS_PER_ISSUE = 8


def _tz():
    try:
        return ZoneInfo(config.TEAM_TIMEZONE)
    except Exception:
        return ZoneInfo('UTC')


def _into_window(moment, window, resume_at):
    """moment: aware datetime in local tz. Returns the same moment, or the next allowed start."""
    start, end = window
    if start <= moment.time() < end:
        return moment
    day = moment.date() if moment.time() < start else moment.date() + timedelta(days=1)
    return datetime.combine(day, resume_at, tzinfo=moment.tzinfo)


def due_at(kind, priority='routine', now=None):
    """Returns (aware UTC-safe datetime, human note explaining the choice)."""
    now = now or timezone.now()
    delay = DELAYS.get((kind, priority)) or DELAYS.get(kind) or DELAYS[('staff_reminder', 'routine')]
    local = (now + delay).astimezone(_tz())

    if kind in TENANT_KINDS:
        moved = _into_window(local, TENANT_WINDOW, TENANT_RESUME)
        note = f"{kind}: +{delay}" + (" moved to tenant hours" if moved != local else "")
        return moved, note

    if priority in ('urgent', 'emergency'):
        return local, f"{kind} ({priority}): +{delay}, any hour"

    moved = _into_window(local, STAFF_WINDOW, STAFF_WINDOW[0])
    note = f"{kind}: +{delay}" + (" moved to staff hours" if moved != local else "")
    return moved, note
