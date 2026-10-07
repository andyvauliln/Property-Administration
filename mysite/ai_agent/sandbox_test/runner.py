"""
Runs test cases one after another in the sandbox (simple_telegram_alerts.md 11.2):
clean start -> set-up -> trigger + the real agent, exactly like the worker -> alert with test header and 🧪 TEST NOTES
-> check -> verdict under the alert + report file -> wait for "next" -> next case. At the end a summary.
"""
import json
import os
import re
import select
import sys
import time
import traceback
from datetime import timedelta
from zoneinfo import ZoneInfo

import yaml
from django.core.management import call_command, get_commands

from mysite.ai_agent import config
from mysite.ai_agent.sandbox_test import catalog, check, story
from mysite.ai_agent.sandbox_test.clock import CaseClock, real_now, week_shift
from mysite.ai_agent.sandbox_test.world import (CONTROL_ROW, IDLE_ROW, REPLAY_ROW, SANDBOX_SID, World, buttons_of, is_on,
                                                parse_command, press_guide)

REPORT_COMMAND = 'ai_agent_daily_report'
PROMPT_LOOK = 'reply to this message'


def _local(moment):
    return moment.astimezone(ZoneInfo(config.TEAM_TIMEZONE))


class Runner:
    def __init__(self, auto=False, real_time=False, offline=False, use_claude=True, out=print, style='v5'):
        self.auto, self.real_time, self.use_claude, self.out = auto, real_time, use_claude, out
        self.style, self._old_style = style, os.environ.get('AI_AGENT_ALERT_STYLE')
        self.clock = CaseClock()
        self.world = World(self.clock, offline=offline, log=out)
        self.run_name = f"{_local(real_now()):%Y-%m-%d_%H%M}"
        self.run_dir = catalog.RESULTS_DIR / self.run_name
        self.results, self.cost, self.started = [], 0.0, time.monotonic()
        self.step = 0
        self.case_alerts, self.pending_change, self.story_shift = [], None, None
        self.case_first_step = self.chapter_first_step = 1
        self.position, self.main_token = 0, None   # the next case of the list ("continue"); the team bot's token (cleaning)

    # -- start / end ------------------------------------------------------------------------------------------------
    def start(self, clean_chat=False):
        self.main_token = os.environ.get('TELEGRAM_TOKEN')
        where = self.world.configure_telegram()
        if clean_chat:
            self.clean_chat()
        os.environ['AI_AGENT_ALERT_STYLE'] = self.style   # this process only
        self.clock.install()
        self.world.install()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.out(f"Sandbox chat {SANDBOX_SID} on {self.world.apartment.name}. {where}")
        self.out(f"Alerts under test: {self.style}. Reports: {self.run_dir}")
        if not self.auto and not self.world.offline:
            self.world.start_chat_watch()   # a person may write in the sandbox chat on the site: taken before the live worker
        if not self.auto and not self.world.own_bot and not (sys.stdin and sys.stdin.isatty()):
            self.out("No terminal and no sandbox bot to take commands from: the cases run one after another, like --auto "
                     "(without its presses).")

    def clean_chat(self):
        """Everything earlier test runs left in the Telegram test group goes: the bot's messages and what people wrote."""
        deleted = self.world.clean_chat(self.main_token)
        kept = getattr(self.world, 'kept_theirs', 0)
        self.out(f"Test chat cleaned: {deleted} message(s) deleted"
                 + (f"; {kept} message(s) written by people could NOT be deleted - make the test bot an admin of the group "
                    f"with the \"Delete messages\" right" if kept else ""))
        return deleted, kept

    def finish(self):
        try:
            self.world.stop_chat_watch()
            self.world.leave_nothing_behind()
        finally:
            self.world.uninstall()
            self.clock.uninstall()
            if self._old_style is None:
                os.environ.pop('AI_AGENT_ALERT_STYLE', None)
            else:
                os.environ['AI_AGENT_ALERT_STYLE'] = self._old_style

    def post(self, text, reply_to=None, markup=None, shown=True):
        """A message of the runner itself (failed checks, summary, notes): never part of a case's result. Posted
        under an alert, it belongs to that alert's run: a reply to it is a reply to the alert (11.5)."""
        from mysite.ai_agent import answer_review, notify
        from mysite.models import AIRun
        if shown:
            self.out("\n" + text)
        self.world.tap.own = True
        try:
            message_id = notify.send_ai_chat(text, reply_to=reply_to, reply_markup=markup)[2]
        finally:
            self.world.tap.own = False
        run = AIRun.objects.filter(telegram_message_id=reply_to).order_by('-id').first() if reply_to and message_id else None
        if run:
            answer_review.remember_bot_message(run.id, message_id)
        return message_id

    # -- steps -----------------------------------------------------------------------------------------------------------
    def _process(self, events):
        """What the worker does with a claimed batch (run_ai_agent.py). Returns a note when no run came out of it."""
        from mysite.ai_agent import service
        from mysite.ai_agent.notify import report_error
        from mysite.models import AIEvent
        ids = [e.id for e in events]
        try:
            run = service.process_events(events)
        except Exception as e:
            AIEvent.objects.filter(id__in=ids).update(status=AIEvent.STATUS_FAILED, error=str(e)[:2000], finished_at=self.clock.now())
            report_error(e, "sandbox test: the worker crashed on a batch", {'event_ids': ids})
            self.out(traceback.format_exc())
            return f"the worker crashed: {type(e).__name__}: {str(e)[:200]}"
        if run is None:
            error = AIEvent.objects.filter(id__in=ids).exclude(error__isnull=True).values_list('error', flat=True).first()
            return f"no AI run: {error or 'the events were closed without one'}"
        self.cost += float(run.cost_usd or 0)
        return f"the AI run failed: {run.error[:300]}" if run.error and not run.answer and not run.actions else None

    def _sleep_until(self, moment):
        while self.clock.now() < moment:
            left = (moment - self.clock.now()).total_seconds()
            self.out(f"  --real-time: waiting {left / 60:.0f} min until {_local(moment):%a %H:%M} (case time)")
            time.sleep(min(left, 60))

    def _move_to(self, moment):
        if self.real_time:
            self._sleep_until(moment)
        else:
            self.clock.forward_to(moment)

    def _last_with_buttons(self):
        tap = self.world.tap
        return next((tap.messages[i] for i in reversed(tap.order) if not tap.messages[i]['own']
                     and tap.messages[i]['step'] >= self.case_first_step and buttons_of(tap.messages[i]['markup'])), None)

    def _alert_ids(self, on=None):
        """The alerts of the story so far (newest last); on: first = the alert BEFORE the newest one (the older of the
        two last alerts, e.g. the one that became outdated when the tenant wrote again) - taken from the runs in the
        database, which is what the bot itself goes by."""
        if on == 'first':
            from mysite.models import AIRun
            ids = list(AIRun.objects.filter(conversation_sid=SANDBOX_SID, telegram_message_id__isnull=False)
                       .order_by('-id').values_list('telegram_message_id', flat=True)[:2])
            return ids[1:2] if len(ids) == 2 else ids[:1]
        return [a['id'] for a in self.case_alerts]

    def _reply_target(self, on):
        """Telegram message a simulated reply answers: the bot's "reply to THIS message" request when the last
        message is one, else the newest alert (on: first - the one before it)."""
        tap = self.world.tap
        case_messages = [tap.messages[i] for i in tap.order if not tap.messages[i]['own'] and tap.messages[i]['step'] >= self.case_first_step]
        alert_ids = self._alert_ids(on)
        if on != 'first' and case_messages and case_messages[-1]['id'] not in alert_ids \
                and PROMPT_LOOK in case_messages[-1]['text'].lower():
            return case_messages[-1]['id']
        if not alert_ids:
            return None
        return alert_ids[-1]

    def do_step(self, step, header=None, notes_hook=None, control=False, control_any=False, notes_always=False):
        """Runs one step (a burst of chat messages, a due reminder, the report, a reply, a press, a wait).
        control: the step's alert gets the test controls (🧪 Next test / Rerun / Stop) as its last row of buttons."""
        from mysite.ai_agent import answer_review
        world, tap = self.world, self.world.tap
        world.flush_held()   # the clock moved: texts held for 08:00 go out, as the live flush job sends them (before this step)
        self.step += 1
        tap.step, tap.header, tap.notes_hook, tap.notes_in, tap.notes_always = self.step, header, notes_hook, None, notes_always
        tap.want_control = control and not self.auto
        tap.control_any = control_any
        tap.interactive = not self.auto
        before, earlier, note = check.snapshot(), self._last_with_buttons(), None
        try:
            if isinstance(step, list):
                moments = [world.moved(m['at']) for m in step if m.get('at')]
                if moments:
                    self._move_to(max(moments))
                events, note = world.queue_burst(step)
                if events:
                    note = self._process(events)
            elif 'reminder' in step:
                from mysite.models import AIFollowUp
                if str(step['reminder']) == 'next':   # the story: the earliest reminder still pending in the sandbox
                    followup = AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status=AIFollowUp.STATUS_PENDING).order_by('due_at', 'id').first()
                else:
                    followup = world.reminders.get(step['reminder'])
                if not followup:
                    note = f"no pending reminder to fire ('{step['reminder']}')"
                else:
                    self._move_to(followup.due_at)
                    events, note = world.fire_reminder(followup)
                    if events:
                        note = self._process(events)
            elif 'notification' in step:
                events, note = world.queue_notification(str(step['notification']), step.get('text'))
                if events:
                    note = self._process(events)
            elif 'report' in step:
                if REPORT_COMMAND not in get_commands():
                    note = f"NOT BUILT YET: the end-of-day report (manage.py {REPORT_COMMAND}) does not exist in this code"
                else:
                    call_command(REPORT_COMMAND, '--now', '--conversation', SANDBOX_SID)
            elif 'reply' in step:
                target = self._reply_target(step.get('on'))
                if not target:
                    note = "there is no alert to reply to"
                else:
                    world.simulate_reply(str(step['reply']), step.get('by') or 'Andy', target)
                    answer_review.poll_telegram()
            elif 'press' in step:
                ids = self._alert_ids('first') if step.get('on') == 'first' and self.case_alerts else None
                record, button = tap.find_button(str(step['press']), ids)
                if not button:
                    have = ", ".join(b.get('text') or '' for i in reversed(tap.order) for b in buttons_of(tap.messages[i]['markup'])
                                     if tap.messages[i]['step'] >= self.case_first_step)
                    note = f"the button \"{step['press']}\" does not exist (buttons in this case now: {have or 'none'})"
                else:
                    earlier = record
                    world.simulate_press(record, button, step.get('by') or 'Andy')
                    answer_review.poll_telegram()
            elif 'wait' in step:
                self._move_to(self.clock.now() + catalog.parse_delta(step['wait']))
            else:
                note = f"unknown step {step!r}"
        except Exception as e:
            self.out(traceback.format_exc())
            note = f"the step crashed in the test runner: {type(e).__name__}: {str(e)[:200]}"
        finally:
            tap.header = tap.notes_hook = None
            tap.want_control = tap.control_any = False
        got = check.collect(world, self.step, before, self.clock.now(), note=note, earlier=earlier,
                            answers=isinstance(step, dict) and ('reply' in step or 'press' in step))
        self.case_alerts += [a for a in got['alerts'] if a['type'] != 'REPLY']
        return got

    # -- one case ------------------------------------------------------------------------------------------------------
    def _step_time(self, step, default):
        if isinstance(step, list):
            moments = [self.world.moved(m['at']) for m in step if m.get('at')]
            return max(moments) if moments else default
        return self.world.moved(step['at']) if isinstance(step, dict) and step.get('at') else default

    def story_start(self, first_case):
        """The story begins: the sandbox is wiped and the story's rows are written (dates moved like the case times)."""
        world = self.world
        self.story_shift = week_shift(catalog.parse_time(first_case['time']) if first_case.get('time') else _local(real_now()))
        world.shift = timedelta(days=self.story_shift)
        try:
            self.out(f"Story start: {world.clean(catalog.story_block(), world.shift)}")
        except Exception as e:
            raise story.StoryError(f"the story could not start: {type(e).__name__}: {str(e)[:300]}") from e
        self.clock.set(_local(real_now()) if self.story_shift == 0 and not first_case.get('time') else
                       catalog.parse_time(first_case['time']) + world.shift - timedelta(minutes=1))
        story.snapshot(world, 'start')
        self.case_alerts, self.case_first_step = [], self.step + 1

    def story_restore(self, name):
        """Puts the sandbox back as it was after chapter `name` ('start' = right after the seed). Returns True when done."""
        world = self.world
        moment = story.restore(world, name)
        if moment is None:
            self.out(f"  no saved state after {name}: {('the chapter runs on what is in the sandbox now')}")
            self.rebuild_alerts()
            return False
        world.reset_memory()
        self.clock.set(moment)
        self.story_shift = world.shift.days
        self.out(f"  sandbox restored to the state after {name} (clock {_local(moment):%a %d %b %H:%M} ET)")
        self.rebuild_alerts()
        return True

    def rebuild_alerts(self):
        """The alerts of the story as the runner knows them (for replies and presses of later chapters), rebuilt from
        the restored runs: needed when this process did not post them itself (a run started in the middle of the
        story) and after a restore. Their buttons come from the run's current state; the text is not needed."""
        from mysite.ai_agent import alerts_v5
        from mysite.models import AIRun
        tap = self.world.tap
        if self.story_shift is None:
            self.story_shift = 0
        self.case_first_step = min(self.case_first_step or self.step, self.step)   # the rebuilt records belong to the story
        known = {a['id'] for a in self.case_alerts}
        rebuilt = []
        for run in AIRun.objects.filter(conversation_sid=SANDBOX_SID).exclude(telegram_message_id__isnull=True).order_by('id'):
            review = run.review or {}
            message_id = run.telegram_message_id
            if message_id in known:
                continue
            try:
                markup, text = alerts_v5.keyboard_for(run), alerts_v5.render_text(run)
            except Exception:
                markup, text = None, ''
            record = tap.messages.get(message_id) or {'id': message_id, 'text': text, 'posted': text, 'markup': markup, 'step': self.step,
                                                      'own': False, 'header': None, 'notes': None, 'silent': True, 'reply_to': None}
            record['markup'] = markup
            if text:
                record['text'] = text
            tap.messages[message_id] = record
            if message_id not in tap.order:
                tap.order.append(message_id)
            rebuilt.append(dict(record, type=check.EVENT_ALERT.get(run.event_type, run.event_type), in_header=False, run=run, parts=[record]))
            for proposal in review.get('proposals') or []:
                pid = proposal.get('message_id')
                if pid and pid not in tap.messages:
                    tap.messages[pid] = {'id': pid, 'text': proposal.get('report') or '', 'posted': '', 'step': self.step, 'own': False,
                                         'markup': alerts_v5.proposal_keyboard(run.id, proposal), 'header': None, 'notes': None,
                                         'silent': True, 'reply_to': message_id}
                    tap.order.append(pid)
        # In story order: the alerts this process posted keep their place, the rebuilt ones go before them
        self.case_alerts = rebuilt + [a for a in self.case_alerts if a['id'] not in {r['id'] for r in rebuilt}]

    def run_case(self, case, index, total, first):
        """Steps 1-5 of one chapter. Returns the result dict (verdict PASS / FAIL, the collected result).
        first: the story begins with this chapter (clean start + seed); else it goes on from the sandbox as it is."""
        world = self.world
        if first or self.story_shift is None:
            self.story_start(case)
        shift_days = self.story_shift
        world.shift = timedelta(days=shift_days)
        case_time = catalog.parse_time(case['time']) if case.get('time') else _local(self.clock.now())
        now = case_time + world.shift
        if now < self.clock.now():
            self.out(f"  note: the chapter's time {_local(now):%a %d %b %H:%M} is before the sandbox clock "
                     f"{_local(self.clock.now()):%a %d %b %H:%M} - the clock does not go back")
            now = self.clock.now()
        self.pending_change, self.chapter_first_step = None, self.step + 1
        if first:
            self.case_alerts, self.case_first_step = [], self.step + 1
        section = catalog.doc_section(case)
        prelude = list(case.get('prelude') or [])
        times = [self._step_time(s, now - timedelta(minutes=5 * (len(prelude) - i))) for i, s in enumerate(prelude)]
        self.clock.forward_to(min(times + [now]) - timedelta(minutes=1))
        for line in world.setup(case, now):
            self.out(f"  CRM: {line}")
        self.out(f"\n=== {case['id']} · {case.get('title') or ''} · {index} of {total} · case time {_local(now):%a %d %b %H:%M} ET"
                 + (f" (document date +{shift_days} days)" if shift_days else "") + f" · {world.mode} ===")

        for moment, step in zip(times, prelude):
            self.clock.forward_to(moment)
            self.do_step(step, header=f"🧪 SANDBOX TEST · case {case['id']} · set-up step")
        self.clock.forward_to(self._step_time(case['trigger'], now))

        notes = {}

        def notes_hook(text, markup):
            # One form for every chapter: what we test + the exact presses of the chapter (also for a bot answer to a reply)
            notes['text'], cost = check.test_notes(case, section, text, markup, clickable=world.own_bot, use_claude=self.use_claude)
            self.cost += cost
            return notes['text']

        history = check.chat_before(world, self.clock.now())   # the story so far, for the judge and the diagnosis
        got = self.do_step(case['trigger'], header=f"🧪 SANDBOX TEST · case {case['id']} · {index} of {total}", notes_hook=notes_hook,
                           control=True)
        notes_inline = world.tap.notes_in not in (None, 'pending')
        self.out(check.render_result(got))
        lines = check.structured(case, got, world)
        suggestion = ''
        if self.use_claude:
            judged = check.judge(case, section, got, lines, shift_days, history=history)
            lines += judged['lines']
            suggestion, self.cost = judged['suggestion'], self.cost + judged['cost']
        pressed = []
        if self.auto:
            for press in case.get('presses') or []:
                # "Create Task | Apply Update": the first of the alternatives that exists on the alert (the AI may word
                # the same thing as a new task or as an update of the open one)
                choices = [c.strip() for c in str(press['button']).split('|') if c.strip()]
                button = next((c for c in choices if self.world.tap.find_button(c)[1]), None)
                if button is None and press.get('optional'):
                    continue   # the AI did not propose it this time: nothing to press
                after = self.do_step({'press': button or choices[0], 'by': press.get('by') or 'Andy'})
                seen = "\n".join([m['text'] for m in after['others']] + after['popups'] + [a['text'] for a in after['alerts']]
                                 + [b.get('text') or '' for b in buttons_of((after['earlier'] or {}).get('markup'))])
                ok = not after['note'] and (not press.get('expect') or str(press['expect']).lower() in seen.lower())
                lines.append((ok, f"press {press['button']}" + (f" → \"{press['expect']}\"" if press.get('expect') else "") if ok else
                              f"press {press['button']}: " + (after['note'] or f"expected \"{press.get('expect')}\", got \"{seen.strip()[:200] or 'nothing'}\"")))
                pressed.append((press['button'], check.render_result(after)))
        text, passed = check.verdict_text(case['id'], lines, suggestion)
        alert = check.main_alert(got, (case.get('expect') or {}).get('alert'))
        anchor = alert['parts'][-1]['id'] if alert else None
        if notes.get('text') and alert and not notes_inline:
            self.post(notes['text'], reply_to=anchor)   # the alert was too long to carry the notes itself
        self.out("\n" + text)
        diag = None
        if not passed:   # the test group only hears about what did not pass - with where the fix belongs and two buttons
            diag = check.diagnose(case, got, lines, history) if self.use_claude else None
            self.cost += (diag or {}).get('cost') or 0
            self.out(check.diagnosis_text(diag))
            markup = {'inline_keyboard': [[{'text': '🔧 Fix', 'callback_data': f"sbx|fix|{case['id']}"},
                                           {'text': '👌 No fix needed', 'callback_data': f"sbx|nofix|{case['id']}"}]]} if self.world.own_bot else None
            self.post(check.failure_text(case['id'], lines, suggestion) + "\n\n" + check.diagnosis_text(diag), reply_to=anchor,
                      shown=False, markup=markup)
        self._controls_without_alert(f"case {case['id']} · {case.get('title') or ''}", got, passed)
        reason = "; ".join(line for ok, line in lines if ok is False)[:3000]
        result = {'id': case['id'], 'verdict': 'PASS' if passed else 'FAIL', 'reason': reason, 'got': got, 'anchor': anchor,
                  'notes_inline': notes_inline, 'diagnosis': diag}
        self._write_report(case, got, text, pressed, notes.get('text'), now)
        catalog.save_result(case['id'], result['verdict'], reason, self.run_name)
        world.seal_runs()
        return result

    def _controls_without_alert(self, title, got, passed):
        """Nothing was posted that could carry the test controls (a "no alert" case): they get a line of their own."""
        tap = self.world.tap
        if not self.world.own_bot or self.auto or tap.control:
            return
        what = "no alert was posted" + (" – as expected ✅" if passed and not got['alerts'] else "") if not got['alerts'] \
            else "done – what the bot answered is above"
        message_id = self.post(f"🧪 {title}\n{what[:1].upper()}{what[1:]}.", markup={'inline_keyboard': [tap.control_row]}, shown=False)
        if message_id:
            tap.clear_busy()
            tap.control.add(message_id)

    def _write_report(self, case, got, verdict, pressed, notes, now):
        shown = {k: v for k, v in case.items() if k != 'id'}
        runs = "\n".join(f"- AI run #{run.id}: {run.report_dir}/report.md" for run in got['runs']) or "- no AI run"
        parts = [
            f"# {case['id']} · {case.get('title') or ''}", f"Run {self.run_name} · case time {_local(now):%a %d %b %Y %H:%M} ET",
            "## Input (the case)", "```yaml\n" + yaml.safe_dump(shown, allow_unicode=True, sort_keys=False, width=110) + "```",
            "## What the agent did", "```\n" + check.render_result(got) + "\n```",
        ]
        if notes:
            parts += ["## Test notes", "```\n" + notes + "\n```"]
        for button, result in pressed:
            parts += [f"## Pressed: {button}", "```\n" + result + "\n```"]
        parts += ["## Verdict (expected vs got)", "```\n" + verdict + "\n```", "## AI run reports", runs]
        (self.run_dir / f"{case['id']}.md").write_text("\n\n".join(parts) + "\n", encoding='utf-8')

    # -- waiting for the person ---------------------------------------------------------------------------------------------
    def _fire_due(self):
        """--real-time: the runner is the sandbox worker, so it fires the sandbox reminders whose time has come."""
        from mysite.models import AIFollowUp
        due = AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status=AIFollowUp.STATUS_PENDING, due_at__lte=self.clock.now())
        for followup in due[:5]:
            self.out(f"  reminder {followup.public_id} is due: {followup.reason}")
            events, note = self.world.fire_reminder(followup)
            self.step += 1
            self.world.tap.step, self.world.tap.header = self.step, "🧪 SANDBOX TEST · reminder became due"
            self.out(self._process(events) or "  alert posted" if events else f"  {note}")
            self.world.tap.header = None

    def _read_command(self):
        """A test command typed in the terminal or sent in the sandbox group, or None after about 2 seconds."""
        from mysite.ai_agent import answer_review
        if self.world.own_bot:
            answer_review.poll_telegram()   # buttons / replies on the alerts are handled here; commands are set aside
            if self.world.commands:
                return self.world.commands.pop(0)['text']
        if sys.stdin and sys.stdin.isatty():
            ready, _, _ = select.select([sys.stdin], [], [], 2.0)
            if ready:
                return sys.stdin.readline().strip()
        else:
            time.sleep(2.0)
        return None

    TEAM_NAMES = ('Edy', 'Kevin', 'Janna', 'Farid', 'Andy')

    def _typed_messages(self, case):
        """Messages a person typed in the sandbox chat on the site (💬 CRM chat): each is handled as one more step of
        this case - "Manager" = a team member (Edy, or "Kevin: text" for another one), "Client" = the tenant."""
        world = self.world
        world.take_typed()
        while world.typed:
            typed = world.typed.pop(0)
            text = typed['text'].strip()
            if not text:
                continue
            if typed['kind'] == 'tenant':
                who = case.get('tenant') or getattr(self.world.tenant, 'full_name', None) or 'Tenant'
                message = {'from': 'tenant', 'name': who, 'text': text}
            else:
                who = 'Edy'
                named = re.match(r'^(' + '|'.join(self.TEAM_NAMES) + r')\s*:\s*(.+)$', text, re.I | re.S)
                if named:
                    who, text = named.group(1).capitalize(), named.group(2).strip()
                message = {'from': 'team', 'name': who, 'text': text}
            self.out(f"\n  [CRM chat] {who} wrote: {text[:200]}")

            def notes_hook(alert_text, markup, who=who, text=text):
                # No catalog case for a typed message: what we test = your message; the buttons of this alert, then Next
                guide = press_guide(markup, then_next=True, alert=True)
                return (f"🧪 TEST NOTES\nWhat we test: your own message in the CRM chat (as {who}) - no expected result, "
                        f"check that the alert makes sense.\n" + (guide or "Nothing to press here. Press 🧪 Next test."))
            got = self.do_step([message], header=f"🧪 SANDBOX TEST · case {case['id']} · {who} wrote in the CRM chat",
                               notes_hook=notes_hook, control=True, control_any=True, notes_always=True)
            self.out(check.render_result(got))
            if not got['alerts']:
                self.post(f"🧪 {who} wrote in the CRM chat: \"{text[:300]}\"\n→ no alert: "
                          + (got.get('note') or "the AI had nothing to tell the team about this message."))

    def _propose(self, case, result, request):
        proposal = check.propose_change(case, catalog.doc_section(case), result.get('got'), request)
        if proposal.get('error'):
            return self.post(f"⚠️ Could not prepare the change ({proposal['error']}).", reply_to=result.get('anchor'))
        self.cost += proposal.get('cost') or 0
        if not (proposal['yaml'] or proposal['doc_example']):
            return self.post(f"ℹ️ Nothing to change.\n{proposal['explanation']}", reply_to=result.get('anchor'))
        self.pending_change = proposal
        where = [name for name, value in (('sandbox_cases.yaml', proposal['yaml']), ('the example in simple_telegram_alerts.md', proposal['doc_example'])) if value]
        self.post(f"✏️ I WILL CHANGE the test {case['id']} ({' and '.join(where)})\n{proposal['explanation']}\n"
                  f"After the press: the files are changed; write \"rerun\" to run the case with them.\n"
                  + ("" if self.world.own_bot else "\nType \"apply\" in the terminal to apply it."),
                  reply_to=result.get('anchor'),
                  markup={'inline_keyboard': [[{'text': '✅ Apply Change', 'callback_data': f"sbx|apply|{case['id']}"}]]})

    def _propose_new(self, case, result):
        """➕ Add to tests on a replayed message: a new catalog case is proposed; it is added only after ✅ Apply Change."""
        replay = result.get('replay')
        if not replay:
            return self.post("ℹ️ \"Add to tests\" is for a replayed real chat (test-conversation CH…). This is already a test case; "
                             "use \"change test: …\" to change it.", reply_to=result.get('anchor'))
        proposal = check.propose_new_case(replay)
        if proposal.get('error'):
            return self.post(f"⚠️ Could not prepare the test case ({proposal['error']}).", reply_to=result.get('anchor'))
        self.cost += proposal.get('cost') or 0
        case_id = catalog.next_case_id(proposal['group'])
        block = proposal['yaml'].replace('NEW:', f"{case_id}:", 1)
        self.pending_change = dict(proposal, new=case_id, yaml=block)
        shown = block if len(block) <= 2200 else block[:2200] + "\n… (the rest is in the file after the press)"
        self.post(f"➕ I WILL ADD the test {case_id} · {proposal['title']}\n{proposal['explanation']}\n\n"
                  f"The case (testbed/sandbox_cases.yaml):\n{shown}\n\n"
                  f"After the press: the case is in the case file and its example in the document (part {case_id[0]}); "
                  f"\"run {case_id}\" runs it." + ("" if self.world.own_bot else "\nType \"apply\" in the terminal to add it."),
                  reply_to=result.get('anchor'),
                  markup={'inline_keyboard': [[{'text': '✅ Apply Change', 'callback_data': f"sbx|apply|{case_id}"}]]})

    def _apply(self, case, result):
        proposal, self.pending_change = self.pending_change, None
        if not proposal:
            return self.post("ℹ️ There is no change waiting.", reply_to=result.get('anchor'))
        if proposal.get('new'):
            error = catalog.add_case(proposal['new'], proposal['yaml'], proposal['title'], proposal['doc_example'],
                                     note=f"Added from a real conversation (replay) on {_local(real_now()):%-d %b %Y}.")
            return self.post(f"⚠️ Not added: {error}" if error else
                             f"✅ Added · test {proposal['new']} \"{proposal['title']}\". Write \"run {proposal['new']}\" to run it.",
                             reply_to=result.get('anchor'))
        done = []
        if proposal['yaml']:
            error = catalog.replace_case_yaml(case['id'], proposal['yaml'])
            done.append(f"case file: {error}" if error else "case file changed")
        if proposal['doc_example']:
            done.append("document example changed" if catalog.replace_doc_example(case, proposal['doc_example'])
                        else "document: the example block was not found, nothing changed")
        self.post(f"✅ Changed · {'; '.join(done)}. Write \"rerun\" to run {case['id']} again.", reply_to=result.get('anchor'))

    def wait(self, case, result):
        """Step 6. Returns 'next' | 'rerun' | 'skip' | 'stop' | ('rerun with', text)."""
        interactive = self.world.own_bot or (sys.stdin and sys.stdin.isatty())
        if self.auto or not interactive:
            return 'next'
        self.out("\nWaiting: next · rerun · rerun with: <text> · change test: <what> · accept · skip · stop"
                 + ("  (the 🧪 buttons under the alert, or as a reply to a bot message in the test group)" if self.world.own_bot
                    else "  (type it here)"))
        while True:
            if self.real_time:
                self._fire_due()
            self._typed_messages(case)
            text = self._read_command()
            command = parse_command(text)
            if not command:
                if text:
                    self.out("  commands: next / continue, rerun, rerun with: <text>, change test: <what>, accept, skip, stop, "
                             "restart, run <cases>, test-conversation <CH...>")
                continue
            word, rest = command
            if word in ('next', 'rerun', 'skip', 'stop', 'restart'):
                return word
            if word == 'continue':
                return 'next'
            if word == 'rerun with':
                return ('rerun with', rest)
            if word in ('run', 'test conversation'):
                return (word, rest)   # another list of cases / a real chat: decided one level up (serve)
            if word == 'add test':
                self._propose_new(case, result)
                continue
            if word == 'fix':
                self._fix_requested(case, result)
                continue
            if word == 'nofix':
                result['verdict'], result['reason'] = 'ACCEPTED', 'no fix needed (the team)'
                catalog.save_result(case['id'], 'ACCEPTED', result['reason'], self.run_name)
                self.post(f"👌 {case['id']}: no fix needed - the chapter counts as accepted. Go on with 🧪 Next test.", reply_to=result.get('anchor'), shown=False)
                self.out(f"  {case['id']}: ACCEPTED by the team (no fix needed)")
                continue
            if word == 'apply':
                self._apply(case, result)
            elif word == 'change test':
                self._propose(case, result, rest)
            elif word == 'accept':
                self._propose(case, result, "The agent's result is right and the example in the document is wrong: put the "
                                            "agent's version into the example (keep the document's layout) and adjust "
                                            "the expectations in the case file to it.")

    def _fix_requested(self, case, result):
        """🔧 Fix pressed: the request is written down for the engineer watching the run (the log and a file); the
        chapter is run again from its saved state once the fix is in."""
        diag = result.get('diagnosis') or {}
        line = {'case': case['id'], 'at': real_now().isoformat(), 'where': diag.get('where'), 'what': diag.get('what'),
                'files': diag.get('files'), 'reason': result.get('reason'), 'run': self.run_name}
        catalog.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(catalog.RESULTS_DIR / 'fix_requests.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.out(f"FIX REQUESTED · {case['id']} · {diag.get('where') or '?'}: {diag.get('what') or result.get('reason')} "
                 f"· files: {', '.join(diag.get('files') or []) or '?'}")
        self.post(f"🔧 {case['id']}: fix requested - it is being fixed" + (f" in {', '.join(diag['files'])}" if diag.get('files') else "")
                  + f". When it is in, this chapter runs again from where it started. Meanwhile you can go on with 🧪 Next test.",
                  reply_to=result.get('anchor'), shown=False)

    # -- runs ----------------------------------------------------------------------------------------------------------
    TOP = ('run', 'test conversation')   # commands that end the running list and are carried out by serve()

    def _before(self, cases, index):
        """The name of the saved state a chapter starts from: the chapter before it in the story, else 'start'."""
        order = [c['id'] for c in catalog.story_order()]
        mine = order.index(cases[index]['id']) if cases[index]['id'] in order else -1
        return order[mine - 1] if mine > 0 else 'start'

    def run_cases(self, cases, label, start=0, fresh=False, restore=True):
        """Returns None when the list ended (or "stop"), else the command that ended it: 'restart' or (word, text).
        fresh: the story begins (clean start + seed). Else the sandbox is put back to the saved state before the
        first chapter to run (restore), or goes on as it is ("continue")."""
        index, override = start, None
        first = fresh or (self.story_shift is None and not story.snapshot_names())
        if not first and restore and cases:
            self.story_restore(self._before(cases, start))
        elif not first and self.story_shift is None:
            self.story_shift, _ = story.saved_shift()
            self.rebuild_alerts()
        while index < len(cases):
            case = dict(catalog.load_cases().get(cases[index]['id']) or cases[index])   # re-read: "change test" edits the file
            if case.get('not_built'):
                if first:
                    self.story_start(case)
                    first = False
                self.out(f"\n=== {case['id']} · {case.get('title') or ''} · {index + 1} of {len(cases)} · SKIPPED: {case['not_built']} ===")
                catalog.save_result(case['id'], 'SKIPPED', f"not built yet: {case['not_built']}", self.run_name)
                self.results.append({'id': case['id'], 'verdict': 'SKIPPED', 'reason': f"not built yet: {case['not_built']}"})
                story.snapshot(self.world, case['id'], {'verdict': 'SKIPPED'})
                index += 1
                self.position = index
                continue
            if override is not None:
                if not isinstance(case['trigger'], list):
                    self.out("  'rerun with' needs a case whose trigger is a chat message - running it unchanged")
                else:
                    case['trigger'] = [dict(m) for m in case['trigger']]
                    case['trigger'][-1]['text'] = override
            try:
                result = self.run_case(case, index + 1, len(cases), first)
            except story.StoryError:
                raise   # the story could not start (seed): nothing to run on
            except Exception as e:
                self.out(traceback.format_exc())
                result = {'id': case['id'], 'verdict': 'FAIL', 'reason': f"the test runner crashed: {type(e).__name__}: {str(e)[:200]}"}
                catalog.save_result(case['id'], 'FAIL', result['reason'], self.run_name)
            first, override = False, None
            command = self.wait(case, result)
            self.world.tap.drop_control()
            if command == 'rerun' or (isinstance(command, tuple) and command[0] == 'rerun with'):
                override = command[1] if isinstance(command, tuple) else None
                self.story_restore(self._before(cases, index))   # rerun = the same chapter again from where it started
                self.case_alerts = [a for a in self.case_alerts if a['step'] < self.chapter_first_step]
                continue
            # The chapter is over, with what the tester pressed under it: that is the state the next chapter starts from
            story.snapshot(self.world, case['id'], {'verdict': result.get('verdict')})
            if command == 'skip':
                result = dict(result, verdict='SKIPPED', reason='skipped by the tester')
                catalog.save_result(case['id'], 'SKIPPED', result['reason'], self.run_name)
            self.results.append(result)
            self.position = index + 1
            if command == 'restart' or (isinstance(command, tuple) and command[0] in self.TOP):
                return command
            if command == 'stop':
                break
            index += 1
        self.summary(label, len(cases))
        return None

    # -- the run as a whole: the list, and what a person asks for in the test group --------------------------------------
    def serve(self, cases, label, stay=False, conversation=None, begin=0):
        """
        Runs the list (or replays a real chat) and carries out the commands that change WHAT is run:
          restart                      the chat is cleaned and the list starts again with its first case
          continue                     (after "stop" or a replay) goes on with the next case of the list
          run A5 / run A,B             another list of cases
          test-conversation CHxxxx [last 20]   replays a real chat (no verdicts; ➕ Add to tests makes a case of a message)
        stay: when nothing is left to run, wait in the test group for one of these instead of ending.
        """
        selection = list(cases or [])
        # ('cases', start, fresh, restore): a fresh story when the list begins with the story's first chapter, else
        # from the saved state before the first chapter to run (--from, --cases A5)
        order = catalog.story_order()
        fresh = begin == 0 and bool(selection) and bool(order) and selection[0]['id'] == order[0]['id']
        plan = ('conversation',) + tuple(conversation) if conversation else ('cases', begin, fresh, True)
        can_wait = stay and not self.auto and (self.world.own_bot or (sys.stdin and sys.stdin.isatty()))
        while True:
            command = None
            if plan:
                try:
                    command = self.run_cases(selection, label, start=plan[1], fresh=plan[2], restore=plan[3]) \
                        if plan[0] == 'cases' else self.run_conversation(*plan[1:])
                except catalog.CatalogError as e:
                    self.post(f"⚠️ {e}")
            if command is None:
                if not can_wait:
                    return
                command = self.wait_idle(selection, label)
            word, rest = (command, '') if isinstance(command, str) else command
            plan = None
            if word == 'restart':
                self.clean_chat()
                self.results, plan = [], ('cases', 0, True, True)
            elif word == 'continue':
                if self.position < len(selection):
                    plan = ('cases', self.position, False, False)
                else:
                    self.post(f"ℹ️ The list ({label}) is finished. Write \"restart\" to run it again from the first case, "
                              f"or \"run A5\" for other cases.")
            elif word == 'run':
                try:
                    selection, label = catalog.pick(rest), f"cases {rest.upper()}"
                    self.results, self.position = [], 0
                    plan = ('cases', 0, False, True)   # from the saved state before its first chapter
                except catalog.CatalogError as e:
                    self.post(f"⚠️ {e}")
            elif word == 'test conversation':
                parts = rest.split()
                plan = ('conversation', parts[0], int(parts[-1]) if len(parts) > 1 else None, None)

    def wait_idle(self, selection, label):
        """Nothing is running: waits in the test group for continue / restart / run … / test-conversation …."""
        left = len(selection) - self.position
        self.world.tap.drop_control()
        message_id = self.post(
            "⏸ Nothing is running now.\n"
            + (f"▶️ continue – next case of {label} ({left} left)\n" if left > 0 else "")
            + "🔄 restart – clean this chat and start the list from its first case\n"
              "run A5 (or: run A,B) – other cases\n"
              "test-conversation CHxxxx (optional: last 20) – replay a real chat\n"
              "Write a command as a reply to this message, or as /continue, /restart …",
            markup={'inline_keyboard': [IDLE_ROW if left > 0 else IDLE_ROW[1:]]} if self.world.own_bot else None)
        if message_id:
            self.world.tap.clear_busy()
            self.world.tap.control.add(message_id)
        free = {'id': 'free chat', 'tenant': getattr(self.world.tenant, 'full_name', None) or 'Tenant'}
        while True:
            self._typed_messages(free)
            command = parse_command(self._read_command())
            if not command:
                continue
            word, rest = command
            if word in ('next', 'continue'):
                return 'continue'
            if word == 'restart' or word in self.TOP:
                return (word, rest) if rest else word
            if word == 'apply':
                self._apply(free, {})

    def summary(self, label, total):
        counts = {v: [r for r in self.results if r['verdict'] == v] for v in ('PASS', 'FAIL', 'SKIPPED')}
        minutes = (time.monotonic() - self.started) / 60
        lines = [f"🧪 SANDBOX TEST RUN · {_local(real_now()):%-d %b, %a %H:%M} ET · {label} ({total} case{'s' if total != 1 else ''})", "",
                 f"✅ {len(counts['PASS'])} passed"]
        if counts['FAIL']:
            lines.append(f"❌ {len(counts['FAIL'])} failed: " + ", ".join(f"{r['id']} ({r['reason'][:70]})" for r in counts['FAIL'])[:2600])
        if counts['SKIPPED']:
            lines.append(f"⏭ {len(counts['SKIPPED'])} skipped: " + ", ".join(r['id'] for r in counts['SKIPPED']))
        not_run = total - len(self.results)
        if not_run > 0:
            lines.append(f"⏹ {not_run} not run (stopped)")
        lines += [f"💰 cost ${self.cost:.2f} · {minutes:.0f} min", "", f"🔗 logs/sandbox_tests/{self.run_name}/summary.md"]
        details = [f"# Sandbox test run {self.run_name} · {label}", "", "| Case | Result | Why |", "|---|---|---|"]
        details += [f"| {r['id']} | {r['verdict']} | {r['reason'].replace('|', '/') or '-'} |" for r in self.results]
        (self.run_dir / 'summary.md').write_text("\n".join(lines) + "\n\n" + "\n".join(details) + "\n", encoding='utf-8')
        if total > 1 or counts['FAIL']:
            self.post("\n".join(lines))
        else:   # one case, nothing failed: nothing to tell the group
            self.out("\n" + "\n".join(lines))

    # -- a real conversation ------------------------------------------------------------------------------------------------
    def run_conversation(self, sid, last=None, since=None):
        """Plays the messages of a real chat one by one into the clean sandbox (11.6). Nothing is written to the real chat."""
        from mysite.ai_agent import inputs
        from mysite.models import AIIssue, Booking, TwilioConversation, TwilioMessage, User

        world = self.world
        real = TwilioConversation.objects.select_related('apartment', 'booking__tenant').filter(conversation_sid=sid).first()
        if not (real and real.apartment_id and real.booking_id):
            raise catalog.CatalogError(f"chat {sid} does not exist or is not linked to an apartment + booking")
        messages = TwilioMessage.objects.filter(conversation_sid=sid).exclude(message_sid__startswith='KB-UPDATE-')
        if since:
            messages = messages.filter(message_timestamp__gte=catalog.parse_time(since))
        messages = list(messages.order_by('message_timestamp', 'id'))
        messages = messages[-last:] if last else messages
        if not messages:
            raise catalog.CatalogError("no messages to replay")
        ai_answers = inputs._ai_answers(sid)
        bursts = []   # messages within 1 minute from the same side are one burst, like in production
        for message in messages:
            role, name = inputs.classify_sender(message, ai_answers)
            side = {'TENANT': 'tenant', 'STAFF': 'team', 'AI': 'ai'}[role]
            if bursts and bursts[-1]['side'] == side and message.message_timestamp - bursts[-1]['messages'][-1].message_timestamp <= timedelta(seconds=60):
                bursts[-1]['messages'].append(message)
            else:
                bursts.append({'side': side, 'name': name, 'messages': [message],
                               'runs': side == 'tenant' or (side == 'team' and name != 'Automated CRM message')})
        total = sum(1 for b in bursts if b['runs'])

        shift = timedelta(days=week_shift(_local(messages[0].message_timestamp)))
        self.out(f"Clean start: {world.clean({}, shift)}")
        self.story_shift = None   # the story has to start again after a replay
        world.real_context = (real.apartment, real.booking)   # the agent READS the real apartment + booking data
        world.mode = 'test'
        tenant_name = getattr(real.booking.tenant, 'full_name', None) or 'Tenant'
        User.objects.filter(id=world.tenant.id).update(full_name=tenant_name)
        Booking.objects.filter(id=world.booking.id).update(start_date=real.booking.start_date + shift, end_date=real.booking.end_date + shift)
        self.clock.set(messages[0].message_timestamp + shift - timedelta(minutes=1))
        tail, label, number = f"CH…{sid[-4:]}", f"replay {sid}", 0
        self.case_alerts, self.case_first_step = [], self.step + 1
        world.tap.control_row = REPLAY_ROW

        for position, burst in enumerate(bursts):
            author = {'tenant': None, 'ai': 'Virtual Assistant'}.get(burst['side'], 'keep')
            step = [{'from': burst['side'], 'text': m.body, 'moment': m.message_timestamp + shift,
                     'author': m.author if author == 'keep' else author} for m in burst['messages']]
            self.clock.forward_to(step[-1]['moment'])
            if not burst['runs']:
                for message in step:
                    world.add_message(message, message['moment'], author=message['author'])
                continue
            number += 1
            real_at = _local(burst['messages'][-1].message_timestamp)
            after = [b for b in bursts[position + 1:]]
            stop = next((i for i, b in enumerate(after) if b['side'] == 'tenant'), len(after))
            did = [f"- {b['name']} answered at {_local(b['messages'][0].message_timestamp):%H:%M}: \"{' '.join(m.body for m in b['messages'])[:300]}\""
                   for b in after[:stop] if b['side'] == 'team'][:3]
            tasks = AIIssue.objects.filter(conversation_sid=sid, created_at__gt=burst['messages'][-1].message_timestamp,
                                           created_at__lte=burst['messages'][-1].message_timestamp + timedelta(hours=24)) \
                .exclude(ticket_title__isnull=True).exclude(ticket_title='')
            did += [f"- ClickUp task \"{i.ticket_title}\" created {_local(i.created_at):%H:%M}" for i in tasks[:3]]
            team_next = "\n".join(did)
            notes = (f"🧪 TEST NOTES\nReal chat {real.apartment.name}, {real_at:%-d %b %H:%M}. What the team did next:\n"
                     f"{team_next or '- nothing (no team message before the next tenant message)'}\n"
                     + (f"🧪 Next message = message {number + 1} of {total}." if number < total else "This was the last message.")
                     + "\n➕ Add to tests = make a test case of this message (you approve it first).")
            self.out(f"\n=== REPLAY {tail} · message {number} of {total} · {burst['side']} · real time {real_at:%a %d %b %H:%M} ET ===")
            got = self.do_step(step, header=f"🧪 REPLAY · {tail} · message {number} of {total}", notes_hook=lambda text, markup: notes,
                               control=True)
            notes_inline = world.tap.notes_in not in (None, 'pending')
            self.out(check.render_result(got))
            # A replay is for LOOKING: no verdict, no judge (user, 2026-10-06). ➕ Add to tests makes a case of this message.
            name = f"{tail} message {number}"
            alert = check.main_alert(got)
            anchor = alert['parts'][-1]['id'] if alert else None
            if alert and not notes_inline:
                self.post(notes, reply_to=anchor)
            if got['note']:
                self.out(f"  note: {got['note']}")
            history = [f"[{_local(m.message_timestamp):%Y-%m-%d %H:%M}] {b['name'] if b['side'] != 'tenant' else tenant_name} "
                       f"({b['side']}): {m.body}" for b in bursts[:position] for m in b['messages']]
            result = {'id': f"message {number}", 'verdict': 'SEEN', 'reason': got['note'] or '', 'got': got, 'anchor': anchor,
                      'replay': {'apartment': real.apartment.name, 'tenant': tenant_name, 'when': f"{real_at:%a %Y-%m-%d %H:%M} ET",
                                 'history': history, 'result': check.render_result(got),
                                 'trigger': [f"{burst['name'] if burst['side'] != 'tenant' else tenant_name} ({burst['side']}): {m['text']}"
                                             for m in step]}}
            (self.run_dir / f"message_{number:03d}.md").write_text(
                f"# {name} · {burst['side']} · real time {real_at:%Y-%m-%d %H:%M} ET\n\n## New message(s)\n\n"
                + "\n".join(f"- {m['text']}" for m in step) + "\n\n## What the agent did\n\n```\n" + check.render_result(got)
                + "\n```\n\n## What the team really did next\n\n" + (team_next or '- nothing') + "\n", encoding='utf-8')
            world.seal_runs()
            self._controls_without_alert(f"replay {name}", got, True)
            command = self.wait({'id': name, 'doc': None}, result)
            world.tap.drop_control()
            if command == 'rerun' or (isinstance(command, tuple) and command[0] == 'rerun with'):
                self.out("  rerun is for catalog cases; a replay goes on with the next message")
            if command == 'skip':
                result = dict(result, verdict='SKIPPED', reason='skipped by the tester')
            self.results.append(result)
            if command == 'restart' or (isinstance(command, tuple) and command[0] in self.TOP):
                world.tap.control_row = CONTROL_ROW
                return command
            if command == 'stop':
                break
        world.tap.control_row = CONTROL_ROW
        self.post(f"🧪 Replay of {tail} ended ({number} of {total} message(s) shown).", shown=True)
        return None


def list_cases(out=print):
    """--list: every case with its last result."""
    cases, results, sections = catalog.load_cases(), catalog.last_results(), catalog.doc_sections()
    icon = {'PASS': '✅', 'FAIL': '❌', 'SKIPPED': '⏭', 'ACCEPTED': '👌'}
    day = None
    for case_id, case in cases.items():
        if str(case.get('time') or '')[:10] != day:   # the story: one block per day
            day = str(case.get('time') or '')[:10]
            out(f"\n{day or '(no time)'}")
        last = results.get(case_id)
        state = f"{icon.get(last['verdict'], '?')} {last['verdict']} {last['at']}" if last else "– not run yet"
        missing = "" if (case.get('doc') or case_id) in sections else "  ⚠ no section in the document"
        out(f"  {case_id:<5} {str(case.get('title') or '')[:58]:<58} {state}{missing}"
            + (f"\n          {last['reason'][:150]}" if last and last['verdict'] == 'FAIL' and last.get('reason') else ""))
    done = [results[c]['verdict'] for c in cases if c in results]
    out(f"\n{len(cases)} cases · {done.count('PASS')} passed · {done.count('FAIL')} failed · {done.count('SKIPPED')} skipped · "
        f"{len(cases) - len(done)} not run yet")
    uncovered = sorted(set(sections) - {case.get('doc') or case_id for case_id, case in cases.items()}, key=catalog.sort_key)
    if uncovered:
        out(f"⚠ document sections without a case: {', '.join(uncovered)}")
