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

# Simple alerts (v5, simple_telegram_alerts.md 1.3): at most 2 reminders per case - the first within 2 hours (urgent
# and emergency: 30 minutes, user decision 2026-10-06), the second the next day. Deadline reminders are separate and
# do not count.
DELAYS_V5 = {
    'escalation_check': timedelta(minutes=30),
    'tenant_nudge': timedelta(hours=2),
    'second_tenant_nudge': timedelta(hours=24),
    ('staff_reminder', 'routine'): timedelta(hours=2),
    ('staff_reminder', 'urgent'): timedelta(minutes=30),
    ('staff_reminder', 'emergency'): timedelta(minutes=30),
}
MAX_REMINDERS_PER_CASE_V5 = 2

# Routine tenant nudges only inside this window (tenant local time); otherwise next day at TENANT_RESUME
TENANT_WINDOW = (time(9, 0), time(20, 0))
TENANT_RESUME = time(10, 0)

# Non-urgent reminders to staff only inside office hours (team time); otherwise next STAFF_WINDOW start
STAFF_WINDOW = (time(config.OFFICE_START_HOUR, 0), time(config.OFFICE_END_HOUR, 0))

TENANT_KINDS = ('tenant_nudge', 'second_tenant_nudge')

# Safety cap: an issue can never produce more follow-ups than this (stops endless reminder loops).
# Deadline reminders are set by the backend, not the AI, and do not count.
MAX_FOLLOWUPS_PER_ISSUE = 8
DEADLINE_KIND = 'deadline_reminder'

# Reminders before a tenant deadline (user decision 2026-09-30): the owner 24 h and 2 h before; at 2 h Kevin too when
# staff have not acted yet
DEADLINE_REMINDERS = (
    (timedelta(hours=24), "24h", "Tenant deadline in about 24 hours: remind the owner"),
    (timedelta(hours=2), "2h", "Tenant deadline in about 2 hours: remind the owner, and ALSO alert Kevin if staff have "
                               "not acted on it yet"),
)


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


def _office_moment(moment):
    """The moment itself when it is inside office hours on a working day, else the start of the next working day
    (weekends and US federal holidays are skipped)."""
    start, end = STAFF_WINDOW
    for _ in range(14):
        working = moment.weekday() < 5 and not config.holiday_name(moment.date())
        if working and start <= moment.time() < end:
            return moment
        day = moment.date() if working and moment.time() < start else moment.date() + timedelta(days=1)
        moment = datetime.combine(day, start, tzinfo=moment.tzinfo)
    return moment


def due_at(kind, priority='routine', now=None):
    """Returns (aware UTC-safe datetime, human note explaining the choice)."""
    now = now or timezone.now()
    delays = DELAYS_V5 if config.alert_style() == 'v5' else DELAYS
    delay = delays.get((kind, priority)) or delays.get(kind) or delays[('staff_reminder', 'routine')]
    local = (now + delay).astimezone(_tz())

    if kind in TENANT_KINDS:
        moved = _into_window(local, TENANT_WINDOW, TENANT_RESUME)
        note = f"{kind}: +{delay}" + (" moved to tenant hours" if moved != local else "")
        return moved, note

    if priority in ('urgent', 'emergency'):
        return local, f"{kind} ({priority}): +{delay}, any hour"

    moved = _office_moment(local) if config.alert_style() == 'v5' else _into_window(local, STAFF_WINDOW, STAFF_WINDOW[0])
    note = f"{kind}: +{delay}" + (" moved to staff hours" if moved != local else "")
    return moved, note


def deadline_reminders(deadline, now=None):
    """[(due_at, label, reason)] for the reminders before a tenant deadline that are still in the future."""
    now = now or timezone.now()
    return [(deadline - before, label, reason) for before, label, reason in DEADLINE_REMINDERS if deadline - before > now]
