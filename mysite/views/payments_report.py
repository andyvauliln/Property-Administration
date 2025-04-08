from django.shortcuts import render
from ..models import Apartment, Payment, PaymenType, PaymentMethod
from datetime import datetime
import calendar
from ..decorators import user_has_role
from .utils import aggregate_data, aggregate_summary, assign_color_classes
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from django.http import HttpResponseRedirect
from django.urls import reverse
from urllib.parse import urlencode
import logging
import calendar

logger_common = logging.getLogger('mysite.common')


def print_info(message):
    print(message)
    logger_common.debug(message)


@user_has_role('Admin')
def paymentReport(request):
    apartment_filter = request.GET.get('apartment', None)
    start_date_str = request.GET.get('start_date', None)
    end_date_str = request.GET.get('end_date', None)
    payment_type_filter = request.GET.get('payment_type', None)
    apartment_type_filter = request.GET.get('apartment_type', None)
    payment_method_filter = request.GET.get('payment_method', None)
    payment_status_filter = request.GET.get('payment_status', None)
    payment_category_filter = request.GET.get('payment_category', None)
    payment_direction_filter = request.GET.get('payment_direction', None)
    isExcel = request.GET.get('isExcel', None)

    # Convert the date strings to datetime objects or set to the start and end of the current year
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%B %d %Y')
    else:
        start_date = datetime(datetime.now().year, datetime.now().month, 1)

    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%B %d %Y')
    else:
        last_day = calendar.monthrange(start_date.year, start_date.month)[1]
        end_date = datetime(start_date.year, start_date.month, last_day)

    # Query for fetching apartments
    apartments = Apartment.objects.all().order_by(
        'name').values_list('name', flat=True)
    apartment_types = Apartment.TYPES
    # payment_types = PaymenType.objects.all().values_list('name', flat=True)
    payment_types = PaymenType.objects.all()
    payment_methods = PaymentMethod.objects.all()

    payments_within_range = Payment.objects.filter(
        payment_date__range=[start_date, end_date]
    )

    if payment_direction_filter:
        if payment_direction_filter == "In":
            payments_within_range = payments_within_range.filter(payment_type__type="In")
        elif payment_direction_filter == "Out":
            payments_within_range = payments_within_range.filter(payment_type__type="Out")

    if payment_category_filter:
        payments_within_range = payments_within_range.filter(
            payment_type__category=payment_category_filter
        )

    payments_within_range = payments_within_range.select_related(
        'payment_type', 'payment_method', 'bank'
    ).order_by(
        'payment_date'
    )

    if apartment_filter:
        if apartment_filter == "None_Booking":
            payments_within_range = [
                payment for payment in payments_within_range
                if not payment.booking
            ]
        elif apartment_filter == "None_Apart":
            payments_within_range = [
                payment for payment in payments_within_range
                if (not payment.booking) and (not payment.apartment)
            ]
        else: 
            filtered_payments = []
            for payment in payments_within_range:
                print_info(f"Checking payment {payment.id}:")
                print_info(f"- Has booking: {bool(payment.booking)}")
                if payment.booking:
                    print_info(f"- Booking apartment: {payment.booking.apartment.name}")
                print_info(f"- Has direct apartment: {bool(payment.apartment)}")
                if payment.apartment:
                    print_info(f"- Direct apartment: {payment.apartment.name}")
                
                if payment.booking and payment.booking.apartment.name == apartment_filter:
                    print_info(f"✓ Payment {payment.id} matched through booking")
                    filtered_payments.append(payment)
                elif payment.apartment and payment.apartment.name == apartment_filter:
                    print_info(f"✓ Payment {payment.id} matched through direct apartment")
                    filtered_payments.append(payment)
                else:
                    print_info(f"✗ Payment {payment.id} did not match filter {apartment_filter}")
                
            payments_within_range = filtered_payments

    if apartment_type_filter:
        payments_within_range = [payment for payment in payments_within_range if (
            (payment.booking and payment.booking.apartment.apartment_type == apartment_type_filter) or
            (payment.apartment and payment.apartment.apartment_type ==
             apartment_type_filter)
        )]

    if payment_type_filter:
        payments_within_range = [payment for payment in payments_within_range
                                 if payment.payment_type.id == int(payment_type_filter)]
    if payment_method_filter:
        payments_within_range = [payment for payment in payments_within_range
                                 if payment.payment_method and payment.payment_method.id == int(payment_method_filter)]

    if payment_status_filter:
        payments_within_range = [payment for payment in payments_within_range
                                 if payment.payment_status == payment_status_filter]

    in_colors = [
        "text-emerald-300",
        "text-emerald-400", "text-emerald-500", "text-emerald-600",
        "text-emerald-700", "text-emerald-800", "text-emerald-900"
    ]

    out_colors = [
        "text-rose-300",
        "text-rose-400", "text-rose-500", "text-rose-600",
        "text-rose-700", "text-rose-800", "text-rose-900"
    ]

    current_month = start_date.replace(day=1)

    monthly_data = []

    # Iterate through each month from the start date to the end date
    while current_month <= end_date:
        # Filter payments for the specific month
        payments_for_month = [
            payment for payment in payments_within_range
            if payment.payment_date.year == current_month.year and payment.payment_date.month == current_month.month
        ]
        assign_color_classes(payments_for_month, in_colors, out_colors)

        income, outcome, pending_income, pending_outcome = aggregate_data(
            payments_for_month)

        profit = income - outcome
        pending_profit = pending_income - pending_outcome

        monthly_data.append({
            "month_name": calendar.month_name[current_month.month] + " " + str(current_month.year),
            "payments": payments_for_month,
            "income": income,
            "outcome": outcome,
            "profit": profit,
            "pending_profit": pending_profit,
            "pending_income": pending_income,
            "pending_outcome": pending_outcome
        })

        # Move to the next month
        if current_month.month == 12:
            current_month = current_month.replace(
                year=current_month.year+1, month=1)
        else:
            current_month = current_month.replace(month=current_month.month+1)

    summary = aggregate_summary(payments_within_range)
    excel_link = ""
    if isExcel:
        excel_link = generate_excel(
            summary, monthly_data, start_date, end_date)
        if (excel_link):
            return HttpResponseRedirect(excel_link)

    context = {
        'start_date': start_date.strftime('%B %d %Y'),
        'end_date': end_date.strftime('%B %d %Y'),
        'summary': summary,
        'monthly_data': monthly_data,
        'apartments': apartments,
        'apartment_types': apartment_types,
        'payment_types': payment_types,
        'payment_methods': payment_methods,
        'current_apartment': apartment_filter,
        'current_apartment_type': apartment_type_filter,
        'current_payment_method': int(payment_method_filter) if payment_method_filter else None,
        'current_payment_status': payment_status_filter,
        'current_payment_type': payment_type_filter,
        'current_payment_category': payment_category_filter,
        'current_payment_direction': payment_direction_filter,
        'title': "Payments Report",
    }

    return render(request, 'payment_report.html', context)


def generate_excel(summary, monthly_data, start_date, end_date):
    try:
        sheets_service, drive_service = get_google_sheets_service()
        print_info("Got GOOGLE Services")
        # Create a new spreadsheet
        spreadsheet_body = {
            'properties': {
                'title': f"Payment Report: {start_date.strftime('%B %d %Y')} - {end_date.strftime('%B %d %Y')}"
            },
            'sheets': [{
                'properties': {
                    'title': 'Summary'
                }
            }]
        }
        spreadsheet = sheets_service.create(body=spreadsheet_body).execute()
        spreadsheet_id = spreadsheet.get('spreadsheetId')
        print_info(f"Spredsheet created {spreadsheet_id}")

        # Insert summary data into the Summary sheet
        insert_summary_data(sheets_service, spreadsheet_id, summary)

        # Generate and insert monthly data reports
        for month_data in monthly_data:
            insert_monthly_data_report(
                sheets_service, spreadsheet_id, month_data)

        share_document_with_user(drive_service, spreadsheet_id)

        # Generate and return the link to the spreadsheet
        excel_link = f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'
        return excel_link
    except Exception as e:
        print_info("ERROR: Error while generating Excel Report")
        return ""


def insert_summary_data(sheets_service, spreadsheet_id, summary):
    summary_values = [
        ['Completed Revenue:', f"${summary['total_income']}"],
        ['Pending Revenue:', f"${summary['total_pending_income']}"],
        ['Expense:', f"-${summary['total_expense']}"],
        ['Pending Expense:', f"-${summary['total_pending_outcome']}"],
        ['Profit:', f"${summary['total_profit']}"],
        ['Pending Profit:', f"${summary['total_pending_profit']}"]
    ]
    summary_range = 'Summary!A1:B6'
    sheets_service.values().update(
        spreadsheetId=spreadsheet_id, range=summary_range,
        valueInputOption='USER_ENTERED', body={'values': summary_values}).execute()

    print_info(f"Created Summary Sheet for {spreadsheet_id}")


def insert_monthly_data_report(sheets_service, spreadsheet_id, month_data):
    month_sheet_title = month_data['month_name'].replace(' ', '_')
    # Add a new sheet for the month
    sheets_service.batchUpdate(spreadsheetId=spreadsheet_id, body={
        'requests': [{'addSheet': {'properties': {'title': month_sheet_title}}}]
    }).execute()

    # Combine month summary and payment details
    month_summary_values = [
        [f"{month_data['month_name']} Report"],
        ["Completed Revenue:", f"${month_data['income']}"],
        ["Pending Revenue:", f"${month_data['pending_income']}"],
        ["Expense:", f"-${month_data['outcome']}"],
        ["Pending Expense:", f"-${month_data['pending_outcome']}"],
        ["Profit:", f"${month_data['profit']}"],
        ["Pending Profit:", f"${month_data['pending_profit']}"],
        [],  # Empty row for spacing
        ["Payment Date", "Payment Notes", "Payment Amount", "Payment Type",
            "Payment Method", "Bank", "Apartment", "Tenant", "Status"]
    ]
    # Append payment details to summary values
    for payment in month_data['payments']:
        amount = payment.amount
        if payment.payment_type.type == "Out":
            amount = -amount  # Make the amount negative for "Out" payments
        
        month_summary_values.append([
            str(payment.payment_date), 
            payment.notes, 
            f"${amount:.2f}",  # Format amount with 2 decimal places
            payment.payment_type.name,
            payment.payment_method.name if payment.payment_method else '', 
            payment.bank.name if payment.bank else '',
            payment.booking.apartment.name if payment.booking and payment.booking.apartment else '',
            payment.booking.tenant.full_name if payment.booking else '', 
            payment.payment_status
        ])

    # Insert data into the month sheet
    month_range = f'{month_sheet_title}!A1'
    sheets_service.values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'valueInputOption': 'USER_ENTERED', 'data': [
            {'range': month_range, 'values': month_summary_values}]}
    ).execute()

    print_info(f"Created Sheet for {month_sheet_title}")


def get_google_sheets_service():
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    SERVICE_ACCOUNT_FILE = 'google_tokens.json'

    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)

    sheets_service = build('sheets', 'v4', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials)

    return sheets_service.spreadsheets(), drive_service


def share_document_with_user(service, document_id):

    try:
       # Permission for public read access
        public_permission = {
            'type': 'anyone',
            'role': 'reader',
        }
        service.permissions().create(
            fileId=document_id,
            body=public_permission,
            fields='id',
        ).execute()

        print_info(f"Document {document_id} shared to public")
    except Exception as e:
        print_info(f"Failed to share document: {e}")
