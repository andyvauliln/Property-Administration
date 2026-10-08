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
PLACEHOLDER_TENANTS = {'Blocked', 'Problem Booking', 'Not Availabale'}
# A booking that starts within this gap after the previous one of the same guest
# in the same apartment is an extension of the same contract
EXTENSION_GAP = timedelta(days=1)
# Out payments that give guest money back (the rest of the Out rows on a booking are our expenses)
DEPOSIT_REFUND_TYPES = ('Damage Deposit', 'Hold Deposit')

HEADERS = [
    'Apartment', 'Apartment Type', 'Contract Start', 'Contract End', 'Days', 'Months',
    'Guest', 'Source', 'Total Paid', 'Bookings', 'Booking IDs',
]


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
        ).exclude(tenant__full_name__in=PLACEHOLDER_TENANTS)
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
    """Merge each guest's back-to-back bookings in one apartment into one contract."""
    by_guest_apartment = defaultdict(list)
    for booking in bookings:
        by_guest_apartment[(booking.tenant_id, booking.apartment_id)].append(booking)

    contracts = []
    for guest_bookings in by_guest_apartment.values():
        guest_bookings.sort(key=lambda b: (b.start_date, b.end_date))
        contract = None
        for booking in guest_bookings:
            if contract and booking.start_date <= contract['end_date'] + EXTENSION_GAP:
                contract['bookings'].append(booking)
                contract['end_date'] = max(contract['end_date'], booking.end_date)
                continue
            contract = {
                'apartment': booking.apartment,
                'tenant': booking.tenant,
                'start_date': booking.start_date,
                'end_date': booking.end_date,
                'bookings': [booking],
            }
            contracts.append(contract)

    contracts.sort(key=lambda c: (c['start_date'], c['apartment'].name))
    return contracts


def add_paid_totals(contracts):
    """Total Paid = guest In payments minus deposit refunds; only Merged and Completed-Cash rows count."""
    booking_ids = [b.id for c in contracts for b in c['bookings']]
    payments = Payment.objects.filter(booking_id__in=booking_ids).filter(
        Q(payment_status='Merged') | Q(payment_status='Completed', payment_method__name='Cash')
    ).filter(
        Q(payment_type__type='In') | Q(payment_type__type='Out', payment_type__name__in=DEPOSIT_REFUND_TYPES)
    ).values_list('booking_id', 'amount', 'payment_type__type')

    paid_by_booking = defaultdict(Decimal)
    for booking_id, amount, direction in payments:
        paid_by_booking[booking_id] += amount if direction == 'In' else -amount

    for contract in contracts:
        contract['total_paid'] = sum((paid_by_booking[b.id] for b in contract['bookings']), Decimal('0'))


def apartment_type_label(apartment):
    return f"{apartment.bedrooms}BR" if apartment.bedrooms else ''


def generate_contracts_excel(contracts, start_date, end_date):
    sheets_service, drive_service = get_google_sheets_service()

    spreadsheet = sheets_service.create(body={
        'properties': {'title': f"Contracts Report: {start_date} - {end_date}"},
        'sheets': [{'properties': {'title': 'Contracts Report'}}],
    }).execute()
    spreadsheet_id = spreadsheet.get('spreadsheetId')
    sheet_id = spreadsheet['sheets'][0]['properties']['sheetId']

    values = [HEADERS]
    for contract in contracts:
        days = (contract['end_date'] - contract['start_date']).days
        values.append([
            contract['apartment'].name,
            apartment_type_label(contract['apartment']),
            contract['start_date'].isoformat(),
            contract['end_date'].isoformat(),
            days,
            round(days / 30.44, 1),
            contract['tenant'].full_name,
            contract['bookings'][0].source,
            float(contract['total_paid']),
            len(contract['bookings']),
            ', '.join(str(b.id) for b in contract['bookings']),
        ])

    sheets_service.values().update(
        spreadsheetId=spreadsheet_id,
        range='Contracts Report!A1',
        valueInputOption='USER_ENTERED',
        body={'values': values},
    ).execute()

    sheets_service.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': [
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
                'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'startColumnIndex': 8, 'endColumnIndex': 9},
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
