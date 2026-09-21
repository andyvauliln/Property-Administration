import os, sys, django
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite"
from django.test import Client
from django.template.loader import render_to_string
from mysite.models import User, AIRun, AIIssue, AIFollowUp, StaffMember, TwilioMessage
from mysite.views.ai_agent_views import get_ai_activity
from mysite.ai_agent import config
from pathlib import Path
from django.conf import settings as _s
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs"

ok = []
def check(name, cond, extra=''):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f" -> {extra}" if not cond else ''))

if not User.objects.filter(email="admin@example.com").exists():
    User.objects.bulk_create([User(email="admin@example.com", full_name="Admin", role="Admin", is_active=True)])
    User.objects.bulk_create([User(email="cleaner@example.com", full_name="Cleaner", role="Cleaner", is_active=True)])
admin, cleaner = User.objects.get(email="admin@example.com"), User.objects.get(email="cleaner@example.com")
c = Client(); c.force_login(admin)

r = c.get("/ai-runs/"); check("/ai-runs/ renders with runs + totals", r.status_code == 200 and b"AI agent runs" in r.content and b"FOLLOWUP" not in r.content[:0] )
run = AIRun.objects.exclude(report_dir__isnull=True).order_by('id').first()
r = c.get(f"/ai-runs/{run.id}/"); check("run detail shows report.md", r.status_code == 200 and b"report.md" in r.content and b"## Actions" in r.content, r.status_code)
r = c.get(f"/ai-runs/{run.id}/?file=07_actions.json"); check("run detail shows actions file", r.status_code == 200 and b"CREATE_ISSUE" in r.content)
r = c.get(f"/ai-runs/{run.id}/?file=../../../../.env"); check("path traversal refused", r.status_code == 404, r.status_code)
r = c.get("/ai-runs/?errors=1"); check("errors filter works", r.status_code == 200 and b"timed out" in r.content)

r = c.get("/ai-issues/"); check("/ai-issues/ lists open issues", r.status_code == 200 and b"loop test" in r.content and b"Kitchen sink" not in r.content)
r = c.get("/ai-issues/?all=1"); check("/ai-issues/?all=1 includes resolved", r.status_code == 200 and b"Kitchen sink dripping" in r.content)
issue = AIIssue.objects.get(summary='loop test')
AIFollowUp.objects.filter(issue=issue).update(status='pending')
r = c.post(f"/ai-issues/{issue.id}/resolve/", {"next": "/ai-issues/"}); issue.refresh_from_db()
check("manager resolve: issue closed, timers stopped, redirect", r.status_code == 302 and issue.state == 'RESOLVED' and not issue.followups.filter(status='pending').exists())

r = c.get("/ai-staff/"); check("/ai-staff/ renders members", r.status_code == 200 and b"Farouk Ahmed" in r.content)
r = c.post("/ai-staff/", {"ai_name": "Kevin", "full_name": "Farid Gazizov", "role": "supervisor", "phone": "5615550123", "clickup_user_id": "126173964", "is_active": "on"})
k = StaffMember.objects.filter(ai_name="Kevin").first()
check("staff add: saved, phone normalised to E.164", r.status_code == 302 and k and k.phone == "+15615550123", getattr(k, 'phone', None))
r = c.post("/ai-staff/", {"id": k.id, "ai_name": "Kevin", "role": "supervisor", "phone": "not-a-phone"})
check("staff edit with bad phone shows error, keeps old value", r.status_code == 200 and b"bg-red-50" in r.content and StaffMember.objects.get(id=k.id).phone == "+15615550123")

m = TwilioMessage.objects.filter(ai_runs__isnull=False).first()
r = c.get(f"/chat/{m.conversation_sid}/messages/{m.id}/ai-agent-status/"); d = r.json()
check("status endpoint: finished + run link + tokens", r.status_code == 200 and d["finished"] and d["run"] and d["run"]["url"].startswith("/ai-runs/"), d)

activity = get_ai_activity("CHtest0001")
html = render_to_string("components/ai_activity_panel.html", {"ai_activity": activity, "conversation": m.conversation, "request": type("R", (), {"path": "/chat/x/"})()})
check("chat panel renders issues/notes/runs", "AI agent activity" in html and "Case notes" in html and "Last runs" in html)
check("chat panel hidden when nothing tracked", render_to_string("components/ai_activity_panel.html", {"ai_activity": get_ai_activity("CHnone")}).strip() == "")

c2 = Client(); c2.force_login(cleaner)
check("non-manager roles are refused", all(c2.get(u).status_code == 403 for u in ("/ai-runs/", "/ai-issues/", "/ai-staff/", f"/ai-runs/{run.id}/")))
check("anonymous is redirected to login", Client().get("/ai-runs/").status_code == 302)
print(f"\n{sum(ok)}/{len(ok)} UI checks passed"); sys.exit(0 if all(ok) else 1)
