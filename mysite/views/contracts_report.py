from django.shortcuts import redirect
from django.db.models import Q
from ..models import Booking, Payment
from ..decorators import user_has_role
from .booking_report import get_google_sheets_service, share_document_with_user
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

# Calendar holds, not real guests
PLACEHOLDER_TENANTS = {'Blocked', 'Problem Booking', 'Not Availabale', 'Pending'}
# Apartments and guests whose name matches this are test data
TEST_NAMES_REGEX = r'test|sandbox'
# A booking of the same guest that starts within this gap after the previous one ends
# is an extension of the same contract, also when the guest moved to another apartment
EXTENSION_GAP = timedelta(days=9)
# A move to another apartment may start this much before the old booking ends; a bigger
# overlap is a second apartment rented at the same time, not a move
MOVE_OVERLAP = timedelta(days=1)
# Out payments that give guest money back (the rest of the Out rows on a booking are our expenses)
DEPOSIT_REFUND_TYPES = ('Damage Deposit', 'Hold Deposit')

HEADERS = [
    'Apartment', 'Apartment Type', 'Start', 'End', 'Days', 'Months', 'Guest', 'Source',
    'Total Merged', 'Total Cash', 'Total Paid', 'Booking ID', 'Extension',
]
MONEY_COLUMNS = (HEADERS.index('Total Merged'), HEADERS.index('Total Paid') + 1)
EXTENSION_ROW_COLOR = {'red': 1.0, 'green': 0.95, 'blue': 0.7}


@user_has_role('Admin', 'Manager')
def contracts_report(request):
    referer_url = request.META.get('HTTP_REFERER', '/')
    try:
        report_start_date = request.GET.get('report_start_date', None)
        report_end_date = request.GET.get('report_end_date', None)
        if not (report_start_date and report_end_date):
            return redirect(referer_url)

        start_date = datetime.strptime(report_start_date, "%B %d %Y").date()
        end_date = datetime.strptime(report_end_date, "%B %d %Y").date()

        bookings = Booking.objects.exclude(status__in=['Cancelled', 'Blocked']).filter(
            tenant__isnull=False, apartment__isnull=False,
        ).exclude(tenant__full_name__in=PLACEHOLDER_TENANTS).exclude(
            apartment__name__iregex=TEST_NAMES_REGEX
        ).exclude(tenant__full_name__iregex=TEST_NAMES_REGEX)
        if request.user.role == 'Manager':
            bookings = bookings.filter(apartment__managers=request.user)

        # Chain the whole history first, so extensions outside the period stay in their contract
        contracts = [
            c for c in build_contracts(bookings.select_related('tenant', 'apartment'))
            if start_date <= c['start_date'] <= end_date
        ]
        logger.info(f'Contracts report {start_date} - {end_date}: {len(contracts)} contracts')
        if not contracts:
            return redirect(referer_url)

        add_paid_totals(contracts)
        report_url = generate_contracts_excel(contracts, report_start_date, report_end_date)
        logger.info(f'Contracts report created {report_url}')
        return redirect(report_url or referer_url)
    except Exception as e:
        logger.exception(f"Error: Generating Contracts Report Error, {str(e)}")
        return redirect(referer_url)


def build_contracts(bookings):
    """Chain each guest's bookings that follow each other into one contract.

    A booking extends the guest's earlier booking that ends within EXTENSION_GAP before it starts:
    in the same apartment any overlap is fine (an edited or doubled booking), in another apartment
    (a move) it may start at most MOVE_OVERLAP before the old one ends. Each booking is extended
    at most once; the same apartment and then the smallest gap win. Every booking gets `extends`
    (the booking it continues, or None) and `gap_days`.
    """
    by_guest = defaultdict(list)
    for booking in bookings:
        by_guest[booking.tenant_id].append(booking)

    contracts = []
    for guest_bookings in by_guest.values():
        guest_bookings.sort(key=lambda b: (b.start_date, b.end_date, b.id))
        extended = set()
        chain_of = {}
        for index, booking in enumerate(guest_bookings):
            candidates = []
            for previous in guest_bookings[:index]:
                if previous.id in extended or booking.start_date > previous.end_date + EXTENSION_GAP:
                    continue
                same_apartment = previous.apartment_id == booking.apartment_id
                if not same_apartment and booking.start_date < previous.end_date - MOVE_OVERLAP:
                    continue
                gap = (booking.start_date - previous.end_date).days
                candidates.append((not same_apartment, abs(gap), previous, gap))

            if candidates:
                _, _, previous, gap = min(candidates, key=lambda c: (c[0], c[1]))
                booking.extends, booking.gap_days = previous, gap
                extended.add(previous.id)
                contract = chain_of[previous.id]
                contract['bookings'].append(booking)
            else:
                booking.extends = None
                contract = {'start_date': booking.start_date, 'bookings': [booking]}
                contracts.append(contract)
            chain_of[booking.id] = contract

    # Newest first; the bookings of one contract stay together in date order
    contracts.sort(key=lambda c: (c['start_date'], c['bookings'][0].apartment.name), reverse=True)
    return contracts


def add_paid_totals(contracts):
    """Per booking: guest In payments minus deposit refunds, split into Merged and Completed-Cash rows."""
    booking_ids = [b.id for c in contracts for b in c['bookings']]
    payments = Payment.objects.filter(booking_id__in=booking_ids).filter(
        Q(payment_status='Merged') | Q(payment_status='Completed', payment_method__name='Cash')
    ).filter(
        Q(payment_type__type='In') | Q(payment_type__type='Out', payment_type__name__in=DEPOSIT_REFUND_TYPES)
    ).values_list('booking_id', 'amount', 'payment_type__type', 'payment_status')

    merged_by_booking = defaultdict(Decimal)
    cash_by_booking = defaultdict(Decimal)
    for booking_id, amount, direction, status in payments:
        totals = merged_by_booking if status == 'Merged' else cash_by_booking
        totals[booking_id] += amount if direction == 'In' else -amount

    for contract in contracts:
        for booking in contract['bookings']:
            booking.total_merged = merged_by_booking[booking.id]
            booking.total_cash = cash_by_booking[booking.id]


def apartment_type_label(apartment):
    return f"{apartment.bedrooms}BR" if apartment.bedrooms else ''


def extension_label(booking):
    if not booking.extends:
        return ''
    gap = booking.gap_days
    gap_text = f'overlap {-gap} days' if gap < 0 else f"gap {gap} day{'' if gap == 1 else 's'}"
    moved = ''
    if booking.extends.apartment_id != booking.apartment_id:
        moved = f', moved from {booking.extends.apartment.name}'
    return f'Extension of #{booking.extends.id}{moved} ({gap_text})'


def booking_row(booking):
    days = (booking.end_date - booking.start_date).days
    return [
        booking.apartment.name,
        apartment_type_label(booking.apartment),
        booking.start_date.isoformat(),
        booking.end_date.isoformat(),
        days,
        round(days / 30.44, 1),
        booking.tenant.full_name,
        booking.source,
        float(booking.total_merged),
        float(booking.total_cash),
        float(booking.total_merged + booking.total_cash),
        f'#{booking.id}',
        extension_label(booking),
    ]


def generate_contracts_excel(contracts, start_date, end_date):
    sheets_service, drive_service = get_google_sheets_service()

    spreadsheet = sheets_service.create(body={
        'properties': {'title': f"Contracts Report: {start_date} - {end_date}"},
        'sheets': [{'properties': {'title': 'Contracts Report'}}],
    }).execute()
    spreadsheet_id = spreadsheet.get('spreadsheetId')
    sheet_id = spreadsheet['sheets'][0]['properties']['sheetId']

    values = [HEADERS]
    extension_row_indexes = []
    for contract in contracts:
        for booking in contract['bookings']:
            if booking.extends:
                extension_row_indexes.append(len(values))
            values.append(booking_row(booking))

    sheets_service.values().update(
        spreadsheetId=spreadsheet_id,
        range='Contracts Report!A1',
        valueInputOption='USER_ENTERED',
        body={'values': values},
    ).execute()

    extension_formats = [
        {
            'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': index, 'endRowIndex': index + 1,
                          'startColumnIndex': 0, 'endColumnIndex': len(HEADERS)},
                'cell': {'userEnteredFormat': {'backgroundColor': EXTENSION_ROW_COLOR}},
                'fields': 'userEnteredFormat.backgroundColor',
            }
        }
        for index in extension_row_indexes
    ]

    sheets_service.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': extension_formats + [
        {
            'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
                'cell': {'userEnteredFormat': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.85, 'green': 0.85, 'blue': 0.85},
                }},
                'fields': 'userEnteredFormat(textFormat,backgroundColor)',
            }
        },
        {
            'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': 1,
                          'startColumnIndex': MONEY_COLUMNS[0], 'endColumnIndex': MONEY_COLUMNS[1]},
                'cell': {'userEnteredFormat': {'numberFormat': {'type': 'CURRENCY', 'pattern': '$#,##0.00'}}},
                'fields': 'userEnteredFormat.numberFormat',
            }
        },
        {
            'setBasicFilter': {
                'filter': {'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endColumnIndex': len(HEADERS)}}
            }
        },
        {
            'autoResizeDimensions': {
                'dimensions': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': len(HEADERS)}
            }
        },
    ]}).execute()

    share_document_with_user(drive_service, spreadsheet_id)
    return f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'
