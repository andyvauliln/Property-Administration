import json

from django.http import JsonResponse
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from ..decorators import user_has_role
from ..models import Apartment, CalendarNote


@user_has_role('Admin', 'Manager')
@require_POST
def create_calendar_note(request):
    try:
        payload = json.loads((request.body or b'{}').decode('utf-8'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body'}, status=400)

    start_date = parse_date(payload.get('start_date') or '')
    end_date = parse_date(payload.get('end_date') or '')
    note = (payload.get('note') or '').strip()
    scope = (payload.get('scope') or 'global').strip()
    apartment_id = payload.get('apartment_id')

    if not start_date or not end_date:
        return JsonResponse({'ok': False, 'error': 'start_date and end_date are required (YYYY-MM-DD)'}, status=400)

    if start_date > end_date:
        return JsonResponse({'ok': False, 'error': 'start_date must be <= end_date'}, status=400)

    if not note:
        return JsonResponse({'ok': False, 'error': 'note is required'}, status=400)

    apartment = None
    if scope == 'global':
        apartment = None
    elif scope == 'apartment':
        if not apartment_id:
            return JsonResponse({'ok': False, 'error': 'apartment_id is required for scope=apartment'}, status=400)

        apartment = Apartment.objects.filter(id=apartment_id).first()
        if not apartment:
            return JsonResponse({'ok': False, 'error': 'Apartment not found'}, status=404)

        if request.user.role == 'Manager':
            if not apartment.managers.filter(id=request.user.id).exists():
                return JsonResponse({'ok': False, 'error': 'You do not have access to this apartment'}, status=403)
    else:
        return JsonResponse({'ok': False, 'error': 'scope must be global or apartment'}, status=400)

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


