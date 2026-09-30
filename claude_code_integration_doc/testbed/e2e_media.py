"""
Photos in Twilio group chats (MMS): webhook stores them, chat UI shows them, the Claude agent sees them,
ClickUp tasks get them, the backfill command recovers old ones. Throwaway DB; Twilio, Claude, ClickUp faked.
"""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

import io
import json
from django.conf import settings as _s
from django.core.management import call_command
from django.http import QueryDict
from django.test import Client
from django.utils import timezone
from PIL import Image
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, TwilioMessageMedia, AIEvent,
                           AIManagement)
from mysite.ai_agent import runner, config, service, inputs, team_notify
import mysite.twilio_media as twilio_media
import mysite.views.messaging as messaging

_s.TWILIO_MEDIA_DIR = str(_s.TESTBED_DIR / "twilio_media")
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_media"
config.WORK_DIR = config.RUNS_DIR / "_cwd"

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

# ---- fakes ---------------------------------------------------------------------------------
def jpeg(w=3000, h=2000, color=(200, 30, 30)):
    buf = io.BytesIO(); Image.new('RGB', (w, h), color).save(buf, format='JPEG'); return buf.getvalue()
PHOTO = jpeg()
downloads = []
def fake_download(chat_service_sid, media_sid):
    downloads.append((chat_service_sid, media_sid))
    if media_sid.startswith('MEbad'):
        raise RuntimeError("404 media gone")
    return PHOTO
twilio_media.download_media = fake_download

def no_twilio():
    raise RuntimeError("Twilio must not be called by this test")
messaging.get_twilio_client = no_twilio
messaging.client = None

script, calls = [], []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None, images=None):
    calls.append({'input': user_input, 'images': images or []})
    return {'ok': True, 'error': None, 'output': script.pop(0), 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 5, 'timed_out': False}
runner.run_claude = fake_run_claude

# ---- fixtures -------------------------------------------------------------------------------
PHONE = "+15550007711"
if not User.objects.filter(email="md@example.com").exists():
    User.objects.bulk_create([User(email="md@example.com", full_name="Mia Photo", role="Tenant", phone=PHONE)])
    User.objects.bulk_create([User(email="md-admin@example.com", full_name="Admin Md", role="Admin", is_active=True)])
tenant, admin = User.objects.get(email="md@example.com"), User.objects.get(email="md-admin@example.com")
Apartment.objects.bulk_create([Apartment(name="750-201 test", building_n="750", apartment_n="201", street="S", state="FL",
    city="WPB", zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available")])
apt = Apartment.objects.get(name="750-201 test")
Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() - timedelta(days=2),
                                     end_date=date.today() + timedelta(days=20), status="Confirmed")])
SID = "CHmedia750"
TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=SID, friendly_name="750-201", apartment=apt,
                                                           booking=Booking.objects.get(apartment=apt))])
conv = TwilioConversation.objects.get(conversation_sid=SID)
AIManagement.objects.update_or_create(prompt_key="ai_backend", defaults={'name': 'b', 'entry_type': 'ai_model', 'content': 'claude_cli'})

def webhook_post(message_sid, body, media):
    return {
        'EventType': 'onMessageAdded', 'MessageSid': message_sid, 'ConversationSid': SID, 'ChatServiceSid': 'ISsvc1',
        'Author': PHONE, 'Body': body, 'Media': json.dumps(media) if media else '',
    }

# ---- 1. parsing + storing -------------------------------------------------------------------
qd = QueryDict(mutable=True); qd.update(webhook_post('IMx', '', [{'Sid': 'MEa1', 'ContentType': 'image/jpeg', 'Filename': 'a.jpg', 'Size': 12}]))
svc, items = twilio_media.parse_webhook_media(qd)
check("webhook Media JSON parsed", svc == 'ISsvc1' and items == [{'sid': 'MEa1', 'content_type': 'image/jpeg', 'filename': 'a.jpg', 'size': 12}], items)
check("bad Media JSON -> no items, no crash", twilio_media.parse_webhook_media({'Media': '{nope'})[1] == [])

# ---- 2. the real webhook with a photo-only message -------------------------------------------
c = Client()
resp = c.post('/conversation-created-webhook/', webhook_post('IMphoto1', '', [{'Sid': 'MEphoto1', 'ContentType': 'image/jpeg', 'Filename': 'leak.jpg', 'Size': len(PHOTO)}]))
m1 = TwilioMessage.objects.filter(message_sid='IMphoto1').first()
media1 = TwilioMessageMedia.objects.filter(media_sid='MEphoto1').first()
check("webhook answered", resp.status_code < 500, resp.status_code)
check("photo-only message saved with empty body", m1 is not None and m1.body == '')
check("photo downloaded once and linked to the message", media1 and media1.message_id == m1.id and media1.is_downloaded
      and os.path.isfile(twilio_media.file_path_of(media1)) and downloads == [('ISsvc1', 'MEphoto1')], downloads)
ev = AIEvent.objects.filter(message=m1).first()
check("photo-only message queued for the Claude agent", ev is not None and ev.body == '[photo]', getattr(ev, 'body', None))

resp = c.post('/conversation-created-webhook/', webhook_post('IMphoto1', '', [{'Sid': 'MEphoto1', 'ContentType': 'image/jpeg'}]))
check("webhook retry does not download or queue again", len(downloads) == 1 and AIEvent.objects.filter(message=m1).count() == 1)

resp = c.post('/conversation-created-webhook/', webhook_post('IMbad1', 'see pic', [{'Sid': 'MEbad1', 'ContentType': 'image/png'}]))
bad = TwilioMessageMedia.objects.get(media_sid='MEbad1')
check("failed download keeps the message and records the error", TwilioMessage.objects.filter(message_sid='IMbad1', body='see pic').exists()
      and not bad.is_downloaded and 'gone' in bad.download_error)
AIEvent.objects.filter(message__message_sid='IMbad1').delete()

AIManagement.objects.filter(prompt_key="ai_backend").update(content='openrouter')
resp = c.post('/conversation-created-webhook/', webhook_post('IMlegacy', '', [{'Sid': 'MElegacy', 'ContentType': 'image/jpeg'}]))
check("an old 'openrouter' backend row changes nothing: the photo is stored and queued for the agent",
      TwilioMessageMedia.objects.filter(media_sid='MElegacy').exists() and AIEvent.objects.filter(message__message_sid='IMlegacy').exists())
AIEvent.objects.filter(message__message_sid='IMlegacy').delete()
AIManagement.objects.filter(prompt_key="ai_backend").delete()

# ---- 3. chat UI -------------------------------------------------------------------------------
anon = Client()
check("media file needs login", anon.get(media1.url).status_code in (302, 403))
ui = Client(); ui.force_login(admin)
r = ui.get(media1.url)
check("logged-in staff get the photo", r.status_code == 200 and r['Content-Type'] == 'image/jpeg' and b''.join(r.streaming_content) == PHOTO)
html = ui.get(f'/chat/{SID}/').content.decode()
check("chat page shows the photo thumbnail", f'src="{media1.url}"' in html)
check("undownloaded photo shows a placeholder", 'Photo not downloaded yet' in html)
svg = TwilioMessageMedia.objects.create(message=m1, media_sid='MEsvg', content_type='image/svg+xml', file_path=media1.file_path)
r = ui.get(svg.url)
check("SVG is never served inline as an image", not svg.is_image and r.get('Content-Disposition') == 'attachment')
svg.delete()
evil = TwilioMessageMedia.objects.create(message=m1, media_sid='MEevil', content_type='image/jpeg', file_path='../../etc/passwd')
check("path traversal refused", ui.get(evil.url).status_code == 404)
evil.delete()
js = ui.get(f'/chat/{SID}/load-more/?page=1')
if js.status_code == 200:
    rows = {m['id']: m for m in js.json()['messages']}
    check("JSON messages carry media", rows[m1.id]['media'] == [{'id': media1.id, 'url': media1.url, 'content_type': 'image/jpeg',
                                                                  'filename': 'leak.jpg', 'is_image': True}], rows.get(m1.id))

# ---- 4. the agent sees the photo -----------------------------------------------------------------
from django.urls import reverse
AIEvent.objects.filter(status='pending').update(created_at=timezone.now() - timedelta(minutes=5))
script.append({'answer': 'Thanks for the photo - we will send someone to check the leak.', 'why': 'photo shows a leak', 'actions': []})
while True:
    batch = service.claim_next_batch()
    if not batch: break
    service.process_events(batch)
call = calls[-1] if calls else {'input': '', 'images': []}
check("agent input marks the photo in the chat line", f"[photo #{media1.id}]" in call['input'], call['input'][-400:])
check("agent input lists the attached photos", "=== PHOTOS ===" in call['input'] and f"photo #{media1.id}" in call['input'].split("=== PHOTOS ===")[1])
check("agent gets the image itself (JPEG, downscaled)", len(call['images']) == 1 and call['images'][0]['media_type'] == 'image/jpeg'
      and call['images'][0]['media_id'] == media1.id)
import base64
img = Image.open(io.BytesIO(base64.b64decode(call['images'][0]['data'])))
check("image downscaled to <= 1568 px", max(img.size) <= 1568, img.size)

stdin = runner.build_stdin("hello", [{'media_type': 'image/jpeg', 'data': 'QUJD'}])
msg = json.loads(stdin)
check("stream-json stdin: text block then image block", msg['type'] == 'user' and msg['message']['content'][0] == {'type': 'text', 'text': 'hello'}
      and msg['message']['content'][1]['source'] == {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'QUJD'})
check("no images -> plain text stdin", runner.build_stdin("hello") == "hello")
cmd = runner.build_command('m', 'sp', 'mcp', with_images=True)
check("CLI switches to stream-json input only with images, still no built-in tools",
      cmd[cmd.index('--input-format') + 1] == 'stream-json' and cmd[cmd.index('--tools') + 1] == ''
      and '--input-format' not in runner.build_command('m', 'sp', 'mcp'))

# ---- 5. ClickUp task gets the photo --------------------------------------------------------------
from mysite.ai_agent import clickup
uploaded = []
clickup.attach_file = lambda task_id, path, filename=None, content_type=None, list_id=None: uploaded.append((task_id, os.path.basename(path), content_type))
photos = team_notify._trigger_photos({'trigger_message_ids': [m1.id]})
note = team_notify._attach_photos('task9', photos, 'list1')
check("trigger photos attached to the ClickUp task", uploaded == [('task9', os.path.basename(media1.file_path), 'image/jpeg')] and '1 photo(s) attached' in note, (uploaded, note))

# ---- 6. backfill ------------------------------------------------------------------------------------
old = TwilioMessage.objects.create(message_sid='IMold1', conversation=conv, conversation_sid=SID, author=PHONE, body='', direction='inbound')
TwilioMessage.objects.create(message_sid='IMold2', conversation=conv, conversation_sid=SID, author=PHONE, body='  ', direction='inbound')
TwilioMessage.objects.create(message_sid='LOCAL-1', conversation=conv, conversation_sid=SID, author='ASSISTANT', body='', direction='outbound')

class _Remote:
    def __init__(self, media): self.media = media
class _Msg:
    def __init__(self, sid): self.sid = sid
    def fetch(self): return _Remote([{'sid': 'MEold1', 'content_type': 'image/jpeg', 'filename': 'old.jpg', 'size': 5}] if self.sid == 'IMold1' else [])
class _Conv:
    def fetch(self): return type('C', (), {'chat_service_sid': 'ISsvc1'})()
    def messages(self, sid): return _Msg(sid)
class _Client:
    class conversations:
        class v1:
            @staticmethod
            def conversations(sid): return _Conv()
messaging.get_twilio_client = lambda: _Client()
out = io.StringIO(); call_command('backfill_twilio_media', '--dry-run', '--conversation', SID, stdout=out)
check("dry run lists only empty Twilio messages without media", 'IMold1' in out.getvalue() and 'IMold2' in out.getvalue()
      and 'LOCAL-1' not in out.getvalue() and 'IMphoto1' not in out.getvalue(), out.getvalue())
check("dry run changes nothing", not TwilioMessageMedia.objects.filter(media_sid='MEold1').exists())
events_before = AIEvent.objects.count()
out = io.StringIO(); call_command('backfill_twilio_media', '--conversation', SID, stdout=out)
check("backfill stores the old photo", TwilioMessageMedia.objects.get(media_sid='MEold1').message_id == old.id
      and TwilioMessageMedia.objects.get(media_sid='MEold1').is_downloaded, out.getvalue())
check("backfill reports counts", '1 message(s) had media, 1 file(s) stored, 1 had no media, 0 failed' in out.getvalue(), out.getvalue())
check("backfill never queues the AI", AIEvent.objects.count() == events_before)
twilio_media.download_media = lambda svc, sid: PHOTO
out = io.StringIO(); call_command('backfill_twilio_media', '--retry-failed', '--conversation', SID, stdout=out)
check("--retry-failed re-downloads earlier failures", TwilioMessageMedia.objects.get(media_sid='MEbad1').is_downloaded, out.getvalue())

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
