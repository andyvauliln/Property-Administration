"""
The case time. A case says "Tue 6 Oct 14:34 ET"; while it runs, this process believes that is the current time, so
office hours, after hours, holidays, reminder times and the CURRENT_TIME the AI is told work like in the example.

Two things to know:
- The case date is moved forward by whole weeks until it is in the real future (same weekday, same time of day, no
  holiday surprise). Reminders created by a test are then never due for the LIVE worker, which shares the database and
  fires every reminder whose time has come. The alerts and the checks use the moved date.
- Only this process is affected (the patches live in memory). The clock runs on from the case time in real time and
  can be moved forward (fast time: a reminder "in 2 h" fires at once, the clock jumps to its due time).
"""
import importlib
import pkgutil
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from mysite.ai_agent import config

_REAL_NOW = timezone.now
# Modules whose own datetime.now() must stay real (file names of run folders)
_KEEP_REAL = ('mysite.ai_agent.run_report', 'mysite.ai_agent.mcp_tools')


def real_now():
    return _REAL_NOW()


def week_shift(case_time, margin=timedelta(minutes=5)):
    """Days (a multiple of 7) to add so the case time is in the real future and holidays stay where they were."""
    def holidays(moment):
        return [bool(config.holiday_name((moment + timedelta(days=d)).date())) for d in (0, 1)]

    days = 0
    for _ in range(520):
        moved = case_time + timedelta(days=days)
        if moved > real_now() + margin and holidays(moved) == holidays(case_time):
            return days
        days += 7
    return days


class CaseClock:
    def __init__(self):
        self.offset = timedelta(0)
        self._undo = []

    def now(self):
        return _REAL_NOW() + self.offset

    def local(self):
        return self.now().astimezone(ZoneInfo(config.TEAM_TIMEZONE))

    def set(self, moment):
        self.offset = moment - _REAL_NOW()

    def forward_to(self, moment):
        """Fast time: jump to the moment when it is still ahead (the clock never goes back inside a case)."""
        if moment > self.now():
            self.set(moment)

    def _patch(self, owner, name, value):
        self._undo.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def install(self):
        from mysite import ai_agent
        from mysite.ai_agent import clickup, notify

        clock = self

        class CaseDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                moment = clock.now()
                return moment.astimezone(tz) if tz else moment.astimezone(ZoneInfo(config.TEAM_TIMEZONE)).replace(tzinfo=None)

        # Every agent module is loaded first, so one that is only imported inside a function is patched too
        for info in pkgutil.iter_modules(ai_agent.__path__):
            name = f"{ai_agent.__name__}.{info.name}"
            if not info.ispkg and name not in _KEEP_REAL:
                importlib.import_module(name)
        self._patch(timezone, 'now', self.now)
        for name, module in list(sys.modules.items()):
            if name.startswith('mysite.ai_agent.') and name not in _KEEP_REAL and '.sandbox_test' not in name \
                    and getattr(module, 'datetime', None) is datetime:
                self._patch(module, 'datetime', CaseDatetime)

        # Functions that import datetime locally
        def property_now():
            return self.now().astimezone(ZoneInfo(config.PROPERTY_TIMEZONE))

        team_now, in_window, next_window = config.team_now, config.is_within_notification_window, config.next_notification_window_start
        self._patch(config, 'team_now', lambda now=None: team_now(now or self.now()))
        self._patch(config, 'is_within_notification_window', lambda now=None: in_window(now or property_now()))
        self._patch(config, 'next_notification_window_start', lambda now=None: next_window(now or property_now()))
        self._patch(notify, '_local_now', lambda: f"{self.local():%b %d %H:%M} {config.TIMEZONE_LABEL}")
        self._patch(clickup, 'default_due_at', lambda priority: self.local() + timedelta(
            hours=clickup.DUE_IN_HOURS.get(priority, clickup.DUE_IN_HOURS['routine'])))

    def uninstall(self):
        for owner, name, value in reversed(self._undo):
            setattr(owner, name, value)
        self._undo = []
