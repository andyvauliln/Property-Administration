"""
The story sandbox: one continuous test chat on an apartment of its own ("Sandbox Test"), with REAL CRM rows - booking,
payments, parking, knowledge page - created from the `story:` block of sandbox_cases.yaml. The cases are the chapters
of one stay, in time order; nothing is wiped between them. After every chapter the sandbox rows are saved (snapshot),
so "rerun" and "run <chapter>" restore the state the chapter started from.

The AI's context is built by the production code from these rows: the test injects nothing into it. What the sandbox
still replaces is only what would reach somebody (SMS, calls, ClickUp list, Telegram group) - see world.py - and the
contract text (there is no DocuSeal submission for the sandbox booking).
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from django.core import serializers
from django.db import transaction

from mysite.ai_agent.sandbox_test.clock import real_now
from mysite.management.commands.ai_agent_sandbox import SANDBOX_NOTE, SANDBOX_SID, SANDBOX_TENANT_EMAIL, SANDBOX_TENANT_PHONE

STORY_APARTMENT = "Sandbox Test"          # must contain "test": the sandbox refuses any other apartment
SANDBOX_SID_2 = "CHSANDBOXAIAGENT00000000000000002"
SIDS = (SANDBOX_SID, SANDBOX_SID_2)
CONTRACT_ID = "SANDBOX-CONTRACT"          # booking.contract_id of the story booking (the text comes from the story file)
SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / 'logs' / 'sandbox_tests' / 'story_state'
CONTRACT_FILE = SNAPSHOT_DIR / 'contract.txt'   # read by mysite/ai_agent/contract.py for the story booking


class StoryError(Exception):
    """The story could not be set up: the run stops (running chapters on a half-written sandbox proves nothing)."""


def ensure_apartment():
    """The story apartment, created once (no model save(): no side effects). Nothing else on the site uses it."""
    from mysite.models import Apartment
    apartment = Apartment.objects.filter(name=STORY_APARTMENT).first()
    if apartment is None:
        Apartment.objects.bulk_create([Apartment(
            name=STORY_APARTMENT, building_n='105', street='Wilson Ave', apartment_n='2B', state='FL', city='Miami', zip_index='33101',
            bedrooms=1, bathrooms=1, apartment_type='In Management', status='Available',
            notes="AI SANDBOX STORY - test apartment of the sandbox test runner (manage.py ai_agent_sandbox_test). Safe to delete.",
            knowledge_base='', ai_group_chat_enabled=False,
        )])
        apartment = Apartment.objects.get(name=STORY_APARTMENT)
    return apartment


def _date(value, shift):
    if isinstance(value, datetime):
        return (value + shift).date()
    if isinstance(value, date):
        return value + shift
    return (datetime.strptime(str(value)[:10], '%Y-%m-%d') + shift).date()


def _payment_type(name):
    from mysite.models import PaymenType
    row = PaymenType.objects.filter(name__iexact=str(name or 'Rent').strip()).order_by('id').first()
    if row is None:
        row = PaymenType.objects.filter(name__icontains=str(name or 'Rent').strip()).order_by('id').first()
    if row is None:
        raise ValueError(f"story: no payment type named {name!r} in the CRM")
    return row


def wipe(world):
    """Everything the story wrote: chat rows, AI rows, payments, parking, the second chat. Keeps the apartment, the
    tenant, the booking row and the first chat (they are re-filled by seed)."""
    from mysite.models import (AIAfterHoursAck, AIAlertCall, AICaseNote, AIEvent, AIFollowUp, AIIssue, AIRun, Parking, ParkingBooking,
                               Payment, PendingOutboundMessage, TwilioConversation, TwilioMessage)
    counts = {}
    for model in (TwilioMessage, AIEvent, AIFollowUp, AICaseNote, AIIssue, AIRun, AIAlertCall, PendingOutboundMessage):
        counts[model.__name__] = model.objects.filter(conversation_sid__in=SIDS).delete()[0]
    AIAfterHoursAck.objects.filter(conversation_sid__in=SIDS).delete()
    TwilioConversation.objects.filter(conversation_sid=SANDBOX_SID_2).delete()
    counts['Payment'] = Payment.objects.filter(booking=world.booking).delete()[0]
    counts['ParkingBooking'] = ParkingBooking.objects.filter(booking=world.booking).delete()[0] \
        + ParkingBooking.objects.filter(apartment=world.apartment).delete()[0]
    counts['Parking'] = _spots(world).delete()[0]
    return {k: v for k, v in counts.items() if v}


def _spots(world):
    """The story apartment's own spots (Parking rows are per building + room, not per apartment)."""
    from mysite.models import Parking
    return Parking.objects.filter(building=world.apartment.building_n, associated_room=world.apartment.apartment_n)


def seed(world, story, shift):
    """Writes the story's rows (dates moved by `shift`, like the case times). Returns a short text."""
    from mysite.models import Apartment, Booking, Parking, ParkingBooking, Payment, TwilioConversation, User
    tenant = story.get('tenant') or {}
    booking = story.get('booking') or {}
    with transaction.atomic():
        User.objects.filter(id=world.tenant.id).update(full_name=tenant.get('name') or 'Sandbox Tenant',
                                                       phone=SANDBOX_TENANT_PHONE)
        start = _date(booking.get('start'), shift) if booking.get('start') else date.today() - timedelta(days=2)
        end = _date(booking.get('end'), shift) if booking.get('end') else start + timedelta(days=60)
        Booking.objects.filter(id=world.booking.id).update(
            start_date=start, end_date=end, status=booking.get('status') or 'Confirmed', apartment=world.apartment,
            tenants_n=booking.get('tenants') or 1, contract_id=CONTRACT_ID if story.get('contract') else None,
            contract_send_status='signed' if (story.get('contract') or {}).get('signed', True) and story.get('contract') else None,
            notes=SANDBOX_NOTE, other_tenants=booking.get('other_tenants') or None)
        TwilioConversation.objects.filter(conversation_sid=SANDBOX_SID).update(booking=world.booking, apartment=world.apartment)
        Apartment.objects.filter(id=world.apartment.id).update(knowledge_base=str(story.get('knowledge') or '').strip(),
                                                               ai_group_chat_enabled=False)
        rows = []
        for p in story.get('payments') or []:
            rows.append(Payment(
                booking=world.booking, payment_type=_payment_type(p.get('type')),
                amount=Decimal(str(p.get('amount') or 0)), payment_date=_date(p['date'], shift),
                payment_status=p.get('status') or 'Pending', notes=p.get('notes') or None, source='manual',
                created_by='sandbox story', last_updated_by='sandbox story'))
        if rows:
            Payment.objects.bulk_create(rows)
        for p in story.get('parking') or []:
            Parking.objects.bulk_create([Parking(number=str(p.get('spot') or ''), notes=p.get('notes') or None,
                                                 building=world.apartment.building_n, associated_room=world.apartment.apartment_n,
                                                 created_by='sandbox story')])
            spot = _spots(world).filter(number=str(p.get('spot') or '')).order_by('-id').first()
            if p.get('booked'):
                ParkingBooking.objects.bulk_create([ParkingBooking(
                    parking=spot, apartment=world.apartment, booking=world.booking, status=p.get('status') or 'Booked',
                    start_date=start, end_date=end, notes=p.get('booking_notes') or None, created_by='sandbox story')])
    world.booking.refresh_from_db()
    world.apartment.refresh_from_db()
    world.contract = story.get('contract')
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    CONTRACT_FILE.write_text(contract_text(world), encoding='utf-8')
    return (f"{tenant.get('name') or 'tenant'} · {start} → {end} · {len(story.get('payments') or [])} payment(s) · "
            f"{len(story.get('parking') or [])} parking spot(s) · knowledge page {len(str(story.get('knowledge') or ''))} chars"
            + (" · contract" if story.get('contract') else ""))


def apply_crm(world, changes, shift):
    """A chapter's `crm:` block: changes of the real rows before its trigger (a payment marked paid, the booking
    extended, a parking spot freed). Returns the lines of what was changed."""
    from mysite.models import Apartment, Booking, Parking, ParkingBooking, Payment
    done = []
    for change in (changes or {}).get('payments') or []:
        rows = Payment.objects.filter(booking=world.booking)
        if change.get('type'):
            rows = rows.filter(payment_type__name__iexact=str(change['type']))
        if change.get('date'):
            rows = rows.filter(payment_date=_date(change['date'], shift))
        if change.get('add'):
            add = change['add']
            Payment.objects.bulk_create([Payment(
                booking=world.booking, payment_type=_payment_type(add.get('type')),
                amount=Decimal(str(add.get('amount') or 0)), payment_date=_date(add['date'], shift),
                payment_status=add.get('status') or 'Pending', notes=add.get('notes') or None, source='manual',
                created_by='sandbox story', last_updated_by='sandbox story')])
            done.append(f"payment added: {add.get('type')} ${add.get('amount')} {_date(add['date'], shift)} {add.get('status') or 'Pending'}")
            continue
        fields = {}
        if change.get('status'):
            fields['payment_status'] = change['status']
        if change.get('amount') is not None:
            fields['amount'] = Decimal(str(change['amount']))
        if change.get('notes') is not None:
            fields['notes'] = change['notes']
        n = rows.update(**fields) if fields else 0
        done.append(f"{n} payment(s) {change.get('type') or ''} {change.get('date') or ''} → {fields}")
    booking = (changes or {}).get('booking') or {}
    if booking:
        fields = {}
        if booking.get('end'):
            fields['end_date'] = _date(booking['end'], shift)
        if booking.get('start'):
            fields['start_date'] = _date(booking['start'], shift)
        if booking.get('status'):
            fields['status'] = booking['status']
        Booking.objects.filter(id=world.booking.id).update(**fields)
        ParkingBooking.objects.filter(booking=world.booking).update(**{k: v for k, v in fields.items() if k in ('start_date', 'end_date')})
        world.booking.refresh_from_db()
        done.append(f"booking → {fields}")
    for change in (changes or {}).get('parking') or []:
        spot = _spots(world).filter(number=str(change.get('spot') or '')).first()
        if change.get('booked') is False:
            n = ParkingBooking.objects.filter(parking=spot).delete()[0]
            done.append(f"parking #{change.get('spot')}: {n} booking(s) removed")
        elif change.get('booked'):
            ParkingBooking.objects.bulk_create([ParkingBooking(
                parking=spot, apartment=world.apartment, booking=world.booking, status=change.get('status') or 'Booked',
                start_date=world.booking.start_date, end_date=world.booking.end_date, created_by='sandbox story')])
            done.append(f"parking #{change.get('spot')} booked for the story booking")
    if (changes or {}).get('knowledge') is not None:
        Apartment.objects.filter(id=world.apartment.id).update(knowledge_base=str(changes['knowledge']).strip())
        done.append("knowledge page replaced")
    return done


# ---------------------------------------------------------------------------
# Snapshots: the sandbox rows after a chapter, so a chapter can be run again from where it started
# ---------------------------------------------------------------------------
def _rows(world):
    from mysite.models import (AIAfterHoursAck, AIAlertCall, AICaseNote, AIEvent, AIFollowUp, AIIssue, AIRun, Apartment, Booking, Parking,
                               ParkingBooking, Payment, PendingOutboundMessage, TwilioConversation, TwilioMessage, User)
    # Order matters for the restore (foreign keys): parents first
    return [
        ('apartment', Apartment.objects.filter(id=world.apartment.id)),
        ('tenant', User.objects.filter(id=world.tenant.id)),
        ('booking', Booking.objects.filter(id=world.booking.id)),
        ('conversations', TwilioConversation.objects.filter(conversation_sid__in=SIDS)),
        ('messages', TwilioMessage.objects.filter(conversation_sid__in=SIDS)),
        ('payments', Payment.objects.filter(booking=world.booking)),
        ('parking', _spots(world)),
        ('parking_bookings', ParkingBooking.objects.filter(apartment=world.apartment)),
        ('issues', AIIssue.objects.filter(conversation_sid__in=SIDS)),
        ('events', AIEvent.objects.filter(conversation_sid__in=SIDS)),
        ('runs', AIRun.objects.filter(conversation_sid__in=SIDS)),
        ('followups', AIFollowUp.objects.filter(conversation_sid__in=SIDS)),
        ('notes', AICaseNote.objects.filter(conversation_sid__in=SIDS)),
        ('calls', AIAlertCall.objects.filter(conversation_sid__in=SIDS)),
        ('acks', AIAfterHoursAck.objects.filter(conversation_sid__in=SIDS)),
        ('pending', PendingOutboundMessage.objects.filter(conversation_sid__in=SIDS)),
    ]


def snapshot(world, name, extra=None):
    """Saves the sandbox rows and the in-memory sandbox state under `name`. Returns the file path."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    data = {'at': real_now().isoformat(), 'clock': world.clock.now().isoformat(), 'state': world.state, 'extra': extra or {},
            'shift_days': world.shift.days,
            'rows': {key: serializers.serialize('json', queryset) for key, queryset in _rows(world)}}
    path = SNAPSHOT_DIR / f"{name}.json"
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def restore(world, name):
    """Puts the sandbox rows back as they were after chapter `name` ('start' = right after the seed). Returns the
    snapshot's clock time, or None when there is no such snapshot."""
    path = SNAPSHOT_DIR / f"{name}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    with transaction.atomic():
        wipe(world)
        for key, _queryset in _rows(world):
            for obj in serializers.deserialize('json', data['rows'].get(key) or '[]'):
                obj.save()   # raw save: no model save() side effects
    world.state = data.get('state') or {'knowledge': [], 'rules': []}
    world._save_state()
    world.shift = timedelta(days=int(data.get('shift_days') or 0))
    world.booking.refresh_from_db()
    world.apartment.refresh_from_db()
    world.tenant.refresh_from_db()
    return datetime.fromisoformat(data['clock'])


def saved_shift():
    """(shift in days, clock) of the newest snapshot, or (0, None) when there is none."""
    newest = max(SNAPSHOT_DIR.glob('*.json'), key=lambda p: p.stat().st_mtime, default=None) if SNAPSHOT_DIR.exists() else None
    if newest is None:
        return 0, None
    data = json.loads(newest.read_text(encoding='utf-8'))
    return int(data.get('shift_days') or 0), datetime.fromisoformat(data['clock'])


def clear_snapshots():
    if SNAPSHOT_DIR.exists():
        for path in SNAPSHOT_DIR.glob('*.json'):
            path.unlink()


def snapshot_names():
    return sorted(p.stem for p in SNAPSHOT_DIR.glob('*.json')) if SNAPSHOT_DIR.exists() else []


def contract_text(world):
    """The story's contract as the agent's get_contract tool shows it (the sandbox booking has no DocuSeal submission)."""
    contract = getattr(world, 'contract', None) or {}
    if not contract:
        return "No contract on file for this booking (contract status in the CRM: none). Do not state any contract terms."
    booking = world.booking
    lines = [f"CONTRACT: {contract.get('name') or 'Residential lease'} (sandbox story contract)",
             f"STATUS: SIGNED by the tenant on {booking.start_date - timedelta(days=3)}" if contract.get('signed', True)
             else "STATUS: NOT SIGNED yet (tenant status: sent)",
             "FILLED-IN TERMS (as signed):"]
    for key, value in (contract.get('terms') or {}).items():
        lines.append(f"- {key}: {value}")
    if contract.get('text'):
        lines += ["", "CONTRACT TEXT:", str(contract['text']).strip()]
    return "\n".join(lines)
