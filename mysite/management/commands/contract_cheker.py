

import requests
from datetime import timedelta, date
from mysite.models import Booking, User
import os
from django.core.management.base import BaseCommand
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
from mysite.models import Chat
import re


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


def extract_plain_text(doc_content):
    plain_text = ''
    for elem in doc_content:
        if 'paragraph' in elem and 'elements' in elem['paragraph']:
            for element in elem['paragraph']['elements']:
                if 'textRun' in element and 'content' in element['textRun']:
                    plain_text += element['textRun']['content']
    return plain_text


def extract_and_update_values(booking, text):
    print("exctractig values")
    # Extracting E-mail
    email_match = re.search(r'E-mail:\s*([^\s]+)', text)
    if email_match:
        email = email_match.group(1)
        print(email,  booking.tenant.email)
        if email and booking.tenant.email != email:
            # Check if a user with the same email already exists
            existing_user = User.objects.filter(email=email).first()
            if existing_user:
                # Update existing user instead of creating a new one
                existing_user.email = email
                existing_user.save()
                # Assign existing user to the booking
                booking.tenant = existing_user
            else:
                # No existing user with the same email, create a new one
                new_user = User.objects.create(email=email, role='Tenant')
                # Assign new user to the booking
                booking.tenant = new_user

    # Extracting Occupant Address
    phone_match = re.search(r'Contact:\s*([^\n]+)', text)
    if phone_match:
        phone = phone_match.group(1)
        print(phone,  booking.tenant.phone)
        if phone and booking.tenant.phone != phone:
            booking.tenant.phone = phone

    occupant_match = re.search(r'Occupant:\s*([^\n]+)', text)
    if occupant_match:
        occupant = occupant_match.group(1)
        print(occupant,  booking.tenant.full_name)
        if occupant and booking.tenant.full_name != occupant:
            booking.tenant.full_name = occupant

    booking.tenant.save()
    print("Updaiting Status")
    booking.status = "Waiting Payment"

    booking.update()


def update_values(booking: Booking, doc_id, docs_service):
    print("Updating Values")

    doc = docs_service.documents().get(documentId=doc_id).execute()
    doc_content = doc.get('body').get('content')
    doc_content_text = extract_plain_text(doc_content)
    print(doc_content_text)
    extract_and_update_values(booking, doc_content_text)


def send_notification(booking: Booking):
    print("Sending Notification to Manager")
    message = f"Contract was signed by {booking.tenant.full_name} for booking {booking.apartment.name} from {booking.start_date} to {booking.end_date} \n {booking.contract_url}"
    send_sms(booking, message)
    send_telegram_message(message)


def send_sms(booking, message, count=0):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]

    client = Client(account_sid, auth_token)
    db_message = Chat.objects.create(
        booking=booking,
        sender_phone=twilio_phone,
        receiver_phone=manager_phone,
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
            to=manager_phone,
            body=message
        )

        print(
            f'SMS sent from {twilio_phone} to {manager_phone} \n{message}')

    except TwilioException as e:
        context = f'Error sending SMS notification to {manager_phone}. \n{message} \n Error: {str(e)}, '
        print(context)
        if (count == 0):
            print(
                f"Try send message one more time to {manager_phone} \n {message}")
            return send_sms(booking, message)
        else:
            print(
                f"SMS can't be sent to {manager_phone} \n {message} after {count} attempt")
            db_message.message_status = "ERROR"
            db_message.context = context
            db_message.save()


def send_telegram_message(message):
    telegram_chat_ids = os.environ["TELEGRAM_CHAT_ID"].split(",")
    telegram_token = os.environ["TELEGRAM_TOKEN"]

    for chat_id in telegram_chat_ids:
        base_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage?chat_id={chat_id.strip()}&text={message}"
        requests.get(base_url)

    print("telegram notification was sent")
