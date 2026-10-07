"""
CRM record changes the AI may PROPOSE on an alert (action CRM_CHANGE, block 🗂, button "🗂 Apply in CRM").

The AI sees CRM data in its context (parking, payments, the booking). When a record is missing or wrong and one of
the changes below fits, it proposes that change; nothing is written until a manager presses the button. Only the
changes listed in CHANGES exist - the AI can not write anything else to the CRM. To allow one more kind of record
change, add one entry to CHANGES: `check` says whether it is possible now and how the alert words it, `apply` does it.
"""
from mysite.ai_agent.actions import ActionError


def _dates(booking):
    return f"{booking.start_date:%-d %b} – {booking.end_date:%-d %b %Y}" if booking.start_date and booking.end_date else 'no dates'


def apartment_spots(apartment):
    """The apartment's own parking spots (Parking rows of its building + apartment number)."""
    from mysite.models import Parking
    apartment_n = (getattr(apartment, 'apartment_n', None) or '').strip()
    building_n = (getattr(apartment, 'building_n', None) or '').strip()
    if not apartment_n or not building_n:
        return []
    return list(Parking.objects.filter(building=building_n, associated_room=apartment_n).order_by('id'))


def taken_by(spot, booking):
    """The other booking's ParkingBooking that holds this spot during the booking's dates, or None."""
    from mysite.models import ParkingBooking
    if not (booking and booking.start_date and booking.end_date):
        return None
    return ParkingBooking.objects.filter(parking=spot, start_date__lt=booking.end_date, end_date__gt=booking.start_date) \
        .exclude(booking=booking).exclude(booking__status='Cancelled').order_by('start_date').first()


def _find_spot(booking, apartment, number):
    from mysite.models import Parking
    number = str(number or '').lstrip('#').strip()
    if not number:
        raise ActionError("no parking spot number given")
    from mysite.models import ParkingBooking
    held = ParkingBooking.objects.filter(booking=booking, parking__number=number).select_related('parking').first()
    if held:
        return held.parking   # already booked for this booking (also a spot of another building)
    own = [s for s in apartment_spots(apartment) if str(s.number).strip() == number]
    if own:
        return own[0]
    building = (getattr(apartment, 'building_n', None) or '').strip()
    spot = Parking.objects.filter(number=number, building=building).order_by('id').first() if building else None
    if not spot:
        raise ActionError(f"the CRM has no parking spot #{number} for this apartment or its building")
    return spot


# -- book_parking: create the ParkingBooking of this booking -----------------------------------------------------------
def _check_book_parking(booking, apartment, action):
    from mysite.models import ParkingBooking
    spot = _find_spot(booking, apartment, action.get('spot'))
    if ParkingBooking.objects.filter(booking=booking, parking=spot).exists():
        raise ActionError(f"spot #{spot.number} is already booked for this booking")
    other = taken_by(spot, booking)
    if other:
        raise ActionError(f"spot #{spot.number} is taken {other.start_date} – {other.end_date} by another booking")
    return f"book parking spot #{spot.number} for this booking ({_dates(booking)})"


def _apply_book_parking(booking, apartment, action, author):
    from mysite.models import ParkingBooking
    spot = _find_spot(booking, apartment, action.get('spot'))
    _check_book_parking(booking, apartment, action)   # the CRM may have changed since the alert was written
    has_car = booking.is_rent_car or booking.car_model or booking.car_price or booking.car_rent_days
    status = 'Unavailable' if booking.status == 'Blocked' else 'Booked' if has_car else 'No Car'
    row = ParkingBooking(parking=spot, booking=booking, status=status, start_date=booking.start_date,
                         end_date=booking.end_date, apartment=booking.apartment, notes=f"AI alert, approved by {author}"[:255])
    row.save(updated_by=f"{author} (AI alert)")
    return f"parking spot #{spot.number} booked for the booking ({_dates(booking)}), ParkingBooking #{row.id}"


# -- cancel_parking: delete the ParkingBooking(s) of this booking ------------------------------------------------------
def _check_cancel_parking(booking, apartment, action):
    from mysite.models import ParkingBooking
    rows = list(ParkingBooking.objects.filter(booking=booking).select_related('parking'))
    if not rows:
        raise ActionError("no parking is booked for this booking")
    spots = ", ".join(f"#{r.parking.number}" for r in rows if r.parking) or 'the spot'
    return f"cancel the parking of this booking (spot {spots}) – the tenant does not need it"


def _apply_cancel_parking(booking, apartment, action, author):
    from mysite.models import ParkingBooking
    label = _check_cancel_parking(booking, apartment, action)
    deleted = 0
    for row in ParkingBooking.objects.filter(booking=booking):
        row.delete()
        deleted += 1
    return f"{deleted} parking booking(s) of the booking deleted ({label})"


CHANGES = {
    'book_parking': {'check': _check_book_parking, 'apply': _apply_book_parking,
                     'for_ai': 'book_parking {"spot": "<number>"} - create the parking record of this booking on a spot '
                               'that the PARKING block shows as free for the booking\'s dates'},
    'cancel_parking': {'check': _check_cancel_parking, 'apply': _apply_cancel_parking,
                       'for_ai': 'cancel_parking {} - delete the parking record of this booking, when the tenant wrote that '
                                 'they have no car or do not need the spot'},
}


def check(booking, apartment, action):
    """The words for the alert ("book parking spot #14 for this booking (6 Oct – 30 Oct)"). ActionError when the change
    is unknown or not possible in the CRM as it is now."""
    change = CHANGES.get(str(action.get('change') or ''))
    if not change:
        raise ActionError(f"unknown CRM change '{action.get('change')}'")
    if not booking:
        raise ActionError("this chat has no booking in the CRM")
    return change['check'](booking, apartment, action)


def carry_out(booking, apartment, action, author):
    """Writes the change to the CRM. Returns a detail line. ActionError when it is no longer possible."""
    change = CHANGES.get(str(action.get('change') or ''))
    if not change:
        raise ActionError(f"unknown CRM change '{action.get('change')}'")
    if not booking:
        raise ActionError("this chat has no booking in the CRM")
    return change['apply'](booking, apartment, action, author)


def for_prompt():
    return "\n".join(f"  - {c['for_ai']}" for c in CHANGES.values())
