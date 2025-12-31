import json

from django.http import JsonResponse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from ..decorators import user_has_role
from ..models import Apartment, CalendarNote
from django.db import models


@user_has_role('Admin', 'Manager')
@require_http_methods(["POST"])
def create_calendar_note(request):
    try:
        payload = json.loads((request.body or b'{}').decode('utf-8'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body'}, status=400)

    start_date = parse_date(payload.get('start_date') or '')
    end_date = parse_date(payload.get('end_date') or '')
    note = (payload.get('note') or '').strip()
    apartment_id = payload.get('apartment_id')
    scope = (payload.get('scope') or '').strip()  # backwards compatibility

    if not start_date or not end_date:
        return JsonResponse({'ok': False, 'error': 'start_date and end_date are required (YYYY-MM-DD)'}, status=400)

    if start_date > end_date:
        return JsonResponse({'ok': False, 'error': 'start_date must be <= end_date'}, status=400)

    if not note:
        return JsonResponse({'ok': False, 'error': 'note is required'}, status=400)

    apartment = None
    if apartment_id:
        apartment = Apartment.objects.filter(id=apartment_id).first()
        if not apartment:
            return JsonResponse({'ok': False, 'error': 'Apartment not found'}, status=404)

        if request.user.role == 'Manager':
            if not apartment.managers.filter(id=request.user.id).exists():
                return JsonResponse({'ok': False, 'error': 'You do not have access to this apartment'}, status=403)
    else:
        # Backwards compatibility: if scope explicitly says apartment but apartment_id is missing, keep old validation
        if scope == 'apartment':
            return JsonResponse({'ok': False, 'error': 'apartment_id is required for scope=apartment'}, status=400)
        apartment = None

    calendar_note = CalendarNote.objects.create(
        apartment=apartment,
        start_date=start_date,
        end_date=end_date,
        note=note,
    )

    return JsonResponse(
        {
            'ok': True,
            'note': {
                'id': calendar_note.id,
                'scope': 'apartment' if calendar_note.apartment_id else 'global',
                'apartment_id': calendar_note.apartment_id,
                'start_date': calendar_note.start_date.isoformat(),
                'end_date': calendar_note.end_date.isoformat(),
                'note': calendar_note.note,
            },
        },
        status=201,
    )


@user_has_role('Admin', 'Manager')
@require_http_methods(["GET"])
def list_calendar_notes(request):
    """
    List notes that apply to a specific date, including global notes and (optionally) notes for an apartment.
    Query params:
      - date=YYYY-MM-DD (required)
      - apartment_id=<id> (optional; when provided includes that apartment's notes)
    """
    date_str = request.GET.get('date') or ''
    day = parse_date(date_str)
    if not day:
        return JsonResponse({'ok': False, 'error': 'date is required (YYYY-MM-DD)'}, status=400)

    apartment_id = request.GET.get('apartment_id')
    apartment = None

    qs = CalendarNote.objects.filter(start_date__lte=day, end_date__gte=day)

    if apartment_id:
        apartment = Apartment.objects.filter(id=apartment_id).first()
        if not apartment:
            return JsonResponse({'ok': False, 'error': 'Apartment not found'}, status=404)

        if request.user.role == 'Manager':
            if not apartment.managers.filter(id=request.user.id).exists():
                return JsonResponse({'ok': False, 'error': 'You do not have access to this apartment'}, status=403)

        qs = qs.filter(models.Q(apartment__isnull=True) | models.Q(apartment=apartment))
    else:
        qs = qs.filter(apartment__isnull=True)

    notes = []
    for n in qs.select_related('apartment').order_by('apartment__isnull', '-start_date', '-id'):
        notes.append({
            'id': n.id,
            'scope': 'apartment' if n.apartment_id else 'global',
            'apartment_id': n.apartment_id,
            'apartment_name': n.apartment.name if n.apartment else None,
            'start_date': n.start_date.isoformat(),
            'end_date': n.end_date.isoformat(),
            'note': n.note,
        })

    return JsonResponse({'ok': True, 'notes': notes})


@user_has_role('Admin', 'Manager')
@require_http_methods(["POST"])
def update_calendar_note(request, note_id: int):
    calendar_note = CalendarNote.objects.select_related('apartment').filter(id=note_id).first()
    if not calendar_note:
        return JsonResponse({'ok': False, 'error': 'Note not found'}, status=404)

    if request.user.role == 'Manager' and calendar_note.apartment_id:
        if not calendar_note.apartment.managers.filter(id=request.user.id).exists():
            return JsonResponse({'ok': False, 'error': 'You do not have access to this apartment'}, status=403)

    try:
        payload = json.loads((request.body or b'{}').decode('utf-8'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body'}, status=400)

    start_date = parse_date(payload.get('start_date') or '')
    end_date = parse_date(payload.get('end_date') or '')
    note = (payload.get('note') or '').strip()
    apartment_id = payload.get('apartment_id')

    if not start_date or not end_date:
        return JsonResponse({'ok': False, 'error': 'start_date and end_date are required (YYYY-MM-DD)'}, status=400)

    if start_date > end_date:
        return JsonResponse({'ok': False, 'error': 'start_date must be <= end_date'}, status=400)

    if not note:
        return JsonResponse({'ok': False, 'error': 'note is required'}, status=400)

    apartment = None
    if apartment_id:
        apartment = Apartment.objects.filter(id=apartment_id).first()
        if not apartment:
            return JsonResponse({'ok': False, 'error': 'Apartment not found'}, status=404)
        if request.user.role == 'Manager':
            if not apartment.managers.filter(id=request.user.id).exists():
                return JsonResponse({'ok': False, 'error': 'You do not have access to this apartment'}, status=403)

    calendar_note.apartment = apartment
    calendar_note.start_date = start_date
    calendar_note.end_date = end_date
    calendar_note.note = note
    calendar_note.save()

    return JsonResponse({
        'ok': True,
        'note': {
            'id': calendar_note.id,
            'scope': 'apartment' if calendar_note.apartment_id else 'global',
            'apartment_id': calendar_note.apartment_id,
            'start_date': calendar_note.start_date.isoformat(),
            'end_date': calendar_note.end_date.isoformat(),
            'note': calendar_note.note,
        }
    })


@user_has_role('Admin', 'Manager')
@require_http_methods(["POST"])
def delete_calendar_note(request, note_id: int):
    calendar_note = CalendarNote.objects.select_related('apartment').filter(id=note_id).first()
    if not calendar_note:
        return JsonResponse({'ok': False, 'error': 'Note not found'}, status=404)

    if request.user.role == 'Manager' and calendar_note.apartment_id:
        if not calendar_note.apartment.managers.filter(id=request.user.id).exists():
            return JsonResponse({'ok': False, 'error': 'You do not have access to this apartment'}, status=403)

    calendar_note.delete()
    return JsonResponse({'ok': True})


