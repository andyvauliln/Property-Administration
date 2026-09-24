"""ClickUp routing checks on the throwaway DB. The ClickUp HTTP API is faked: nothing leaves the machine."""
import os, sys, django
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite"
from mysite.models import Apartment, AIIssue, AIRun, StaffMember
from mysite.ai_agent import actions, clickup, team_notify

telegram, calls = [], []
team_notify.notify_ai_chat = lambda t: (telegram.append(t), (True, "sent"))[1]
team_notify.send_ai_chat = lambda t, reply_to=None: (telegram.append(t), (True, "sent", None))[1]
class FakeResponse:
    def __init__(self, status, data): self.status_code, self._d, self.text = status, data, str(data)
    def json(self): return self._d
fail = {'on': False}
def fake_request(method, url, json=None, timeout=None, headers=None):
    calls.append((method, url.replace(clickup.API, ''), json, headers.get('Authorization')))
    if fail['on']: return FakeResponse(401, {'err': 'Token invalid'})
    if url.endswith('/task'): return FakeResponse(200, {'id': 'abc123', 'url': 'https://app.clickup.com/t/abc123'})
    if url.endswith('/channels/location'): return FakeResponse(201, {'data': {'id': '6-555-8'}})
    return FakeResponse(201, {'data': {'id': 'msg-1'}})
clickup.requests.request = fake_request
checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

Apartment.objects.bulk_create([Apartment(name="Test_CU", building_n="555", apartment_n="1", street="T", state="FL", city="W", zip_index="1",
    bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available"),
    Apartment(name="Plain_CU", building_n="556", apartment_n="1", street="T", state="FL", city="W", zip_index="1",
    bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available")])
mapped, plain = Apartment.objects.get(name="Test_CU"), Apartment.objects.get(name="Plain_CU")
mapped.ai_clickup_list_id, mapped.ai_clickup_channel_id, mapped.ai_clickup_name = "555", "6-555-8", "TEST channel"
mapped.save()
StaffMember.objects.get_or_create(ai_name="Edy", defaults={'full_name': 'Farouk Ahmed', 'role': 'operations', 'clickup_user_id': '176673799'})
StaffMember.objects.get_or_create(ai_name="Andrei", defaults={'full_name': 'Andrei Vaulin', 'role': 'engineering', 'clickup_user_id': '89595503'})
def run(apartment, mode, acts, sid):
    ctx = actions.ActionContext(mode, {'apartment': apartment.name, 'tenant': 'T', 'mode': mode, 'event_type': 'TENANT_MESSAGE'}, 'tenant text', sid, apartment=apartment)
    parsed = {'answer': None, 'why': 'w', 'actions': acts}
    results = actions.execute_actions(parsed, ctx)
    team_notify.deliver(ctx, AIRun.objects.create(conversation_sid=sid), parsed, results, {}, 'tenant text')
    return results
ticket = [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Sink drips', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
          {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'priority': 'urgent', 'title': 'Sink drips - Test_CU', 'description': 'd', 'responsible': ['Edy']},
          {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'urgent', 'responsible': ['Edy'], 'text': 'sink'}]

os.environ.pop('CLICKUP_API_TOKEN', None)
res = run(mapped, 'test', ticket, 'CHcu1')
check("no token (and Claude delivery off) -> nothing sent to ClickUp, Telegram still gets it, action still executed",
      not calls and len(telegram) == 1 and all(r['status'] == 'executed' for r in res) and 'ClickUp: skipped' in res[1]['detail'], res)

os.environ['CLICKUP_API_TOKEN'] = 'pk_test'; telegram.clear()
res = run(mapped, 'test', ticket, 'CHcu2')
task_calls = [c for c in calls if c[1].endswith('/task')]; msg_calls = [c for c in calls if c[1].endswith('/messages')]
check("test mode: task created in the List, marked [AI][TEST], urgent -> priority 2, assigned to engineering (Andrei), not to Edy, has a due date and the ai/maintenance tags",
      len(task_calls) == 1 and task_calls[0][1] == '/v2/list/555/task' and task_calls[0][2]['name'].startswith('[AI] [TEST]')
      and task_calls[0][2]['priority'] == 2 and task_calls[0][2].get('assignees') == [89595503]
      and task_calls[0][2].get('due_date') and task_calls[0][2].get('due_date_time') is True
      and task_calls[0][2].get('tags') == ['ai', 'maintenance'], task_calls)
check("ONE channel message per run with the token header, TEST marker, task link and report link",
      len(msg_calls) == 1 and msg_calls[0][3] == 'pk_test' and 'TEST MODE' in msg_calls[0][2]['content']
      and 'app.clickup.com/t/abc123' in msg_calls[0][2]['content'] and 'http://crm.test/ai-runs/' in msg_calls[0][2]['content'], msg_calls)
check("task link stored on the issue", AIIssue.objects.get(conversation_sid='CHcu2').ticket_ref == 'https://app.clickup.com/t/abc123')
check("Telegram copy kept (one message) and it shows the task link too", len(telegram) == 1 and 'app.clickup.com/t/abc123' in telegram[0])

calls.clear()
res = run(mapped, 'live', ticket, 'CHcu3')
check("live mode: task assigned to Edy's ClickUp user, no [TEST], still marked [AI]", [c for c in calls if c[1].endswith('/task')][0][2].get('assignees') == [176673799]
      and '[TEST]' not in [c for c in calls if c[1].endswith('/task')][0][2]['name']
      and [c for c in calls if c[1].endswith('/task')][0][2]['name'].startswith('[AI]'))
calls.clear()
res = run(mapped, 'live', [{'type': 'CREATE_TICKET', 'issue_id': AIIssue.objects.get(conversation_sid='CHcu3').public_id, 'priority': 'urgent', 'title': 'again'}], 'CHcu3')
check("an issue that already has a task does not get a second one", not [c for c in calls if c[0] == 'POST' and c[1].endswith('/task')])

calls.clear(); telegram.clear()
res = run(plain, 'live', ticket, 'CHcu4')
check("apartment without a mapping -> Telegram only", not calls and len(telegram) == 1)

plain.ai_clickup_list_id, plain.ai_clickup_name = "777", "real apartment list"
plain.save()
calls.clear(); telegram.clear()
res = run(plain, 'test', ticket, 'CHcu6')
t = [c for c in calls if c[1].endswith('/task')]
check("tasks are created in test mode too (real apartment): its own List, [AI][TEST] + unit in the name, assigned to Andrei",
      len(t) == 1 and t[0][1] == '/v2/list/777/task' and t[0][2]['name'] == '[AI] [TEST] Plain_CU · Sink drips - Test_CU' and t[0][2]['assignees'] == [89595503], t)
calls.clear()
res = run(plain, 'live', ticket, 'CHcu7')
check("the same apartment LIVE: assigned to Edy", [c for c in calls if c[1] == '/v2/list/777/task'][0][2].get('assignees') == [176673799])
os.environ['AI_AGENT_CLICKUP_TEST_LIST_ID'] = '999'; calls.clear()
run(plain, 'live', ticket, 'CHcu8'); run(mapped, 'test', ticket, 'CHcu9')
t = [c for c in calls if c[1].endswith('/task')]
check("test List configured: EVERY apartment's task goes there (even live + mapped), assigned to Andrei, no chat message",
      [c[1] for c in t] == ['/v2/list/999/task'] * 2 and all(c[2]['assignees'] == [89595503] for c in t) and not [c for c in calls if c[1].endswith('/messages')], calls)
del os.environ['AI_AGENT_CLICKUP_TEST_LIST_ID']
plain.ai_clickup_list_id, plain.ai_clickup_channel_id, plain.ai_clickup_name = None, None, None
plain.save()

Apartment.objects.create(name="Another_Test_Unit", building_n="558", apartment_n="1", street="T", state="FL", city="W", zip_index="1",
    bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available")
another_test = Apartment.objects.get(name="Another_Test_Unit")
calls.clear(); telegram.clear()
run(another_test, 'live', ticket, 'CHcu10')
t = [c for c in calls if c[1].endswith('/task')]
check("an apartment with 'test' in its name but no mapping row of its own still routes to the shared test channel/List, no test-list env set",
      len(t) == 1 and t[0][1] == '/v2/list/555/task', t)

calls.clear(); telegram.clear()
res = run(plain, 'live', ticket, 'CHcu11')
check("a real (non-test-named) apartment without a mapping row still gets no ClickUp routing, Telegram only",
      not calls and len(telegram) == 1, calls)

calls.clear(); fail['on'] = True
res = run(mapped, 'live', [{'type': 'INTERNAL_ALERT', 'priority': 'routine', 'responsible': ['Edy'], 'text': 'x'}], 'CHcu5')
check("ClickUp error never loses the alert: action executed via Telegram, failure written in the detail",
      res[0]['status'] == 'executed' and 'ClickUp FAILED' in res[0]['detail'] and '401' in res[0]['detail'])
fail['on'] = False
check("create channel for a List returns its id", clickup.create_list_channel('555') == '6-555-8'
      and calls[-1][2] == {'location': {'id': '555', 'type': 'list'}, 'visibility': 'PRIVATE'})
def fake_get(method, url, json=None, timeout=None, headers=None):
    if url.endswith('/comment'):
        return FakeResponse(200, {'comments': [{'comment_text': 'older note', 'user': {'username': 'Janna'}, 'date': '1789990000000'},
                                               {'comment_text': 'Plumber booked for 3 PM', 'user': {'username': 'Farouk Ahmed'}, 'date': '1789998553506'}]})
    return FakeResponse(200, {'id': 'abc123', 'status': {'status': 'in progress', 'type': 'custom'}, 'date_closed': None, 'date_updated': '1789998553506',
                              'assignees': [{'id': 1, 'username': 'Farouk Ahmed'}]})
clickup.requests.request = fake_get
state = clickup.get_task_state('https://app.clickup.com/t/abc123')
check("task state (API): status, not closed, assignee, newest comment last, Florida time", state['status'] == 'in progress' and not state['closed']
      and state['assignees'] == ['Farouk Ahmed'] and state['comments'][-1]['text'] == 'Plumber booked for 3 PM' and state['comments'][-1]['when'].startswith('2026-09-21 09:4'), state)
check("closed is recognised from date_closed and from the status type",
      clickup._task_state({'status': 'complete', 'date_closed': '1789998553506'}, [])['closed'] and clickup._task_state({'status': {'status': 'done', 'type': 'closed'}}, [])['closed']
      and not clickup._task_state({'status': 'to do', 'date_closed': None}, [])['closed'])
check("task id is read from both link forms", clickup.task_id_from_ref('https://app.clickup.com/t/86akmxt14') == '86akmxt14'
      and clickup.task_id_from_ref('https://app.clickup.com/t/9013651059/86akmxt14') == '86akmxt14' and clickup.task_id_from_ref('86akmxt14') == '86akmxt14')

# ClickUp writes switch (user request 2026-09-24): off -> alerts say what would happen, nothing is written.
# Apartments with "test" in the name (sandbox) ignore the switch and always write.
from mysite.models import AIManagement
from mysite.ai_agent import answer_review
clickup.requests.request = fake_request
Apartment.objects.create(name="Real_CU", building_n="559", apartment_n="1", street="T", state="FL", city="W", zip_index="1",
    bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", ai_clickup_list_id="888", ai_clickup_name="real list")
real = Apartment.objects.get(name="Real_CU")
run(real, 'live', ticket, 'CHcu19')   # writes on: this issue gets a real task
real_issue = AIIssue.objects.get(conversation_sid='CHcu19')
os.environ['AI_AGENT_CLICKUP_WRITES'] = 'off'
calls.clear(); telegram.clear()
res = run(real, 'live', ticket, 'CHcu20')
check("writes OFF (env), real apartment: no ClickUp call at all, Telegram says writes are OFF and names the task it would create, no ticket_ref stored",
      not calls and len(telegram) == 1 and 'ClickUp writes OFF' in telegram[0] and 'Would CREATE ClickUp task' in telegram[0]
      and 'NOT created' in telegram[0] and not AIIssue.objects.get(conversation_sid='CHcu20').ticket_ref
      and 'ClickUp writes OFF' in res[1]['detail'] and all(r['status'] == 'executed' for r in res), (calls, telegram, res))
calls.clear()
res = run(real, 'live', [{'type': 'TICKET_COMMENT', 'ticket_id': real_issue.public_id, 'text': 'plumber booked'},
                         {'type': 'UPDATE_ISSUE_STATE', 'issue_id': real_issue.public_id, 'state': 'RESOLVED'}], 'CHcu19')
check("writes OFF, real apartment: comment and close of an existing task are only described, nothing sent",
      not calls and 'comment NOT posted' in res[0]['detail'] and 'task NOT closed' in res[1]['detail'], (calls, res))
try:
    clickup.create_task('888', 'x', 'y'); blocked = False
except clickup.ClickUpWritesOff:
    blocked = True
check("writes OFF: the clickup module itself refuses writes for a real List (safety net)", blocked and not calls)

calls.clear(); telegram.clear()
res = run(mapped, 'live', ticket, 'CHcu22')
check("writes OFF: a TEST apartment still creates its task + channel message, no OFF line in its alert",
      [c for c in calls if c[1] == '/v2/list/555/task'] and [c for c in calls if c[1].endswith('/messages')]
      and 'ClickUp writes OFF' not in telegram[0] and AIIssue.objects.get(conversation_sid='CHcu22').ticket_ref, (calls, telegram))
test_issue = AIIssue.objects.get(conversation_sid='CHcu22')
test_issue.apartment = mapped; test_issue.save()
calls.clear()
res = run(mapped, 'live', [{'type': 'TICKET_COMMENT', 'ticket_id': test_issue.public_id, 'text': 'on it'}], 'CHcu22')
check("writes OFF: a TEST apartment's existing task still gets the comment", [c for c in calls if c[1].endswith('/comment')], (calls, res))
calls.clear()
clickup.create_task('555', 'x', 'y')
check("writes OFF: the safety net lets the test List through", [c for c in calls if c[1] == '/v2/list/555/task'])

os.environ.pop('AI_AGENT_CLICKUP_WRITES')
AIManagement.objects.create(prompt_key='ai_clickup_writes', content='off')
check("writes switch from the AIManagement row; test apartments stay on", not clickup.writes_enabled(real) and clickup.writes_enabled(mapped))
AIManagement.objects.filter(prompt_key='ai_clickup_writes').update(content='on')
calls.clear()
run(real, 'live', ticket, 'CHcu21')
check("writes back ON -> real apartment task created again", [c for c in calls if c[1] == '/v2/list/888/task'])
AIManagement.objects.filter(prompt_key='ai_clickup_writes').delete()
del os.environ['CLICKUP_API_TOKEN']
print(f"\n{sum(checks)}/{len(checks)} checks passed"); sys.exit(0 if all(checks) else 1)
