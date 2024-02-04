import re
from django.core.management.base import BaseCommand
from mysite.models import Booking, Payment
import os
from django.utils import timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from django.db.models import Sum
from dateutil.relativedelta import relativedelta


class Command(BaseCommand):
    help = 'Gernerate Ivoice For Payment'

    def handle(self, *args, **options):
        payment = Booking.objects.get(id=440)
        invoice_url = fill_template_and_save(payment)
        print(f'Invoice created {invoice_url}')


def fill_template_and_save(payment: Payment):
    try:

        docs_service, drive_service = get_services()

        document_id = create_doc_from_template(payment, drive_service)
        replaceText(payment, document_id, docs_service)

        share_document_with_user(
            drive_service, document_id)

        payment.invoice_url = f"https://docs.google.com/document/d/{document_id}/edit"
        payment.save()

        return payment.invoice_url

    except Exception as e:
        # Handle errors appropriately
        print(f"Error: {e}")
        return None


def get_services():
    print("Getting GOOGLE Services")
    # Authenticate with Google Docs API using service account credentials
    credentials = service_account.Credentials.from_service_account_file(
        'google_tokens.json',
        scopes=['https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/drive']
    )

    # Build the service
    drive_service = build('drive', 'v3', credentials=credentials)
    docs_service = build('docs', 'v1', credentials=credentials)

    return docs_service, drive_service


def create_doc_from_template(payment, drive_service):
    print("Creating Document from Template")
    copy_title = f'Payment Invoice For[{payment.booking.apartment.name}] N {payment.booking.id}'
    document_copy = drive_service.files().copy(
        fileId=os.environ["TEMPLATE_DOCUMENT_ID"],
        body={"name": copy_title},
    ).execute()

    id = document_copy.get('id')
    return id


def share_document_with_user(service, document_id, user_email):

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

        print(f"Document {document_id} shared to public")
    except Exception as e:
        print(f"Failed to share document: {e}")


def replaceText(payment: Payment, document_id, docs_service):

    tenant_name = "tenant_name"
    address = "address"
    room_number = "room_number"

    if (payment.booking):
        tenant_name = payment.booking.tenant.full_name
        address = f"{payment.booking.apartment.building_n} {payment.booking.apartment.city} {payment.booking.apartment.street}, {payment.booking.apartment.state}, {payment.booking.apartment.zip_index}"
        room_number = payment.booking.apartment.apartment_n

    rent_period = f"{payment.payment_date.strftime('%Y-%m-%d')} - {(payment.payment_date + relativedelta(months=1) ).strftime('%Y-%m-%d')}"
    current_date = payment.payment_date.strftime('%Y-%m-%d')
    total_price = payment.amount
    payment_method = payment.payment_method

    requests = []
    requests.append({
        'replaceAllText': {
            'containsText': {
                'text': '{{tenant_name}}',
                'matchCase': 'true',
            },
            'replaceText': tenant_name,
        },
    })
    requests.append({
        'replaceAllText': {
            'containsText': {
                'text': '{{payment_method}}',
                'matchCase': 'true',
            },
            'replaceText': payment_method,
        },
    })
    requests.append({
        'replaceAllText': {
            'containsText': {
                'text': '{{rent_period}}',
                'matchCase': 'true',
            },
            'replaceText': rent_period,
        },
    })
    requests.append({
        'replaceAllText': {
            'containsText': {
                'text': '{{current_date}}',
                'matchCase': 'true',
            },
            'replaceText': str(current_date),
        },
    })
    requests.append({
        'replaceAllText': {
            'containsText': {
                'text': '{{total_price}}',
                'matchCase': 'true',
            },
            'replaceText': str(total_price),
        },
    })
    requests.append({
        'replaceAllText': {
            'containsText': {
                'text': '{{address}}',
                'matchCase': 'true',
            },
            'replaceText': str(address),
        },
    })
    requests.append({
        'replaceAllText': {
            'containsText': {
                'text': '{{room_number}}',
                'matchCase': 'true',
            },
            'replaceText': str(room_number),
        },
    })

    result = docs_service.documents().batchUpdate(
        documentId=document_id, body={'requests': requests}).execute()

    return result
