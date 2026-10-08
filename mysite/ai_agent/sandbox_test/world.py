"""
The sandbox world of a test run: the DB-only sandbox chat on the test apartment, its clean start and set-up, and
the isolation layer that keeps everything a test does inside the sandbox.

What the layer does IN THIS PROCESS ONLY (the live worker and the web server never see these patches):
- Telegram: every bot call goes through TelegramTap - recorded for the checks, sent to the sandbox chat with a test
  header. Without an own sandbox bot the buttons are written as text: the live worker owns the main bot's updates,
  so a real press would be handled by production code.
- Tenant SMS: a send to the sandbox chat becomes a message in the sandbox chat; a send to ANY other chat is refused.
- Phone calls: simulated. Twilio is unreachable from this process.
- ClickUp: new tasks only in the TEST list, named [SANDBOX]; tasks given by a case's set-up are fakes in memory.
- Knowledge and rules: kept in a sandbox file and shown to the agent, never written to the real knowledge base /
  prompts. The clean start deletes them.
- live / test mode and "AI Auto ClickUp ON / OFF" are what the case says, whatever the site says.
"""
import copy
import io
import json
import os
import re
import threading
import uuid
from datetime import date, timedelta

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction

from mysite.ai_agent.sandbox_test import catalog
from mysite.ai_agent.sandbox_test.clock import real_now
from mysite.management.commands.ai_agent_sandbox import (
    SANDBOX_NOTE, SANDBOX_SID, SANDBOX_TENANT_EMAIL, SANDBOX_TENANT_PHONE, Command as SandboxCommand,
)

SANDBOX_SID_2 = "CHSANDBOXAIAGENT00000000000000002"   # a second chat of the sandbox tenant ("created by mistake"), only while a case needs it
SANDBOX_SIDS = (SANDBOX_SID, SANDBOX_SID_2)
FAKE_TASK = 'sandbox://task/'
FAKE_UPDATE_BASE = 9_000_000_000_000
STATE_FILE = catalog.RESULTS_DIR / '.sandbox_state.json'
POSTED_FILE = catalog.RESULTS_DIR / '.posted_messages.jsonl'   # what test runs posted in Telegram, for --clean-chat
REMINDER_KINDS = {'staff': 'staff_reminder', 'tenant': 'tenant_nudge', 'deadline': 'deadline_reminder'}
_ALERT_LOOK = re.compile(r'TENANT MESSAGE|TEAM MESSAGE|REMINDER ·|AI MESSAGE|· TYPE ')
FOOTER_MARK = '↩ Reply to this message'
# Test controls as buttons under the alert (a bot does not see plain messages typed in a group, only replies and presses)
REPLAY_ROW = [{'text': '🧪 Next message', 'callback_data': 'sbx|next'}, {'text': '➕ Add to tests', 'callback_data': 'sbx|add test'},
              {'text': '🧪 Stop', 'callback_data': 'sbx|stop'}]
IDLE_ROW = [{'text': '▶️ Continue', 'callback_data': 'sbx|continue'}, {'text': '🔄 Restart', 'callback_data': 'sbx|restart'}]
CONTROL_ROW = [{'text': '🧪 Next test', 'callback_data': 'sbx|next'}, {'text': '🧪 Rerun', 'callback_data': 'sbx|rerun'},
               {'text': '🧪 Stop', 'callback_data': 'sbx|stop'}]


def is_on(value):
    """YAML reads on / off as booleans; the file may also say "on" / "off"."""
    return value is True or str(value).strip().lower() in ('on', 'true', 'yes', '1', 'live')


def button_label(text):
    """'🎫 Create Task 2' -> 'create task 2' (how labels are compared); a done button's '· Andy 09:58' is left out."""
    text = re.sub(r'\s·\s.*$', '', text or '')
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s+\'/,.-]', ' ', text)).strip().lower()


def buttons_of(markup):
    return [button for row in (markup or {}).get('inline_keyboard') or [] for button in row]


class _Response:
    """What requests.post returns, for a Telegram call that was not sent."""
    status_code, content = 200, b'{}'

    def __init__(self, result):
        self._body = {'ok': True, 'result': result}

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


# What each button of a bot answer to a typed reply does, for the tester (button code -> line)
PRESS_GUIDE = {
    'ps': lambda label: ("{label} → the tenant gets it as one more message (see the CRM chat); the button turns into ✅ Message sent."
                         if 'Message' in label else
                         "{label} → this new answer goes to the tenant instead of the first one; the alert above shows ✅ Answer sent."),
    'pk': lambda label: ("{label} → the fact is saved for this apartment (🌍📚 Global = for all apartments; press only one of the two). "
                         "In a test it is saved in the sandbox only."),
    'pr': lambda label: "{label} → the AI follows this rule next time (in a test: sandbox only).",
    'pa': lambda label: "{label} → the change is made for real; the alert above shows the new values.",
}


# The same for the buttons of an alert itself (used for a message typed in the CRM chat, which has no catalog case)
ALERT_GUIDE = {
    'sa': lambda label: "{label} → the answer goes to the tenant (see the CRM chat); the button turns into ✅ Answer sent.",
    'sc': lambda label: "{label} → the answer is sent AND the task is created, in one press.",
    'ea': lambda label: "{label} → the bot asks you for the text; reply to it with the answer the tenant should get.",
    'ct': lambda label: "{label} → the [SANDBOX] task is created in the ClickUp TEST list (also with AI Auto ClickUp OFF).",
    'up': lambda label: "{label} → the update is written on the existing task.",
    'cr': lambda label: "{label} → the reminder is closed; a reminder you leave open will come back as its own alert when due.",
    'ka': lambda label: "{label} → the fact is saved for this apartment (🌍📚 = for all apartments); in a test: sandbox only.",
    'kg': lambda label: "{label} → the fact is saved for all apartments; in a test: sandbox only.",
    'cc': lambda label: "{label} → the record is changed in the CRM (in a test: simulated); the block shows ✅ Done in CRM.",
}


def press_guide(markup, then_next=False, alert=False):
    """The "what to press now" lines for a bot answer to a typed reply (its buttons are proposals) - with alert=True also
    for the buttons of an alert - or ''."""
    guides = {**PRESS_GUIDE, **ALERT_GUIDE} if alert else PRESS_GUIDE
    lines = []
    for button in buttons_of(markup):
        code = (str(button.get('callback_data') or '').split('|') + [''])[1]
        if code in guides and not any(code == done for done, _ in lines):
            label = str(button.get('text') or '')
            lines.append((code, guides[code](label).format(label=label)))
    if not lines:
        return ''
    return "\n".join(["Do this:"] + [f"{n}. {line}" for n, (_, line) in enumerate(lines, 1)]
                     + ([f"{len(lines) + 1}. Press 🧪 Next test."] if then_next else []))


class TelegramTap:
    """Stands in for the `requests` module inside mysite.ai_agent.notify."""

    def __init__(self, world):
        self.world = world
        self.messages, self.order, self.popups = {}, [], []
        self.step = 0
        self.header = None          # test header put on the alerts of the running agent step
        self.notes_hook = None      # (text, markup) -> test notes for the first alert of the step, or None
        self.notes_always = False   # notes also on an alert without buttons (a message typed in the CRM chat)
        self.notes_in = None        # message id that carries the notes
        self.own = False            # the runner's own messages (verdict, summary): not part of the result
        self.want_control = False   # put the test controls under the first alert of this step
        self.interactive = False    # a person steps through the cases (not --auto)
        self.control_step = None    # the step whose alert got the test controls last
        self.control_row = CONTROL_ROW   # the test controls shown now (a real-chat replay has its own)
        self.control_any = False    # this step's first alert gets the controls even when it has no buttons of its own
        self.control = set()        # ids of the messages that carry the test controls right now
        self.busy = set()           # messages whose test button shows ⏳ working… (cleared when the next one is posted)
        self._fake_id = 800_000_000

    def __getattr__(self, name):
        return getattr(requests, name)

    def post(self, url, data=None, **kwargs):
        method = url.rsplit('/', 1)[-1]
        handler = getattr(self, f"_on_{method}", None)
        if handler:
            return handler(url, dict(data or {}), kwargs)
        return _Response(True) if self.world.offline else requests.post(url, data=data, **kwargs)

    def _decorate(self, text, markup):
        if self.own:
            return text
        if not self.header:
            # A bot answer to something the tester typed while the run waits: say what to press now
            guide = press_guide(markup, then_next=self.world.own_bot and self.interactive)
            return f"{text}\n\n🧪 TEST NOTES\n{guide}"[:4096] if guide else text
        notes = None
        # The alert that carries buttons is the one to try things on; a message without buttons that comes with it
        # (an AI MESSAGE about the after-hours text, a part of a long alert) stays as it is
        if self.notes_hook and not self.notes_in and (buttons_of(markup) or self.notes_always):
            notes = self.notes_hook(text, markup)
        if notes:
            cut = text.find(FOOTER_MARK)
            body = (f"{text[:cut].rstrip()}\n{notes}\n———\n\n{text[cut:]}" if cut > 0 else f"{text}\n\n{notes}")
            if len(self.header) + len(body) + 2 <= 4096:
                self.notes_in = 'pending'
                self._last_notes = notes
                return f"{self.header}\n\n{body}"
        guide = press_guide(markup)
        return f"{self.header}\n\n{text}"[:4096] + (f"\n\n🧪 TEST NOTES\n{guide}" if guide else "")

    def _redecorate(self, record, text):
        """An alert written again by the code under test keeps the test header and notes it was posted with."""
        header, notes = record.get('header'), record.get('notes')
        if not header:
            return text
        cut = text.find(FOOTER_MARK)
        body = (f"{text[:cut].rstrip()}\n{notes}\n———\n\n{text[cut:]}" if cut > 0 else f"{text}\n\n{notes}") if notes else text
        return f"{header}\n\n{body}"[:4096]

    def _on_sendMessage(self, url, data, kwargs):
        text = data.get('text') or ''
        markup = json.loads(data['reply_markup']) if data.get('reply_markup') else None
        out = dict(data, text=self._decorate(text, markup))
        took_notes = self.notes_in == 'pending'
        add_control = (self.world.own_bot and self.want_control and not self.own and self.control_step != self.step
                       and (buttons_of(markup) or self.control_any))
        # A bot answer to a reply typed while the run waits: the tester is down here, so the test controls are here too
        add_control = add_control or bool(self.world.own_bot and self.interactive and not self.own and not self.header
                                          and press_guide(markup))
        if add_control:
            out['reply_markup'] = json.dumps(self.with_control(markup))
        if self.world.post_only and markup:
            out.pop('reply_markup', None)
            rows = "\n".join(" ".join(f"[{b.get('text')}]" for b in row) for row in markup.get('inline_keyboard') or [])
            if rows and len(out['text']) + len(rows) + 90 <= 4096:
                out['text'] += f"\n\n{rows}\n(🧪 buttons shown as text: no sandbox bot is set up, they can not be pressed)"
        if self.world.offline:
            self._fake_id += 1
            response = _Response({'message_id': self._fake_id})
        else:
            response = requests.post(url, data=out, **kwargs)
        try:
            message_id = (response.json().get('result') or {}).get('message_id')
        except Exception:
            message_id = None
        if took_notes:
            self.notes_in = message_id
        if add_control and message_id:
            self.clear_busy()
            self.control.add(message_id)
            self.control_step = self.step
        if message_id and not self.world.offline:
            self.world.remember_posted(data.get('chat_id'), message_id)
        if message_id:
            self.messages[message_id] = {
                'header': None if self.own else self.header, 'notes': getattr(self, '_last_notes', None) if took_notes else None,
                'id': message_id, 'text': text, 'posted': out['text'], 'markup': markup, 'step': self.step, 'own': self.own,
                'silent': str(data.get('disable_notification', '')).lower() in ('true', '1'),
                'reply_to': int(data['reply_to_message_id']) if data.get('reply_to_message_id') else None,
            }
            self.order.append(message_id)
        return response

    def _record_edit(self, data):
        record = self.messages.get(int(data.get('message_id') or 0))
        if record is not None:
            if 'text' in data:
                record['text'] = data['text']
            if 'reply_markup' in data or 'text' not in data:
                record['markup'] = json.loads(data['reply_markup']) if data.get('reply_markup') else None
            record['edited_in'] = self.step
        return record

    def with_control(self, markup, record=None):
        row = (record or {}).get('busy_row') or self.control_row
        return {'inline_keyboard': list((markup or {}).get('inline_keyboard') or []) + [row]}

    def show_busy(self, message_id, data):
        """A pressed test button becomes "⏳ Next test · working…" until the next alert is up: the press was taken."""
        from mysite.ai_agent import notify
        record = self.messages.get(message_id)
        if record is None or message_id not in self.control:
            return
        rows = self.with_control(record.get('markup'), record)['inline_keyboard']
        label = next((b.get('text') for row in rows for b in row if b.get('callback_data') == data), data.split('|')[-1])
        record['busy_row'] = [{'text': f"⏳ {label.lstrip('🧪▶️🔄➕ ').strip()} · working…", 'callback_data': 'sbx|busy'}]
        notify.edit_reply_markup(message_id, record.get('markup'))

    def drop_control(self):
        """The case is over: the test controls leave its alert (the alert's own buttons stay). A ⏳ working… row stays
        until something new is posted (clear_busy), so the person sees that the press was taken."""
        from mysite.ai_agent import notify
        ids, self.control = list(self.control), set()
        for message_id in ids:
            record = self.messages.get(message_id) or {}
            if record.get('busy_row'):
                self.busy.add(message_id)
                continue
            notify.edit_reply_markup(message_id, None if record.get('own') else record.get('markup'))

    def clear_busy(self):
        from mysite.ai_agent import notify
        ids, self.busy = list(self.busy), set()
        for message_id in ids:
            record = self.messages.get(message_id) or {}
            record.pop('busy_row', None)
            if message_id not in self.control:
                notify.edit_reply_markup(message_id, None if record.get('own') else record.get('markup'))

    def _on_editMessageReplyMarkup(self, url, data, kwargs):
        record = self._record_edit(data)
        if self.world.offline or self.world.post_only:   # post-only: the real message has no buttons to change
            return _Response(True)
        if record is not None and (record['id'] in self.control or record['id'] in self.busy):
            data = dict(data, reply_markup=json.dumps(self.with_control(record['markup'], record)))
        return requests.post(url, data=data, **kwargs)

    def _on_editMessageText(self, url, data, kwargs):
        record = self._record_edit(data)
        if self.world.offline:
            return _Response(True)
        if record is not None:
            data = dict(data, text=self._redecorate(record, data.get('text') or ''))
            if record['id'] in self.control or record['id'] in self.busy:
                data['reply_markup'] = json.dumps(self.with_control(record['markup'], record))
        if self.world.post_only:
            data.pop('reply_markup', None)
        return requests.post(url, data=data, **kwargs)

    def _on_answerCallbackQuery(self, url, data, kwargs):
        self.popups.append({'text': data.get('text') or '', 'step': self.step})
        if self.world.offline or str(data.get('callback_query_id')).startswith('sbx-'):
            return _Response(True)
        return requests.post(url, data=data, **kwargs)

    # -- for the runner -------------------------------------------------------------------------------------------
    def in_step(self, step):
        return [self.messages[i] for i in self.order if self.messages[i]['step'] == step and not self.messages[i]['own']]

    def find_button(self, label, message_ids=None):
        """(message record, button) of the newest message carrying that button; exact label first, then a part of it."""
        wanted = button_label(label)
        records = [self.messages[i] for i in reversed(self.order) if message_ids is None or i in message_ids]
        for exact in (True, False):
            for record in records:
                for button in buttons_of(record['markup']):
                    have = button_label(button.get('text'))
                    if have == wanted or (not exact and wanted in have):
                        return record, button
        return None, None


class World:
    def __init__(self, clock, offline=False, log=print):
        self.clock, self.offline, self.log = clock, offline, log
        self.tap = TelegramTap(self)
        self.own_bot = self.post_only = False
        self.mode, self.clickup_on = 'test', False
        self.sms_fail = self.clickup_fail = 0
        self.call_answered = True
        self.typed = []                 # messages a person typed in the sandbox chat on the site, waiting for the runner
        self.contract = None            # the story's contract (story.yaml), shown by the agent's get_contract tool
        self.conversation2 = None
        self.real_context = None        # (apartment, booking) of a replayed real chat: read only
        self.sent, self.held, self.clickup_log, self.calls = [], [], [], []
        self.fake_tasks, self.reminders, self.issues = {}, {}, {}
        self.state = {'knowledge': [], 'rules': []}
        self.shift = timedelta(0)
        self._undo, self._fake_updates, self._orig = [], [], {}
        self.commands = []              # test commands read from the sandbox bot
        self.staff_aliases = {}         # sandbox sender id -> team member name, for people without a phone in AI staff
        self._fake_seq = 0

    # -- start ------------------------------------------------------------------------------------------------------
    def configure_telegram(self):
        """Decides where the alerts go. Changes the environment of this process only."""
        bot = (os.environ.get('AI_AGENT_SANDBOX_BOT_TOKEN') or '').strip()
        chat = (os.environ.get('AI_AGENT_SANDBOX_CHAT_ID') or '').strip()
        if self.offline:
            os.environ['TELEGRAM_TOKEN'] = 'offline'   # no real bot token in this process at all
            os.environ['AI_AGENT_ALERT_CHAT_ID'] = chat or os.environ.get('AI_AGENT_ALERT_CHAT_ID') or '-1'
            return "offline: nothing is posted to Telegram, the alerts are printed here"
        if bot and not chat:
            raise CommandError("AI_AGENT_SANDBOX_BOT_TOKEN is set but AI_AGENT_SANDBOX_CHAT_ID is not")
        if chat:
            moved = self._moved_chat(bot or os.environ.get('TELEGRAM_TOKEN'), chat)
            if moved:
                # Telegram gave the group a new id (it became a supergroup, e.g. when a bot was made admin). Posts would
                # still arrive, but every button press would be refused as "Unknown button": use the new id.
                self.log(f"NOTE: the test group {chat} is now {moved} - using the new id; set AI_AGENT_SANDBOX_CHAT_ID={moved} in .env")
                chat = moved
            os.environ['AI_AGENT_ALERT_CHAT_ID'] = chat
            os.environ['TELEGRAM_ERROR_CHAT_ID'] = ''   # a failed post is not copied to the production error chat
        if bot:
            os.environ['TELEGRAM_TOKEN'] = bot
            self.own_bot = True
            return f"Telegram: sandbox bot in chat {chat} - buttons and replies work, handled by THIS process"
        self.post_only = True
        from mysite.ai_agent import notify
        where = f"the test group {chat}" if chat else f"the team's AI group {notify.ai_chat_id()} (no test group set)"
        return (f"Telegram: main bot in {where} - VIEW ONLY: buttons are shown as text (set AI_AGENT_SANDBOX_BOT_TOKEN "
                f"+ AI_AGENT_SANDBOX_CHAT_ID for buttons and replies)")

    def remember_posted(self, chat_id, message_id, theirs=False):
        """theirs: a message a person wrote in the test group (the sandbox bot deletes it when it is an admin there)."""
        try:
            catalog.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            with POSTED_FILE.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'chat': str(chat_id), 'id': message_id, 'own_bot': self.own_bot, 'theirs': theirs}) + "\n")
        except OSError:
            pass

    def clean_chat(self, main_token):
        """Deletes from Telegram what earlier test runs posted (each message by the bot that posted it) and what people
        wrote in the test group during them (only possible while the sandbox bot is an admin of the group with the
        "Delete messages" right). Returns how many were deleted; self.kept_theirs = people's messages it could not delete."""
        self.kept_theirs = 0
        try:
            rows = [json.loads(line) for line in POSTED_FILE.read_text(encoding='utf-8').splitlines() if line.strip()]
        except OSError:
            return 0
        deleted = 0
        for row in rows:
            token = (os.environ.get('AI_AGENT_SANDBOX_BOT_TOKEN') or '').strip() if row.get('own_bot') else main_token
            if not token or self.offline:
                continue
            try:
                body = requests.post(f"https://api.telegram.org/bot{token}/deleteMessage",
                                     data={'chat_id': row['chat'], 'message_id': row['id']}, timeout=10).json()
                deleted += bool(body.get('ok'))
                self.kept_theirs += bool(row.get('theirs') and not body.get('ok'))
            except Exception:
                pass
        POSTED_FILE.unlink(missing_ok=True)
        return deleted

    @staticmethod
    def _moved_chat(token, chat):
        """The new id of a group that Telegram upgraded to a supergroup, or None."""
        try:
            body = requests.post(f"https://api.telegram.org/bot{token}/sendChatAction", data={'chat_id': chat, 'action': 'typing'},
                                 timeout=10).json()
            new_id = (body.get('parameters') or {}).get('migrate_to_chat_id')
            return str(new_id) if new_id and str(new_id) != str(chat) else None
        except Exception:
            return None

    def _patch(self, owner, name, value):
        self._orig[f"{owner.__name__}.{name}"] = getattr(owner, name)
        self._undo.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def install(self):
        from mysite.ai_agent import answer_review, calls, clickup, config, inputs, kb_documents, notify, prompt_library, prompts
        from mysite.views import messaging

        self.ensure()
        staff_names = inputs._staff_names
        self._patch(inputs, '_staff_names', lambda: {**staff_names(), **self._sandbox_staff()})
        self._patch(notify, 'requests', self.tap)
        self._patch(messaging, '_should_send_ai_to_group',
                    lambda apartment: bool(apartment) and apartment.id == self.apartment.id and self.mode == 'live')
        self._patch(messaging, 'send_messsage_by_sid', self._send)
        self._patch(messaging, 'send_tenant_sms_gated', self._send_gated)
        # In the runner the sandbox chat plays a real tenant chat (SMS buttons); its sends are caught here, and a
        # 📝 Send Answer (CRM) press lands in the sandbox chat like any send
        self._patch(messaging, 'is_crm_only_chat', lambda conversation_sid: False)
        self._patch(messaging, 'write_to_crm_chat', lambda conversation_sid, author, message: self._send(conversation_sid, author, message))
        self._patch(messaging, 'get_twilio_client', self._no_twilio)
        global_kb = messaging.get_global_knowledge_base_text
        self._patch(messaging, 'get_global_knowledge_base_text', lambda: self._global_kb(global_kb()))
        context = messaging.build_full_context
        # A replayed real chat is read with ITS apartment and booking; the story chat uses the real builder as it is
        self._patch(messaging, 'build_full_context', lambda sid, apartment, booking, *a, **k: context(
            sid, *(self.real_context or (apartment, booking)), *a, **k))
        self._patch(calls, '_dial', self._dial)
        self._patch(clickup, 'writes_enabled', lambda apartment=None: self.clickup_on or clickup.press_active())
        self._patch(config, 'clickup_writes_enabled', lambda: self.clickup_on)
        create_task = clickup.create_task
        self._patch(clickup, 'create_task', lambda list_id, name, *a, **k: self._create_task(create_task, list_id, name, *a, **k))
        for name in ('set_task_closed', 'add_task_comment', 'update_task', 'delete_task', 'get_task_state'):
            self._patch(clickup, name, self._task_op(name, getattr(clickup, name)))
        # A simulated "live" case must not assign its [SANDBOX] task to the real team (they would be notified by ClickUp):
        # always the test assignees, as for a test apartment
        from mysite.ai_agent import team_notify
        assignees = team_notify.task_assignees
        self._patch(team_notify, 'task_assignees', lambda is_test, responsible=(): assignees(True, responsible))
        self._patch(kb_documents, 'merge', self._kb_merge)
        self._patch(kb_documents, 'save_document', self._kb_save)
        self._patch(prompt_library, 'upsert_lesson', self._lesson)
        self._patch(prompt_library, 'upsert_team_rule', lambda apartment, key, rule: self._lesson(apartment, key, rule, kind='team'))
        system_prompt = prompts.get_system_prompt
        self._patch(prompts, 'get_system_prompt', lambda apartment=None: self._system_prompt(system_prompt(apartment)))
        fetch, write_offset = answer_review.fetch_updates, answer_review._write_offset
        self._patch(answer_review, 'fetch_updates', lambda: self._updates(fetch))
        self._patch(answer_review, '_write_offset', lambda value: None if value > FAKE_UPDATE_BASE else write_offset(value))
        try:
            self.state.update(json.loads(STATE_FILE.read_text(encoding='utf-8')))
        except Exception:
            pass

    def uninstall(self):
        for owner, name, value in reversed(self._undo):
            setattr(owner, name, value)
        self._undo = []

    def ensure(self):
        from mysite.ai_agent.sandbox_test import story
        from mysite.models import Booking, TwilioConversation, User
        story.ensure_apartment()
        call_command('ai_agent_sandbox', apartment=story.STORY_APARTMENT, stdout=io.StringIO())
        self.apartment = story.ensure_apartment()
        if 'test' not in (self.apartment.name or '').lower():
            raise CommandError("the sandbox chat is not on a test apartment - refusing to run")
        # Offline runs give the alerts made-up Telegram message ids: they must not repeat those of the runs an earlier
        # process left in the sandbox (a reply would reach the wrong run)
        from django.db.models import Max
        from mysite.models import AIRun
        used = AIRun.objects.filter(conversation_sid__in=SANDBOX_SIDS, telegram_message_id__gte=self.tap._fake_id).aggregate(m=Max('telegram_message_id'))['m']
        if used:
            self.tap._fake_id = used
        # The sandbox chat and booking may still point at the apartment of earlier runs (Test_Apart2): the story owns them now
        TwilioConversation.objects.filter(conversation_sid=SANDBOX_SID).update(apartment=self.apartment)
        Booking.objects.filter(notes=SANDBOX_NOTE).update(apartment=self.apartment)
        self.conversation = TwilioConversation.objects.select_related('apartment').get(conversation_sid=SANDBOX_SID)
        self.booking = Booking.objects.get(notes=SANDBOX_NOTE)
        self.tenant = User.objects.get(email=SANDBOX_TENANT_EMAIL)

    # -- isolation ----------------------------------------------------------------------------------------------------
    def _send(self, conversation_sid, author, message, sender_phone=None, receiver_phone=None):
        from mysite.models import TwilioMessage
        if conversation_sid not in SANDBOX_SIDS:
            raise RuntimeError(f"sandbox test runner: refused to send a message to the real chat {conversation_sid}")
        if self.sms_fail > 0:
            self.sms_fail -= 1
            raise Exception("Error sending message via Twilio: 30008 unknown error (simulated by the sandbox test)")
        TwilioMessage.objects.bulk_create([TwilioMessage(
            message_sid=f"SBX-OUT-{uuid.uuid4().hex[:20]}",
            conversation=self.conversation2 if conversation_sid == SANDBOX_SID_2 and self.conversation2 else self.conversation,
            conversation_sid=conversation_sid,
            author='Virtual Assistant', body=message, direction='outbound', message_timestamp=self.clock.now(),
        )])
        self.sent.append({'text': message, 'at': self.clock.now(), 'step': self.tap.step})
        return True

    def _send_gated(self, conversation_sid, author, message, sender_phone=None, receiver_phone=None):
        from mysite.ai_agent import config
        if config.is_within_notification_window():
            self._send(conversation_sid, author, message, sender_phone, receiver_phone)
            return True
        # Held for 08:00 like the real thing, but kept here: the live flush job must never find a sandbox row
        self.held.append({'text': message, 'until': config.next_notification_window_start(), 'step': self.tap.step})
        return False

    def flush_held(self):
        """What the live flush job does at 08:00: the texts held for the SMS window go out once the (case) clock has
        passed their time. Returns how many were sent."""
        now, sent = self.clock.now(), 0
        for item in list(self.held):
            if item['until'] <= now:
                self.held.remove(item)
                self._send(SANDBOX_SID, 'Virtual Assistant', item['text'])
                sent += 1
        if sent:
            self.log(f"  [sandbox] {sent} held message(s) sent to the tenant at {item['until']:%a %d %b %H:%M} (the SMS window opened)")
        return sent

    def _no_twilio(self):
        raise RuntimeError("sandbox test runner: Twilio is switched off in this process")

    def _dial(self, call):
        from mysite.ai_agent import calls
        from mysite.models import AIAlertCall
        call.status = AIAlertCall.STATUS_ANSWERED if self.call_answered else AIAlertCall.STATUS_UNANSWERED
        call.note = "sandbox: call simulated - " + ("answered" if self.call_answered else "nobody answered")
        call.save()
        self.calls.append({'staff': call.staff_name, 'answered': self.call_answered, 'step': self.tap.step})
        if not self.call_answered:
            calls.send_ai_chat(f"📞 {call.staff_name} did NOT answer ({len(call.phones)} number(s) tried, simulated) about "
                               f"{calls._unit(call.conversation_sid)}: {call.reason}")

    def _global_kb(self, text):
        """The real global knowledge base plus what a test saved as global (sandbox-only: the real page is never written)."""
        extra = [k['text'] for k in self.state['knowledge'] if k.get('scope') == 'global']
        return text + ("\n" + "\n".join(extra) if extra else "")

    def _clickup_fail(self):
        from mysite.ai_agent import clickup
        if self.clickup_fail > 0:
            self.clickup_fail -= 1
            raise clickup.ClickUpError("ClickUp error 500 (simulated by the sandbox test)")

    def _create_task(self, original, list_id, name, *args, **kwargs):
        from mysite.ai_agent import clickup
        self._clickup_fail()
        if not clickup._is_test_list(list_id):
            raise clickup.ClickUpError(f"sandbox test runner: refused to create a task outside the TEST list ({list_id})")
        task_id, url = original(list_id, f"[SANDBOX] {name}", *args, **kwargs)
        self.clickup_log.append({'op': 'create', 'name': name, 'ref': url or task_id, 'step': self.tap.step})
        return task_id, url

    def _task_op(self, name, original):
        from mysite.ai_agent import clickup
        from mysite.models import AIIssue

        def run(ticket_ref, *args, **kwargs):
            if name != 'get_task_state':
                self._clickup_fail()
                self.clickup_log.append({'op': name, 'ref': ticket_ref, 'args': [str(a)[:300] for a in args],
                                         'step': self.tap.step})
            task = self.fake_tasks.get(ticket_ref)
            if task is None:
                if name != 'get_task_state' and not AIIssue.objects.filter(conversation_sid=SANDBOX_SID, ticket_ref=ticket_ref).exists():
                    raise clickup.ClickUpError("sandbox test runner: refused to change a ClickUp task that is not a sandbox task")
                return original(ticket_ref, *args, **kwargs)
            if name == 'set_task_closed':
                task['closed'] = kwargs.get('closed', args[0] if args else True)
                return 'complete' if task['closed'] else 'to do'
            if name == 'add_task_comment':
                task['comments'].append({'when': f"{self.clock.local():%Y-%m-%d %H:%M}", 'user': 'AI', 'text': str(args[0] if args else kwargs.get('text'))})
            elif name == 'update_task':
                task.update({k: v for k, v in zip(('name', 'description', 'priority'), args) if v is not None})
                task.update({k: v for k, v in kwargs.items() if v is not None})
            elif name == 'delete_task':
                self.fake_tasks.pop(ticket_ref, None)
            elif name == 'get_task_state':
                return {'status': 'complete' if task['closed'] else 'to do', 'closed': bool(task['closed']),
                        'assignees': [task['owner']] if task.get('owner') else [], 'updated': task.get('updated') or '',
                        'due': None, 'url': ticket_ref, 'comments': list(task['comments'])}
            return None
        return run

    def _save_state(self):
        catalog.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding='utf-8')

    def _kb_merge(self, scope, apartment, text, replaces='', source=''):
        """Apartment knowledge is written to the story apartment's real page (nothing else uses that apartment);
        global knowledge stays sandbox-only: the company page is shared with every real chat."""
        self.state['knowledge'].append({'scope': 'global' if scope == 'company' else 'apartment', 'text': text,
                                        'replaces': replaces, 'source': source, 'step': self.tap.step})
        self._save_state()
        if scope != 'company' and apartment is not None and apartment.id == self.apartment.id:
            return self._orig['mysite.ai_agent.kb_documents.merge'](scope, apartment, text, replaces=replaces, source=source)
        if scope != 'company':
            raise RuntimeError(f"sandbox test runner: refused to write knowledge of another apartment ({getattr(apartment, 'name', apartment)})")
        return "saved as SANDBOX-ONLY global knowledge (the real company page is not changed)", ''

    def _kb_save(self, scope, text, apartment=None, source=None):
        if scope != 'company' and apartment is not None and apartment.id == self.apartment.id:
            return self._orig['mysite.ai_agent.kb_documents.save_document'](scope, text, apartment=apartment, source=source)
        self.state['knowledge'].append({'scope': 'global', 'text': text, 'replaces': '', 'source': source or '', 'step': self.tap.step})
        self._save_state()

    def _lesson(self, apartment, key, rule, prompt_key=None, kind='lesson'):
        self.state['rules'].append({'key': key, 'rule': rule, 'kind': kind, 'step': self.tap.step,
                                    'scope': 'apartment' if apartment is not None else 'company'})
        self._save_state()
        return f"'{key}' saved as a SANDBOX-ONLY rule (the real prompts are not changed)"

    def _system_prompt(self, result):
        text, source = result
        lessons = [r for r in self.state['rules'] if r.get('kind', 'lesson') != 'team']
        team = [r for r in self.state['rules'] if r.get('kind') == 'team']
        if lessons:
            text += "\n\nRules saved by the team (sandbox):\n" + "\n".join(f"- {r['rule']}" for r in lessons)
        if team:
            from mysite.ai_agent import prompts
            text += "\n\n" + prompts.TEAM_RULES_HEADER + " (sandbox)\n" + "\n".join(f"- [{r['scope']}] {r['key']}: {r['rule']}" for r in team)
        return text, source

    # -- Telegram updates -------------------------------------------------------------------------------------------
    def _updates(self, fetch):
        """What answer_review.poll_telegram() reads: our simulated replies / presses, plus (own bot) the real ones."""
        from mysite.ai_agent import answer_review, notify
        updates, self._fake_updates = self._fake_updates, []
        if not self.own_bot:
            return updates
        for update in fetch():
            message = update.get('message') or {}
            text = (message.get('text') or '').strip()
            in_chat = str((message.get('chat') or {}).get('id')) == str(notify.ai_chat_id())
            callback = update.get('callback_query') or {}
            if in_chat and message.get('message_id'):
                # What a person writes in the test group is remembered too: a restart cleans the whole chat
                self.remember_posted(notify.ai_chat_id(), message['message_id'], theirs=True)
            if in_chat and parse_command(text):
                self.commands.append({'text': text, 'by': answer_review._author(message.get('from') or {}),
                                      'message_id': message.get('message_id')})
            elif (callback.get('data') or '') == 'sbx|busy':
                notify.answer_callback(callback.get('id'), "⏳ Still working on it")
            elif (callback.get('data') or '').startswith('sbx|'):
                text = callback['data'].split('|')[1]   # a test control: next / rerun / stop / apply
                self.commands.append({'text': text, 'by': answer_review._author(callback.get('from') or {}),
                                      'callback_id': callback.get('id')})
                notify.answer_callback(callback.get('id'), f"🧪 {text}")
                pressed = (callback.get('message') or {}).get('message_id')
                if pressed:
                    self.tap.show_busy(pressed, callback['data'])
            else:
                # Kept in the run's log, so nothing a person writes or presses in the test group is ever lost
                if text:
                    parent = (message.get('reply_to_message') or {}).get('message_id')
                    self.log(f"  [telegram] {answer_review._author(message.get('from') or {})} replied to message {parent}: {text}")
                elif callback:
                    self.log(f"  [telegram] {answer_review._author(callback.get('from') or {})} pressed {callback.get('data')}")
                updates.append(update)
                continue
            self.log(f"  [telegram] command: {text or 'apply'}")
            answer_review._write_offset(update['update_id'] + 1)
        return updates

    def _fake_update(self, body):
        self._fake_seq += 1
        self._fake_updates.append({'update_id': FAKE_UPDATE_BASE + self._fake_seq, **body})

    def simulate_reply(self, text, by, to_message_id):
        from mysite.ai_agent import notify
        self._fake_update({'message': {
            'message_id': None, 'chat': {'id': notify.ai_chat_id()}, 'text': text, 'date': int(self.clock.now().timestamp()),
            'from': {'id': 1, 'first_name': by}, 'reply_to_message': {'message_id': to_message_id, 'from': {'is_bot': True}},
        }})

    def simulate_press(self, record, button, by):
        from mysite.ai_agent import notify
        self._fake_update({'callback_query': {
            'id': f"sbx-{uuid.uuid4().hex[:8]}", 'from': {'id': 1, 'first_name': by}, 'data': button.get('callback_data'),
            'message': {'message_id': record['id'], 'chat': {'id': notify.ai_chat_id()}},
        }})

    # -- clean start: only at the start of the story -------------------------------------------------------------------
    def clean(self, story_block, shift):
        """Wipes the sandbox and writes the story's rows (11.2 step 1). Returns a short text of what was done."""
        from mysite.ai_agent import clickup
        from mysite.ai_agent.sandbox_test import story
        from mysite.models import AIIssue

        closed = 0
        for issue in AIIssue.objects.filter(conversation_sid=SANDBOX_SID).exclude(ticket_ref__isnull=True).exclude(ticket_ref=''):
            if issue.ticket_ref.startswith(FAKE_TASK) or clickup.delivery_mode() != 'api':
                continue
            try:
                self._orig['mysite.ai_agent.clickup.set_task_closed'](issue.ticket_ref)
                closed += 1
            except Exception as e:
                self.log(f"  could not close the sandbox ClickUp task {issue.ticket_ref}: {str(e)[:120]}")
        counts = story.wipe(self)
        self.conversation2 = None
        self.state = {'knowledge': [], 'rules': []}
        self._save_state()
        self.reset_memory()
        seeded = story.seed(self, story_block or {}, shift)
        story.clear_snapshots()
        return (", ".join(f"{n} {k}" for k, n in counts.items() if n) or "already empty") \
            + (f", {closed} ClickUp task(s) closed" if closed else "") + f"; seeded: {seeded}"

    def reset_memory(self):
        """What the world remembers of earlier steps (not the database)."""
        self.sent, self.held, self.clickup_log, self.calls = [], [], [], []
        self.fake_tasks, self.reminders, self.issues = {}, {}, {}
        self.real_context = None
        self.sms_fail = self.clickup_fail = 0
        self.call_answered = True
        self.mode, self.clickup_on = 'test', False

    # -- messages a person types in the sandbox chat on the site while a run is alive -----------------------------------
    def take_typed(self):
        """
        A person may write in the sandbox chat on the site (the 💬 CRM chat link) during a test. The site queues such
        a message for the LIVE worker (it waits 5 s), which would answer with its own card in the team's real AI
        group. Here it is taken first: the queued event and the stored row are removed and the text is kept in
        self.typed - the runner handles it as a step of the running case. Returns how many were taken.
        """
        from mysite.models import AIEvent, TwilioMessage
        taken = 0
        pending = AIEvent.objects.filter(conversation_sid__in=SANDBOX_SIDS, status=AIEvent.STATUS_PENDING)
        for event in [e for e in pending.order_by('id') if (e.payload or {}).get('source') == 'chat_ui']:
            if not AIEvent.objects.filter(id=event.id, status=AIEvent.STATUS_PENDING).update(
                    status=AIEvent.STATUS_RUNNING, started_at=real_now()):
                continue   # the live worker was faster
            self.typed.append({'kind': 'tenant' if event.event_type == AIEvent.TYPE_TENANT_MESSAGE else 'team',
                               'text': event.body or '', 'by': event.created_by})
            AIEvent.objects.filter(id=event.id).delete()
            if event.message_id:
                TwilioMessage.objects.filter(id=event.message_id, conversation_sid__in=SANDBOX_SIDS).delete()
            taken += 1
        return taken

    def start_chat_watch(self):
        """Looks for typed messages every second, also while the runner is busy with the AI (the live worker waits 5 s)."""
        self._watch_stop = threading.Event()

        def watch():
            from django.db import connection
            try:
                while not self._watch_stop.wait(1.0):
                    try:
                        self.take_typed()
                    except Exception as e:
                        self.log(f"  [chat watch] {type(e).__name__}: {str(e)[:200]}")
            finally:
                connection.close()
        threading.Thread(target=watch, name='sandbox-chat-watch', daemon=True).start()

    def stop_chat_watch(self):
        if getattr(self, '_watch_stop', None):
            self._watch_stop.set()

    def leave_nothing_behind(self, note='sandbox test finished'):
        """End of a run (also after Ctrl+C): the live worker shares the database, so nothing of the test may be left
        that it would act on later - a pending reminder, an unfinished event, a failed send waiting for its retry."""
        from mysite.models import AIEvent, AIFollowUp, AIRun
        AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status=AIFollowUp.STATUS_PENDING).update(
            status=AIFollowUp.STATUS_CANCELLED, status_note=note)
        AIEvent.objects.filter(conversation_sid=SANDBOX_SID, status__in=(AIEvent.STATUS_PENDING, AIEvent.STATUS_RUNNING)).update(
            status=AIEvent.STATUS_SKIPPED, error=note)
        runs = AIRun.objects.filter(conversation_sid=SANDBOX_SID)
        runs.filter(hold_status=AIRun.HOLD_FAILED).update(hold_status=AIRun.HOLD_CANCELLED, delivery_note=note)
        runs.filter(mode=AIRun.MODE_LIVE).update(mode=AIRun.MODE_TEST)

    def seal_runs(self):
        """View-only mode, after every case: a "live" sandbox run is stored as test, so nothing the live worker later
        does with it (someone replies to the alert in the team's group) can try a real send."""
        from mysite.models import AIRun
        if self.post_only:
            AIRun.objects.filter(conversation_sid=SANDBOX_SID, mode=AIRun.MODE_LIVE).update(mode=AIRun.MODE_TEST)

    # -- set-up ---------------------------------------------------------------------------------------------------------
    def moved(self, value):
        """A time written in a case -> the moment it has in this run (the case dates are moved by whole weeks)."""
        return catalog.parse_time(value) + self.shift

    def _sandbox_staff(self):
        """Team members the story wrote under a sandbox-only sender id ("SBX-STAFF:Kevin": no phone in AI staff): known
        from the chat rows themselves, so a run started later (restore, new process) still shows them as the team."""
        from mysite.models import TwilioMessage
        found = {a: a.split(':', 1)[1] for a in TwilioMessage.objects.filter(conversation_sid__in=SANDBOX_SIDS, author__startswith='SBX-STAFF:')
                 .values_list('author', flat=True).distinct()}
        return {**found, **self.staff_aliases}

    def _staff_author(self, name):
        from mysite.models import StaffMember
        member = StaffMember.objects.filter(ai_name__iexact=str(name or '').strip(), is_active=True).first()
        if member and member.phones:
            return member.phones[0]
        if member:
            # In AI staff without a phone (the chat knows people by phone): a sandbox-only sender id with that name
            self.staff_aliases[f"SBX-STAFF:{member.ai_name}"] = member.ai_name
            return f"SBX-STAFF:{member.ai_name}"
        self.log(f"  note: {name!r} is not in AI staff - the message is shown as 'Manager (CRM)'")
        return 'ASSISTANT'

    def _second_chat(self):
        """The sandbox tenant's second chat (same booking), as when a chat is created twice by mistake."""
        from mysite.models import TwilioConversation
        if not self.conversation2:
            if not TwilioConversation.objects.filter(conversation_sid=SANDBOX_SID_2).exists():
                TwilioConversation.objects.bulk_create([TwilioConversation(
                    conversation_sid=SANDBOX_SID_2, friendly_name="AI SANDBOX 2 (second chat of the same tenant, database only)",
                    apartment=self.apartment, booking=self.booking, notes=SANDBOX_NOTE)])
            self.conversation2 = TwilioConversation.objects.get(conversation_sid=SANDBOX_SID_2)
        return self.conversation2

    def add_message(self, message, at, author=None):
        """Stores one chat message in the sandbox chat (no Twilio, no model side effects). Returns the row.
        `chat: 2` on a message puts it into the tenant's second chat."""
        from mysite.models import TwilioMessage
        second = str(message.get('chat') or '') == '2'
        conversation = self._second_chat() if second else self.conversation
        role = message.get('from') or 'tenant'
        author = author or {'tenant': SANDBOX_TENANT_PHONE, 'ai': 'Virtual Assistant'}.get(role) or self._staff_author(message.get('name'))
        sid = f"SBX-{uuid.uuid4().hex[:24]}"
        TwilioMessage.objects.bulk_create([TwilioMessage(
            message_sid=sid, conversation=conversation, conversation_sid=conversation.conversation_sid, author=author[:50],
            body=str(message.get('text') or ''), direction='inbound' if role == 'tenant' else 'outbound',
        )])
        TwilioMessage.objects.filter(message_sid=sid).update(message_timestamp=at, created_at=at)
        return TwilioMessage.objects.get(message_sid=sid)

    def setup(self, case, now):
        """The switches of a chapter and its changes of the real CRM rows (`crm:`), before its trigger."""
        from mysite.ai_agent.sandbox_test import story
        setup = case.get('setup') or {}
        self.mode = 'live' if str(case.get('mode') or 'test').lower() == 'live' else 'test'
        self.clickup_on = is_on(case.get('clickup', False))
        self.sms_fail, self.clickup_fail = int(setup.get('sms_fail') or 0), int(setup.get('clickup_fail') or 0)
        self.call_answered = setup.get('call_answered', True) is not False
        return story.apply_crm(self, case.get('crm'), self.shift)

    def team_task(self, step):
        """A team member works on a sandbox task directly in ClickUp, outside the alerts: {close_task: "sink"} closes it
        (C6), {comment_task: "sink", text: "Plumber booked Mon 9-11am"} comments on it (C5) - the real [SANDBOX] task
        in the TEST list, found by a text of its title. Returns a note for the step."""
        from mysite.ai_agent import clickup
        from mysite.models import AIIssue
        closing = 'close_task' in step
        text = str(step.get('close_task') if closing else step.get('comment_task') or '').strip().lower()
        issue = next((i for i in AIIssue.objects.filter(conversation_sid=SANDBOX_SID, ticket_ref__isnull=False).order_by('-id')
                      if text and text in f"{i.ticket_title or ''} {i.summary}".lower()), None)
        if not issue:
            return f"no sandbox task with \"{text}\" in its title"
        with clickup.pressed():
            if closing:
                clickup.set_task_closed(issue.ticket_ref, True)
            else:
                clickup.add_task_comment(issue.ticket_ref, str(step.get('text') or ''))
        self.log(f"  [sandbox] the team {'closed' if closing else 'commented on'} the task \"{issue.ticket_title or issue.summary}\" in ClickUp")
        return None

    # -- triggers -------------------------------------------------------------------------------------------------------
    def queue_burst(self, messages):
        """
        Stores the new message(s) and queues them through the real enqueue functions, claimed in the same transaction:
        the live worker (same database) never sees them pending. Returns (events, note when nothing was queued).
        """
        from mysite.ai_agent import after_hours, service
        from mysite.models import AIEvent

        with transaction.atomic():
            for index, message in enumerate(messages):
                at = message.get('moment') or (self.moved(message['at']) if message.get('at')
                                               else self.clock.now() + timedelta(seconds=index))
                row = self.add_message(message, at, author=message.get('author'))
                if (message.get('from') or 'tenant') == 'tenant':
                    service.enqueue_tenant_message(SANDBOX_SID, row.message_sid, row.body, send_allowed=True, source='sandbox_test')
                else:
                    service.enqueue_staff_message(SANDBOX_SID, row.message_sid, row.body, source='sandbox_test')
            events = list(AIEvent.objects.filter(conversation_sid=SANDBOX_SID, status=AIEvent.STATUS_PENDING).order_by('id'))
            AIEvent.objects.filter(id__in=[e.id for e in events]).update(
                status=AIEvent.STATUS_RUNNING, started_at=real_now(), attempts=1)
            for event in events:
                event.status = AIEvent.STATUS_RUNNING
                if event.event_type == AIEvent.TYPE_TENANT_MESSAGE:
                    # The after-hours decision is made here too, or the live worker would make it (with its own clock)
                    after_hours.handle_event(event, self.clock.now())
        return events, None if events else "no AI run: the message was skipped before the queue (short acknowledgment)"

    def queue_notification(self, kind, text):
        """An automatic notification becomes due, as the 08:00 job would hand it over. Returns (events, note)."""
        from mysite.ai_agent import service
        from mysite.management.commands.sms_notifications import Command as NotificationJob
        from mysite.models import AIEvent
        text = text or NotificationJob().get_message_for_event(kind)
        if not text:
            return [], f"the template '{kind}' is switched off - nothing is sent and the AI is not asked"
        with transaction.atomic():
            service.enqueue_notification(SANDBOX_SID, kind, text, self.booking.id, source='sandbox_test')
            events = list(AIEvent.objects.filter(conversation_sid=SANDBOX_SID, status=AIEvent.STATUS_PENDING).order_by('id'))
            AIEvent.objects.filter(id__in=[e.id for e in events]).update(status=AIEvent.STATUS_RUNNING, started_at=real_now(), attempts=1)
            for event in events:
                event.status = AIEvent.STATUS_RUNNING
        return events, None if events else "the notification could not be queued"

    def fire_reminder(self, followup):
        """
        One sandbox reminder becomes due. Mirrors service.fire_due_followups(), which can not be called here: it fires
        every due reminder in the database, the live ones too. Returns (events, note).
        """
        from mysite.models import AIEvent, AIFollowUp
        with transaction.atomic():
            if not AIFollowUp.objects.filter(id=followup.id, status=AIFollowUp.STATUS_PENDING).update(status=AIFollowUp.STATUS_FIRED):
                return [], "the reminder is not pending any more (closed or already fired)"
            followup.refresh_from_db()
            issue = followup.issue
            if issue and not issue.is_open:
                note = 'issue already resolved - AI not woken'
                AIFollowUp.objects.filter(id=followup.id).update(status=AIFollowUp.STATUS_CANCELLED, status_note=note[:255])
                return [], f"closed quietly, no alert ({note})"
            event = AIEvent.objects.create(
                event_type=AIEvent.TYPE_FOLLOWUP_DUE, conversation=self.conversation, conversation_sid=SANDBOX_SID,
                body=followup.reason, payload={'followup_id': followup.id, 'source': 'followup'},
                status=AIEvent.STATUS_RUNNING, started_at=real_now(), attempts=1,
            )
        return [event], None


# The whole message must be the command: "next time offer a same-day visit" is a lesson for the AI, not "next"
# Test commands, typed in the test group (also as /commands, which reach a bot that is not an admin of the group)
COMMAND = re.compile(
    r'^\s*/?(?:(next|continue|rerun|restart|accept|skip|stop|apply|add[\s_-]*test|fix|nofix|no[\s_-]*fix(?:[\s_-]*needed)?)(?:@\w+)?\s*[.!]?'
    r'|(rerun\s+with|change\s+test)\s*:\s*(.+)'
    r'|(test[\s_-]*conversation(?:[\s_-]*id)?|run)(?:@\w+)?\s*:?\s+(.+?))\s*$', re.I | re.S)
_CASES = re.compile(r'^[A-Za-z]\d*[a-z]?(\s*,\s*[A-Za-z]\d*[a-z]?)*$')
_CHAT = re.compile(r'^CH\w{6,}(\s+(last\s+)?\d+)?$', re.I)


def parse_command(text):
    """'rerun with: the sink floods' -> ('rerun with', 'the sink floods'); 'test-conversation CHab12 last 20' ->
    ('test conversation', 'CHab12 last 20'); 'run A5,B' -> ('run', 'A5,B'); not a test command -> None."""
    match = COMMAND.match(text or '')
    if not match:
        return None
    word = ' '.join(re.sub(r'[\s_-]+', ' ', (match.group(1) or match.group(2) or match.group(4)).lower()).split())
    if word.startswith('no fix'):
        word = 'nofix'
    rest = (match.group(3) or match.group(5) or '').strip()
    if word.startswith('test conversation'):
        word = 'test conversation'
        if not _CHAT.match(rest):
            return None
    elif word == 'run' and not _CASES.match(rest):
        return None   # "run the dryer again" is a sentence, not a command
    return word, rest
