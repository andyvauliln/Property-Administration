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
EXTENSION_GAP = timedelta(days=9)
# Out payments that give guest money back (the rest of the Out rows on a booking are our expenses)
DEPOSIT_REFUND_TYPES = ('Damage Deposit', 'Hold Deposit')

HEADERS = [
    'Row', 'Apartment', 'Apartment Type', 'Start', 'End', 'Days', 'Months',
    'Guest', 'Source', 'Total Paid', 'Bookings', 'Booking ID', 'Extension',
]
TOTAL_PAID_COLUMN = HEADERS.index('Total Paid')
# Combined contract rows (a contract with extensions)
COMBINED_ROW_COLOR = {'red': 1.0, 'green': 0.95, 'blue': 0.7}


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
        for booking in contract['bookings']:
            booking.total_paid = paid_by_booking[booking.id]
        contract['total_paid'] = sum((b.total_paid for b in contract['bookings']), Decimal('0'))


def apartment_type_label(apartment):
    return f"{apartment.bedrooms}BR" if apartment.bedrooms else ''


def report_row(row_type, apartment, start, end, guest, source, paid, bookings, booking_id, extension):
    days = (end - start).days
    return [
        row_type, apartment.name, apartment_type_label(apartment), start.isoformat(), end.isoformat(),
        days, round(days / 30.44, 1), guest.full_name, source, float(paid), bookings, booking_id, extension,
    ]


def contract_rows(contract):
    """One Contract row; a contract with extensions also gets one Booking row per booking under it."""
    bookings = contract['bookings']
    first = bookings[0]
    source = next((b.source for b in bookings if b.source), '')
    if len(bookings) == 1:
        return [report_row('Contract', contract['apartment'], contract['start_date'], contract['end_date'],
                           contract['tenant'], source, contract['total_paid'], 1, f'#{first.id}', '')], False

    rows = [report_row(
        'Contract', contract['apartment'], contract['start_date'], contract['end_date'], contract['tenant'],
        source, contract['total_paid'], len(bookings), ', '.join(f'#{b.id}' for b in bookings),
        f'Combined: {len(bookings)} bookings',
    )]
    covered_until = first.end_date
    for previous, booking in zip([None] + bookings, bookings):
        if previous is None:
            extension = 'First booking'
        else:
            gap = (booking.start_date - covered_until).days
            gap_text = f'overlap {-gap} days' if gap < 0 else f"gap {gap} day{'' if gap == 1 else 's'}"
            extension = f'Extension of #{previous.id} ({gap_text})'
            covered_until = max(covered_until, booking.end_date)
        rows.append(report_row(
            'Booking', contract['apartment'], booking.start_date, booking.end_date, contract['tenant'],
            booking.source, booking.total_paid, '', f'#{booking.id}', extension,
        ))
    return rows, True


def generate_contracts_excel(contracts, start_date, end_date):
    sheets_service, drive_service = get_google_sheets_service()

    spreadsheet = sheets_service.create(body={
        'properties': {'title': f"Contracts Report: {start_date} - {end_date}"},
        'sheets': [{'properties': {'title': 'Contracts Report'}}],
    }).execute()
    spreadsheet_id = spreadsheet.get('spreadsheetId')
    sheet_id = spreadsheet['sheets'][0]['properties']['sheetId']

    values = [HEADERS]
    combined_row_indexes = []
    for contract in contracts:
        rows, combined = contract_rows(contract)
        if combined:
            combined_row_indexes.append(len(values))
        values.extend(rows)

    sheets_service.values().update(
        spreadsheetId=spreadsheet_id,
        range='Contracts Report!A1',
        valueInputOption='USER_ENTERED',
        body={'values': values},
    ).execute()

    combined_formats = [
        {
            'repeatCell': {
                'range': {'sheetId': sheet_id, 'startRowIndex': index, 'endRowIndex': index + 1,
                          'startColumnIndex': 0, 'endColumnIndex': len(HEADERS)},
                'cell': {'userEnteredFormat': {'textFormat': {'bold': True}, 'backgroundColor': COMBINED_ROW_COLOR}},
                'fields': 'userEnteredFormat(textFormat,backgroundColor)',
            }
        }
        for index in combined_row_indexes
    ]

    sheets_service.batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': combined_formats + [
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
                          'startColumnIndex': TOTAL_PAID_COLUMN, 'endColumnIndex': TOTAL_PAID_COLUMN + 1},
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
