"""
Merged view of a tenant's chats. Some tenants have more than one Twilio conversation (contract re-sends,
Twilio-made regroups and 1:1 threads, see data/tenant_multiple_conversations_report.md). The database keeps
them separate; this module only works out, at read time, which conversations belong to the same tenant.

- tenant of a conversation: its booking's tenant if that tenant wrote there (or nobody known wrote there),
  otherwise the known tenant (User with that phone) who wrote the most messages in it
- main conversation of a group: the one where the tenant sent their last message (if the tenant never wrote,
  the one with the latest message)

The chat list/page show one merged timeline, the AI agent reads the merged history, and notifications that are
not a reply to a message go to the main conversation. Replies always go to the conversation the message came from.
"""
import time
from collections import defaultdict
from datetime import datetime, timezone as dt_timezone

_cache = {'at': 0.0, 'data': None}
CACHE_SECONDS = 30
_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def _compute():
    from django.db.models import Count, Max

    from mysite.models import TwilioConversation, TwilioMessage, User
    from mysite.views.messaging import RESERVED_PHONES, get_manager_phones

    staff = set(get_manager_phones()) | set(RESERVED_PHONES)
    # conv_id -> author -> (count, last timestamp); KB-UPDATE rows are CRM notes, not chat
    stats = defaultdict(dict)
    for row in (TwilioMessage.objects.exclude(message_sid__startswith='KB-UPDATE-')
                .values('conversation_id', 'author').annotate(n=Count('id'), last=Max('message_timestamp'))):
        stats[row['conversation_id']][(row['author'] or '').strip()] = (row['n'], row['last'])

    phones = {a for authors in stats.values() for a in authors if a.startswith('+') and a not in staff}
    users = {u['phone']: u for u in User.objects.filter(phone__in=phones).values('id', 'phone', 'full_name')}
    convs = {c['id']: c for c in TwilioConversation.objects.values(
        'id', 'conversation_sid', 'friendly_name', 'apartment__name',
        'booking__tenant_id', 'booking__tenant__phone', 'booking__tenant__full_name')}

    tenant_of, tenant_info = {}, {}
    for cid, c in convs.items():
        writers = {}
        for author, (n, _last) in stats.get(cid, {}).items():
            user = users.get(author)
            if user:
                writers[user['id']] = writers.get(user['id'], 0) + n
                tenant_info[user['id']] = (user['full_name'], user['phone'])
        booked = c['booking__tenant_id']
        if booked and c['booking__tenant__phone'] in staff:
            booked = None   # placeholder tenant records with a staff phone never own a chat
        if booked and (booked in writers or not writers):
            tenant_of[cid] = booked
            tenant_info.setdefault(booked, (c['booking__tenant__full_name'], c['booking__tenant__phone']))
        elif writers:
            tenant_of[cid] = max(writers, key=writers.get)

    members = defaultdict(list)
    for cid, tid in tenant_of.items():
        members[tid].append(cid)

    by_conv = {}
    for tid, cids in members.items():
        if len(cids) < 2:
            continue
        name, phone = tenant_info.get(tid, (None, None))
        rows = []
        for cid in cids:
            authors = stats.get(cid, {})
            last = max((v[1] for v in authors.values()), default=None)
            tenant_last = authors.get(phone, (0, None))[1] if phone else None
            c = convs[cid]
            rows.append({
                'id': cid, 'sid': c['conversation_sid'], 'name': (c['friendly_name'] or '').strip(),
                'apartment': c['apartment__name'], 'last': last, 'tenant_last': tenant_last,
                'messages': sum(v[0] for v in authors.values()),
            })
        rows.sort(key=lambda r: (r['tenant_last'] or _EPOCH, r['last'] or _EPOCH), reverse=True)
        main = rows[0]
        rows = [main] + sorted(rows[1:], key=lambda r: r['last'] or _EPOCH, reverse=True)
        for r in rows:
            r['is_main'] = r is main
        group = {'tenant_id': tid, 'tenant_name': name, 'tenant_phone': phone, 'conversations': rows,
                 'main_id': main['id'], 'main_sid': main['sid'], 'ids': [r['id'] for r in rows],
                 'sids': [r['sid'] for r in rows]}
        for cid in cids:
            by_conv[cid] = group
    return by_conv


def _groups(fresh=False):
    now = time.monotonic()
    if fresh or _cache['data'] is None or now - _cache['at'] > CACHE_SECONDS:
        _cache['data'] = _compute()
        _cache['at'] = now
    return _cache['data']


def _conv_id(conversation):
    from mysite.models import TwilioConversation
    if isinstance(conversation, TwilioConversation):
        return conversation.id
    if isinstance(conversation, int):
        return conversation
    return TwilioConversation.objects.filter(conversation_sid=conversation).values_list('id', flat=True).first()


def group_for(conversation, fresh=False):
    """The tenant's group (dict) when this conversation's tenant has 2+ chats, else None.
    conversation: TwilioConversation, its id, or its sid. Never raises."""
    try:
        return _groups(fresh).get(_conv_id(conversation))
    except Exception:
        return None


def group_ids(conversation, fresh=False):
    """Conversation ids to show together with this one (just its own id when it is not merged)."""
    group = group_for(conversation, fresh)
    return group['ids'] if group else [_conv_id(conversation)]


def group_sids(conversation_sid, fresh=False):
    group = group_for(conversation_sid, fresh)
    return group['sids'] if group else [conversation_sid]


def main_sid(conversation_sid, fresh=True):
    """Where a notification for this tenant should go: the chat the tenant wrote in last."""
    group = group_for(conversation_sid, fresh)
    return group['main_sid'] if group else conversation_sid


def summary_line(conversation_sid, fresh=True):
    """One line for alerts, or None: 'Tenant has 3 chats: #194 (main, ...) · #136 ... · this message came in #136'."""
    group = group_for(conversation_sid, fresh)
    if not group:
        return None
    parts = []
    for r in group['conversations']:
        when = r['tenant_last'] or r['last']
        tag = 'main - tenant wrote here last' if r['is_main'] else ''
        parts.append(f"#{r['id']}" + (f" {r['apartment']}" if r['apartment'] else '')
                     + (f" ({tag})" if tag else '') + (f" last {when:%m-%d}" if when else ''))
    here = next((r['id'] for r in group['conversations'] if r['sid'] == conversation_sid), None)
    return f"Tenant has {len(parts)} chats: " + " · ".join(parts) + (f". This one is #{here}." if here else '')
