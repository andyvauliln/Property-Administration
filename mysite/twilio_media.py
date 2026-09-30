"""
Photos/files sent in Twilio Conversations (group MMS).

Twilio keeps the media in its Media Content Service (MCS); the webhook and the Message resource only carry
media SIDs. We download each file once to settings.TWILIO_MEDIA_DIR/<conversation_sid>/<media_sid>.<ext>
and record it as a TwilioMessageMedia row.
"""
import base64
import io
import json
import mimetypes
import os
import re

import requests
from django.conf import settings

from mysite.unified_logger import log_error, log_info, log_warning

MCS_BASE = 'https://mcs.us1.twilio.com/v1/Services'
MAX_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT = 8
MODEL_MAX_EDGE = 1568
MODEL_MAX_IMAGES = 5

_EXTENSIONS = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
    'image/webp': '.webp', 'image/heic': '.heic', 'image/heif': '.heif', 'video/mp4': '.mp4',
    'video/3gpp': '.3gp', 'audio/amr': '.amr', 'application/pdf': '.pdf', 'text/vcard': '.vcf',
}
_SAFE = re.compile(r'[^A-Za-z0-9_-]')


def media_root():
    return settings.TWILIO_MEDIA_DIR


def _normalize_item(item):
    """Webhook items use Sid/ContentType/...; Message-resource items use sid/content_type/... ."""
    get = lambda *keys: next((item.get(k) for k in keys if item.get(k) not in (None, '')), None)
    size = get('Size', 'size')
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    return {
        'sid': get('Sid', 'sid'),
        'content_type': (get('ContentType', 'content_type') or '').lower(),
        'filename': get('Filename', 'filename') or '',
        'size': size,
    }


def parse_webhook_media(post):
    """Returns (chat_service_sid, [items]) from an onMessageAdded webhook POST."""
    raw = post.get('Media')
    if not raw:
        return post.get('ChatServiceSid'), []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log_warning(f"Could not parse webhook Media field: {raw!r}", category='sms')
        return post.get('ChatServiceSid'), []
    if isinstance(data, dict):
        data = [data]
    items = [_normalize_item(i) for i in data if isinstance(i, dict)]
    return post.get('ChatServiceSid'), [i for i in items if i['sid']]


def _auth():
    return (os.environ.get('TWILIO_ACCOUNT_SID', ''), os.environ.get('TWILIO_AUTH_TOKEN', ''))


def download_media(chat_service_sid, media_sid):
    """Bytes of one MCS media file. Raises on HTTP errors or when the file exceeds MAX_BYTES."""
    url = f"{MCS_BASE}/{chat_service_sid}/Media/{media_sid}/Content"
    with requests.get(url, auth=_auth(), timeout=DOWNLOAD_TIMEOUT, stream=True, allow_redirects=True) as resp:
        resp.raise_for_status()
        chunks, total = [], 0
        for chunk in resp.iter_content(64 * 1024):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError(f"media {media_sid} is larger than {MAX_BYTES} bytes")
            chunks.append(chunk)
    return b''.join(chunks)


def _relative_path(conversation_sid, media_sid, content_type):
    ext = _EXTENSIONS.get(content_type) or mimetypes.guess_extension(content_type or '') or '.bin'
    return os.path.join(_SAFE.sub('', conversation_sid or 'unknown'), _SAFE.sub('', media_sid) + ext)


def store_message_media(message, chat_service_sid, items):
    """
    Records and downloads the media of one message. Idempotent per media_sid; a failed download keeps
    the row with download_error so the backfill command can retry it. Returns the TwilioMessageMedia rows.
    """
    from mysite.models import TwilioMessageMedia

    rows = []
    for item in items:
        media, _ = TwilioMessageMedia.objects.get_or_create(
            media_sid=item['sid'],
            defaults={
                'message': message,
                'content_type': item['content_type'],
                'filename': item['filename'][:255],
                'size': item['size'],
            },
        )
        rows.append(media)
        if media.is_downloaded and os.path.exists(os.path.join(media_root(), media.file_path)):
            continue
        if not chat_service_sid:
            media.download_error = 'missing ChatServiceSid'
            media.save(update_fields=['download_error', 'updated_at'])
            continue
        try:
            content = download_media(chat_service_sid, media.media_sid)
            rel = _relative_path(message.conversation_sid, media.media_sid, media.content_type)
            full = os.path.join(media_root(), rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'wb') as fh:
                fh.write(content)
            media.file_path = rel
            media.size = media.size or len(content)
            media.download_error = ''
            media.save(update_fields=['file_path', 'size', 'download_error', 'updated_at'])
            log_info(f"Stored media {media.media_sid} for message {message.message_sid}", category='sms')
        except Exception as e:
            media.download_error = str(e)[:1000]
            media.save(update_fields=['download_error', 'updated_at'])
            log_error(e, f"Error downloading media {media.media_sid}", source='twilio')
    return rows


def fetch_media_for_existing_message(message, client=None, chat_service_sid=None):
    """
    Backfill: asks Twilio which media a stored message has and downloads it.
    Returns the TwilioMessageMedia rows ([] when the message has no media).
    """
    if client is None:
        from mysite.views.messaging import get_twilio_client
        client = get_twilio_client()
    conv = client.conversations.v1.conversations(message.conversation_sid)
    if not chat_service_sid:
        chat_service_sid = conv.fetch().chat_service_sid
    remote = conv.messages(message.message_sid).fetch()
    items = [_normalize_item(i) for i in (remote.media or []) if isinstance(i, dict)]
    items = [i for i in items if i['sid']]
    if not items:
        return []
    return store_message_media(message, chat_service_sid, items)


def file_path_of(media):
    """Absolute, traversal-safe path of a downloaded media file, or None."""
    if not media.file_path:
        return None
    root = os.path.realpath(media_root())
    full = os.path.realpath(os.path.join(root, media.file_path))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    return full


def image_for_model(media):
    """
    {'media_type': 'image/jpeg', 'data': <base64>} for the Claude input, or None when the file is not an
    image or can't be decoded (e.g. HEIC without a decoder). Downscaled to MODEL_MAX_EDGE, re-encoded as JPEG.
    """
    if not media.is_image:
        return None
    path = file_path_of(media)
    if not path:
        return None
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.thumbnail((MODEL_MAX_EDGE, MODEL_MAX_EDGE))
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
        return {'media_type': 'image/jpeg', 'data': base64.b64encode(buf.getvalue()).decode('ascii')}
    except Exception as e:
        log_warning(f"Could not prepare media {media.media_sid} for the agent: {e}", category='sms')
        return None
