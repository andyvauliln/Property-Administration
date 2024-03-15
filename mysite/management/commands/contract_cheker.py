

import requests
from datetime import timedelta, date
from mysite.models import Booking
import os
from django.core.management.base import BaseCommand
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
from mysite.models import Chat


def check_contract():
    bookings = Booking.objects.filter(status="Waiting Contract")

    for booking in bookings:
        if booking.contract_url:
            doc_id = booking.contract_url.split("/")[-2]
            docs_service = get_services()
            isSigned = check_signiture(doc_id, docs_service)
            if isSigned:
                update_values(booking, doc_id, docs_service)
                send_notification(booking)

    print(f"Signature is {isSigned}")


class Command(BaseCommand):
    help = 'Run contract checker task'

    def handle(self, *args, **options):
        self.stdout.write('Running contract cheker task...')
        check_contract()
        self.stdout.write(self.style.SUCCESS(
            'Successfully ran contract cheker task'))


def get_services():
    print("Getting GOOGLE Services")
    # Authenticate with Google Docs API using service account credentials
    credentials = service_account.Credentials.from_service_account_file(
        'google_tokens.json',
        scopes=['https://www.googleapis.com/auth/documents']
    )

    # Build the service
    docs_service = build('docs', 'v1', credentials=credentials)

    return docs_service


def check_signiture(doc_id, docs_service):
    print("Checking signeture")
    doc = docs_service.documents().get(documentId=doc_id).execute()
    doc_content = doc.get('body').get('content')

    signature_text_index = None
    for i, elem in enumerate(doc_content):
        if elem.get('paragraph') and elem['paragraph'].get('elements'):
            for paragraph_elem in elem['paragraph']['elements']:
                # print(f"\n\n {paragraph_elem.get('textRun')}\n\n")
                if paragraph_elem.get('textRun') and 'Occupant’s Signature:' in paragraph_elem['textRun'].get('content', ''):
                    print(
                        f"FOUND SOMETHING: {paragraph_elem['textRun'].get('content')}")
                    signature_text_index = i
                    break
        if signature_text_index is not None:
            break

    # Check if a signature image is present after the "Occupant’s Signature:" text
    if signature_text_index is not None:
        for i in range(signature_text_index + 1, len(doc_content)):
            elem = doc_content[i]
            if elem.get('paragraph') and elem['paragraph'].get('elements'):
                for paragraph_elem in elem['paragraph']['elements']:
                    if paragraph_elem.get('inlineObjectElement'):
                        # A signature inline object is present
                        return True

    # No signature image found
    return False


def extract_value(doc_content, field_name):
    print(f"Extracting value {field_name}")
    # TODO: Extraction of Values from DOC
    print(doc_content)
    return ""


def update_values(booking: Booking, doc_id, docs_service):
    print("Getting Values")

    response = docs_service.documents().export(
        documentId=doc_id, mimeType='text/plain').execute()

    doc_content = response.decode('utf-8')

    address_value = extract_value(doc_content, "Address:")
    car_info_value = extract_value(doc_content, "Car Info:")
    print(f"Got Values {address_value} {car_info_value}")

    # TODO: Store Values in DB

    booking.status = "Waiting Payment"
    booking.update()


def send_notification(booking: Booking):
    print("Sending Notification to Manager")
    message = f"Contract was signed by {booking.tenant.full_name} for booking {booking.apartment.name} from {booking.start_date} to {booking.end_date} \n {booking.contract_url}"
    send_sms(booking, message)
    send_telegram_message(message)


def send_sms(booking, message, recipient, count=0):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]

    client = Client(account_sid, auth_token)
    db_message = Chat.objects.create(
        booking=booking,
        sender_phone=twilio_phone,
        receiver_phone=recipient,
        message=message,
        context="",
        sender_type="SYSTEM",
        message_type="NO_NEED_ACTION",
        message_status="SENDED",
    )
    db_message.save()
    try:
        twilio_message = client.messages.create(
            from_=twilio_phone,
            to=recipient,
            body=message
        )

        print(
            f'SMS sent from {twilio_phone} to {recipient} \n{message}')

    except TwilioException as e:
        context = f'Error sending SMS notification to {recipient}. \n{message} \n Error: {str(e)}, '
        print(context)
        if (count == 0):
            print(
                f"Try send message one more time to {recipient} \n {message}")
            return send_sms(manager_phone, context, 1)
        else:
            print(
                f"SMS can't be sent to {recipient} \n {message} after {count} attempt")
            db_message.message_status = "ERROR"
            db_message.context = context
            db_message.save()


def send_telegram_message(message):
    telegram_chat_ids = os.environ["TELEGRAM_CHAT_ID"].split(",")
    telegram_token = os.environ["TELEGRAM_TOKEN"]

    for chat_id in telegram_chat_ids:
        base_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage?chat_id={chat_id.strip()}&text={message}"
        requests.get(base_url)
