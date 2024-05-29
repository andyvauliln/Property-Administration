from googleapiclient.discovery import build
from google.oauth2 import service_account
from django.utils import timezone
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from django.contrib import messages
import base64
import pickle
import os


def create_contract(booking):

    docs_service, drive_service = get_services()

    document_id = create_doc_from_template(booking, drive_service)
    replaceText(booking, document_id, docs_service)

    share_document_with_user(
        drive_service, document_id)

    booking.contract_url = f"https://docs.google.com/document/d/{document_id}/edit"
    booking.update()
    print(f"Contract was made link is {booking.contract_url}")
    message = f"Hi, here you will find a link for your apartment rental reservation. Please, fill it out and sign. {booking.contract_url}"

    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com":
        print("Sending EMAIL")
        send_email(booking, booking.tenant.email, message)

    if booking.tenant.phone and booking.tenant.phone.startswith("+1"):
        send_sms(booking, message, booking.tenant.phone)

    if not (booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com") and not (booking.tenant.phone and booking.tenant.phone.startswith("+1")):
        print("Client wasn't notified about contract to sign")
        raise Exception(
            "Client wasn't notified about contract to sign, please process manualy")

    return booking.contract_url


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


def create_doc_from_template(booking, drive_service):
    print("Creating Document from Template")
    copy_title = f'Booking Contract {booking.tenant.full_name}, [{booking.apartment.name}] #{booking.pk}'
    document_copy = drive_service.files().copy(
        fileId=os.environ["TEMPLATE_BOOKING_CONTRACT_DOCUMENT_ID"],
        body={"name": copy_title},
    ).execute()

    id = document_copy.get('id')
    return id


def share_document_with_user(service, document_id):

    try:
       # Permission for public read access
        public_permission = {
            'type': 'anyone',
            'role': 'writer',
        }
        service.permissions().create(
            fileId=document_id,
            body=public_permission,
            fields='id',
        ).execute()

        print(f"Document {document_id} shared to public")
    except Exception as e:
        print(f"Failed to share document: {e}")


def replaceText(booking, document_id, docs_service):

    variables = {
        "owner": booking.apartment.owner.full_name,
        "occupant": booking.tenant.full_name,
        "phone": booking.tenant.phone or "",
        "email": booking.tenant.email or "",
        "start_date":  booking.start_date.strftime('%m/%d/%Y'),
        "end_date":  booking.end_date.strftime('%m/%d/%Y'),
        "apartment_address": booking.apartment.address,
        "payment_terms": booking.payment_str,
        "contract_date": timezone.now().strftime('%m/%d/%Y'),
    }
    print(booking.payment_str)

    requests = []

    for variable_name, variable_value in variables.items():
        request = {
            'replaceAllText': {
                'containsText': {
                    'text': "{{" + f"{variable_name}" + "}}",
                    'matchCase': 'true',
                },
                'replaceText': variable_value,
            }
        }
        requests.append(request)

    result = docs_service.documents().batchUpdate(
        documentId=document_id, body={'requests': requests}).execute()

    print("Template values are replaced")

    return result


def send_sms(booking, message, recipient, count=0):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]

    client = Client(account_sid, auth_token)
    try:
        twilio_message = client.messages.create(
            from_=twilio_phone,
            to=recipient,
            body=message
        )

        print(
            f'SMS sent from {twilio_phone} to {recipient} \n{message}')

        if booking.contract_send_status == "Not Sent":
            booking.contract_send_status = "Sent by SMS"
        else:
            booking.contract_send_status += ", Sent by SMS"
        booking.update()

    except TwilioException as e:
        context = f'Error sending SMS notification to {recipient}. \n{message} \n Error: {str(e)}, '
        print(context)
        if (count == 0):
            print(
                f"Try send message one more time to {recipient} \n {message}")
            return send_sms(booking, message, recipient, context, 1)
        else:
            print(
                f"SMS can't be sent to {recipient} \n {message} after {count} attempt")
            # db_message.message_status = "ERROR"
            # db_message.context = context
            # db_message.save()


def send_email(booking, recipient_email, message):
    try:
        print("Sending Email")
        CLIENT_SECRET_FILE = os.environ.get("GOOGLE_OAUTH2_TOKEN_FILE")
        API_NAME = 'gmail'
        API_VERSION = 'v1'
        SCOPES = ['https://www.googleapis.com/auth/gmail.send']

        service = create_service(
            CLIENT_SECRET_FILE, API_NAME, API_VERSION, SCOPES)

        mimeMessage = MIMEMultipart()
        mimeMessage['to'] = recipient_email
        mimeMessage['subject'] = 'Booking Contract Signing'
        mimeMessage.attach(MIMEText(message, 'plain'))
        raw_string = base64.urlsafe_b64encode(mimeMessage.as_bytes()).decode()

        message = service.users().messages().send(
            userId='me', body={'raw': raw_string}).execute()

        if booking.contract_send_status == "Not Sent":
            booking.contract_send_status = "Sent by Email"
        else:
            booking.contract_send_status += "Sent by Email"

        booking.update()
        print("Email sent successfully!")

    except Exception as e:
        print("An error occurred while sending the email:", e)


def create_service(client_secret_file, api_name, api_version, *scopes):
    print("CREATING GMAIL SERVICE")
    CLIENT_SECRET_FILE = client_secret_file
    API_SERVICE_NAME = api_name
    API_VERSION = api_version
    SCOPES = [scope for scope in scopes[0]]

    cred = None

    pickle_file = f'token_{API_SERVICE_NAME}_{API_VERSION}.pickle'
    # print(pickle_file)

    if os.path.exists(pickle_file):
        with open(pickle_file, 'rb') as token:
            cred = pickle.load(token)

    if not cred or not cred.valid:
        print("CRED VALID")
        if cred and cred.expired and cred.refresh_token:
            print("CRED EXPIRED")
            cred.refresh(Request())
        else:
            print("CREATE CRED")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES)
            cred = flow.run_local_server()

        with open(pickle_file, 'wb') as token:
            print("DUMP TOKEN")
            pickle.dump(cred, token)

    try:
        service = build(API_SERVICE_NAME, API_VERSION, credentials=cred)
        print(API_SERVICE_NAME, 'service created successfully')
        return service
    except Exception as e:
        print('Unable to connect.')
        print(e)
        return None
