"""
ClickUp delivery for the AI agent: a message in the apartment's chat channel and, for tickets, a task
in the apartment's List. Inert until CLICKUP_API_TOKEN is set in .env.

Only apartments with Apartment.ai_clickup_list_id set (and ai_clickup_active) are routed here.
Everything still goes to the AI Telegram chat as well, so a ClickUp problem never loses an alert.
"""
import os

import requests

API = "https://api.clickup.com/api"
TIMEOUT = 15
PRIORITY = {'emergency': 1, 'urgent': 2, 'routine': 3}   # ClickUp: 1 urgent, 2 high, 3 normal, 4 low
# How soon an AI-created task is due, by priority. Every AI task gets a due date (user request 2026-09-22).
DUE_IN_HOURS = {'emergency': 4, 'urgent': 24, 'routine': 72}


class ClickUpError(Exception):
    pass


class ClickUpWritesOff(ClickUpError):
    pass


_PRESS = {'active': 0}


class pressed:
    """
    `with clickup.pressed():` - a person pressed a button for this ClickUp change (simple alerts, rule 1.1.2): it is
    done for real also when the AI's own ClickUp writing is OFF.
    """

    def __enter__(self):
        _PRESS['active'] += 1

    def __exit__(self, *exc):
        _PRESS['active'] -= 1


def press_active():
    return _PRESS['active'] > 0


def is_test_apartment(apartment):
    """Apartment (or its name) with "test" in the name: the sandbox / test apartments (same rule as channel_for)."""
    name = apartment if isinstance(apartment, str) else getattr(apartment, 'name', '')
    return 'test' in (name or '').lower()


def writes_enabled(apartment=None):
    """The ai_clickup_writes switch. Test apartments always write, whatever the switch says (user request 2026-09-24)."""
    from mysite.ai_agent import config
    return press_active() or config.clickup_writes_enabled() or is_test_apartment(apartment)


def _is_test_list(list_id):
    from mysite.models import Apartment
    if test_list_id() and str(list_id) == test_list_id():
        return True
    return Apartment.objects.filter(name__icontains='test', ai_clickup_list_id=str(list_id)).exists()


def _is_test_ticket(ticket_ref):
    from mysite.models import AIIssue
    return AIIssue.objects.filter(ticket_ref=ticket_ref, apartment__name__icontains='test').exists()


def _require_writes(list_id=None, ticket_ref=None):
    """Safety net: every AI write to ClickUp passes here. Callers normally check writes_enabled() first."""
    if writes_enabled() or (list_id and _is_test_list(list_id)) or (ticket_ref and _is_test_ticket(ticket_ref)):
        return
    raise ClickUpWritesOff("ClickUp writes are OFF (AIManagement 'ai_clickup_writes')")


def token():
    return (os.environ.get('CLICKUP_API_TOKEN') or '').strip()


def workspace_id():
    return (os.environ.get('CLICKUP_WORKSPACE_ID') or '9013651059').strip()


def is_configured():
    return bool(token())


def _call(method, path, payload=None):
    if not is_configured():
        raise ClickUpError("CLICKUP_API_TOKEN is not set")
    try:
        response = requests.request(
            method, f"{API}{path}", json=payload, timeout=TIMEOUT,
            headers={'Authorization': token(), 'Content-Type': 'application/json'},
        )
    except requests.RequestException as e:
        raise ClickUpError(f"ClickUp not reachable: {e}")
    if response.status_code >= 300:
        raise ClickUpError(f"ClickUp {method} {path} -> {response.status_code}: {response.text[:400]}")
    try:
        return response.json()
    except ValueError:
        return {}


def test_list_id():
    """
    Test period: when AI_AGENT_CLICKUP_TEST_LIST_ID is set, EVERY task of EVERY apartment goes into that one
    List (the "TEST-AI-sandbox" list) and is assigned to the engineering staff member. Remove the setting to
    route tasks to each apartment's own List.
    """
    return (os.environ.get('AI_AGENT_CLICKUP_TEST_LIST_ID') or '').strip()


class _TestListMapping:
    channel_id = None
    name = 'test list (AI_AGENT_CLICKUP_TEST_LIST_ID)'

    def __init__(self, list_id):
        self.list_id = list_id


class _ApartmentMapping:
    def __init__(self, apartment):
        self.list_id = apartment.ai_clickup_list_id
        self.channel_id = apartment.ai_clickup_channel_id
        self.name = apartment.ai_clickup_name or apartment.name


def _mapping_from(apartment):
    if apartment and apartment.ai_clickup_active and apartment.ai_clickup_list_id:
        return _ApartmentMapping(apartment)
    return None


def _any_mapped_test_apartment():
    from mysite.models import Apartment
    apartment = (
        Apartment.objects.filter(name__icontains='test', ai_clickup_active=True)
        .exclude(ai_clickup_list_id__isnull=True).exclude(ai_clickup_list_id='').first()
    )
    return _mapping_from(apartment)


def channel_for(apartment):
    """
    Where this apartment's tasks go, in order:
    1. While AI_AGENT_CLICKUP_TEST_LIST_ID is set (Phase 4 for real apartments is postponed), every
       apartment's tasks go to that one shared test List, no exceptions.
    2. Otherwise, any apartment with "test" in its name always goes to the shared test-sandbox
       channel/List - it must never leak into a real apartment's channel, and it must never be
       silently unrouted even if it has no mapping of its own (falls back to any mapped apartment
       whose name also contains "test").
    3. Otherwise, the apartment's own ai_clickup_* fields, if set; unmapped apartments get no ClickUp
       routing at all (Telegram only).
    """
    if not apartment:
        return None
    if test_list_id():
        return _TestListMapping(test_list_id())
    if 'test' in (apartment.name or '').lower():
        return _mapping_from(apartment) or _any_mapped_test_apartment()
    return _mapping_from(apartment)


def create_list_channel(list_id, visibility='PRIVATE'):
    """Creates (or returns) the chat channel that belongs to a List. Returns the channel id."""
    data = _call('POST', f"/v3/workspaces/{workspace_id()}/chat/channels/location", {
        'location': {'id': str(list_id), 'type': 'list'}, 'visibility': visibility,
    })
    channel = data.get('data') or data
    if not channel.get('id'):
        raise ClickUpError(f"ClickUp returned no channel id: {str(data)[:300]}")
    return channel['id']


def post_message(channel_id, text):
    data = _call('POST', f"/v3/workspaces/{workspace_id()}/chat/channels/{channel_id}/messages", {
        'type': 'message', 'content': text[:9000], 'content_format': 'text/md',
    })
    return (data.get('data') or data).get('id')


def default_due_at(priority):
    """When an AI-created task should be due: emergency same day, urgent next day, routine in 3 days."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from mysite.ai_agent import config
    hours = DUE_IN_HOURS.get(priority, DUE_IN_HOURS['routine'])
    return datetime.now(ZoneInfo(config.TEAM_TIMEZONE)) + timedelta(hours=hours)


def create_task(list_id, name, description, priority='routine', assignee_ids=None, due_at=None, tags=None):
    """Returns (task_id, task_url). Tags that don't exist yet in the space are created automatically."""
    _require_writes(list_id=list_id)
    payload = {'name': name[:250], 'description': description[:9000], 'priority': PRIORITY.get(priority, 3)}
    if assignee_ids:
        payload['assignees'] = [int(a) for a in assignee_ids if str(a).isdigit()]
    if tags:
        payload['tags'] = list(tags)
    payload['due_date'] = int((due_at or default_due_at(priority)).timestamp() * 1000)
    payload['due_date_time'] = True
    data = _call('POST', f"/v2/list/{list_id}/task", payload)
    return data.get('id'), data.get('url')


def attach_file(task_id, path, filename=None, content_type=None, list_id=None):
    """Uploads one file (a tenant photo) as an attachment of the task (list_id: the task's List, for the safety net)."""
    _require_writes(list_id=list_id)
    if not is_configured():
        raise ClickUpError("CLICKUP_API_TOKEN is not set")
    try:
        with open(path, 'rb') as fh:
            response = requests.post(
                f"{API}/v2/task/{task_id}/attachment", timeout=TIMEOUT * 2,
                headers={'Authorization': token()},
                files={'attachment': (filename or os.path.basename(path), fh, content_type or 'application/octet-stream')},
            )
    except (OSError, requests.RequestException) as e:
        raise ClickUpError(f"ClickUp attachment upload failed: {e}")
    if response.status_code >= 300:
        raise ClickUpError(f"ClickUp attachment -> {response.status_code}: {response.text[:400]}")


# ---------------------------------------------------------------------------
# Changing existing tasks (staff replies in Telegram, answer_review.py) - API token only
# ---------------------------------------------------------------------------

def _task_id_or_fail(ticket_ref):
    _require_writes(ticket_ref=ticket_ref)
    if delivery_mode() != 'api':
        raise ClickUpError("changing ClickUp tasks needs CLICKUP_API_TOKEN")
    task_id = task_id_from_ref(ticket_ref)
    if not task_id:
        raise ClickUpError(f"no ClickUp task id in '{ticket_ref}'")
    return task_id


def _status_of_type(list_id, types):
    """Name of the List's first status whose type is in types ('closed' / 'open')."""
    for status in _call('GET', f"/v2/list/{list_id}").get('statuses') or []:
        if status.get('type') in types:
            return status.get('status')
    raise ClickUpError(f"List {list_id} has no status of type {'/'.join(types)}")


def set_task_closed(ticket_ref, closed=True):
    """Moves the task to its List's closed status (or back to the open one). Returns the status name."""
    task_id = _task_id_or_fail(ticket_ref)
    list_id = ((_call('GET', f"/v2/task/{task_id}").get('list')) or {}).get('id')
    status = _status_of_type(list_id, ('closed', 'done') if closed else ('open',))
    _call('PUT', f"/v2/task/{task_id}", {'status': status})
    return status


def delete_task(ticket_ref):
    _call('DELETE', f"/v2/task/{_task_id_or_fail(ticket_ref)}")


def update_task(ticket_ref, name=None, description=None, priority=None):
    payload = {}
    if name:
        payload['name'] = name[:250]
    if description:
        payload['description'] = description[:9000]
    if priority in PRIORITY:
        payload['priority'] = PRIORITY[priority]
    if payload:
        _call('PUT', f"/v2/task/{_task_id_or_fail(ticket_ref)}", payload)
    return payload


def add_task_comment(ticket_ref, text):
    _call('POST', f"/v2/task/{_task_id_or_fail(ticket_ref)}/comment", {'comment_text': text[:9000], 'notify_all': True})


def delivery_mode():
    """api (CLICKUP_API_TOKEN set) | off"""
    return 'api' if is_configured() else 'off'


# ---------------------------------------------------------------------------
# Reading a task before a reminder: status + latest comments
# ---------------------------------------------------------------------------

def task_id_from_ref(ticket_ref):
    import re
    found = re.search(r'/t/(?:\d+/)?([A-Za-z0-9_-]+)', str(ticket_ref or ''))
    return found.group(1) if found else (str(ticket_ref).strip() if ticket_ref and '/' not in str(ticket_ref) else None)


def _when(milliseconds):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from mysite.ai_agent import config
    try:
        return datetime.fromtimestamp(int(milliseconds) / 1000, ZoneInfo(config.TEAM_TIMEZONE)).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return ''


def _task_state(task, comments):
    """Normalises what the API / the MCP tool return into one small dict."""
    status = task.get('status')
    status_type = ''
    if isinstance(status, dict):
        status, status_type = status.get('status'), status.get('type') or ''
    items = []
    for comment in (comments or [])[:50]:
        text = comment.get('comment_text') or comment.get('text') or comment.get('comment') or ''
        if isinstance(text, list):
            text = "".join(part.get('text', '') for part in text if isinstance(part, dict))
        user = comment.get('user') or {}
        items.append({'when': _when(comment.get('date')), 'ms': int(comment.get('date') or 0),
                      'user': user.get('username') or user.get('email') or '?', 'text': str(text).strip()[:500]})
    items.sort(key=lambda c: c['ms'])
    return {
        'status': str(status or '?'),
        'closed': bool(task.get('date_closed')) or status_type in ('closed', 'done'),
        'assignees': [a.get('username') or a.get('email') or str(a.get('id')) for a in task.get('assignees') or []],
        'updated': _when(task.get('date_updated')),
        'due': _when(task.get('due_date')) if task.get('due_date') else '',
        'url': task.get('url') or '',
        'comments': items[-5:],
    }


def get_task_state(ticket_ref):
    """{'status', 'closed', 'assignees', 'updated', 'comments': [{'when','user','text'}]}. Raises ClickUpError."""
    task_id = task_id_from_ref(ticket_ref)
    if not task_id:
        raise ClickUpError(f"no ClickUp task id in '{ticket_ref}'")
    if delivery_mode() != 'api':
        raise ClickUpError("ClickUp is not configured")
    task = _call('GET', f"/v2/task/{task_id}")
    comments = _call('GET', f"/v2/task/{task_id}/comment").get('comments') or []
    return _task_state(task, comments)


