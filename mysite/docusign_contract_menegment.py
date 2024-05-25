from googleapiclient.discovery import build
from google.oauth2 import service_account
from django.utils import timezone
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
from docusign_esign import ApiClient
from docusign_esign.client.api_exception import ApiException
import os
from docusign_esign import EnvelopesApi, EnvelopeDefinition, Document, Signer, CarbonCopy, SignHere, Tabs, Recipients
from docusign_esign import EnvelopesApi, EnvelopeDefinition, TemplateRole, Text, Tabs, EnvelopeEvent
from docusign_esign import ApiClient, AuthenticationApi, RecipientViewRequest, EventNotification, RecipientEvent

SCOPES = ["signature", "impersonation"]

DOCUSIGN_AUTH_SERVER = os.environ["DOCUSIGN_AUTH_SERVER"]
DOCUSIGN_PRIVATE_KEY = os.environ["DOCUSIGN_PRIVATE_KEY"]
BASE_PATH = os.environ["DOCUSIGN_API_URL"]
DOCUSIGN_INTEGRATION_KEY = os.environ["DOCUSIGN_INTEGRATION_KEY"]
DOCUSIGN_USER_ID = os.environ["DOCUSIGN_USER_ID"]
DOCUSIGN_TEMPLATE_ID = os.environ["DOCUSIGN_TEMPLATE_ID"]
DOCUSIGN_SIGN_CALLBACK_URL = os.environ["DOCUSIGN_SIGN_CALLBACK_URL"]
DOCUSIGN_API_ACCOUNT_ID = os.environ["DOCUSIGN_API_ACCOUNT_ID"]


def create_contract(booking):
    print("\n********** Start Creating Contract **********\n")
    envelope_api, envelope_id, contract_url = create_contract_docusign(booking)

    booking.contract_url = contract_url
    booking.update()
    print(f"Contract was made link is {booking.contract_url}")
    message = f"Hi, here you will find a link for your apartment rental reservation. Please, fill it out and sign. {booking.contract_url}"

    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com":
        print("Sending EMAIL")
        process_docusign_email(booking, envelope_api, envelope_id)

    if booking.tenant.phone and booking.tenant.phone.startswith("+1"):
        print("Sending SMS")
        send_sms(booking, message, booking.tenant.phone)

    if not (booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com") and not (booking.tenant.phone and booking.tenant.phone.startswith("+1")):
        print("Client wasn't notified about contract to sign")
        raise Exception(
            "Client wasn't notified about contract to sign, please process manualy")

    return booking.contract_url


def create_contract_docusign(booking):
    api_client = ApiClient()
    api_client.set_base_path(DOCUSIGN_AUTH_SERVER)
    api_client.set_oauth_host_name(DOCUSIGN_AUTH_SERVER)
    jwt_values = get_token(DOCUSIGN_PRIVATE_KEY, api_client)
    print("WE GOT ACCESS TOKEN", jwt_values)

    api_client.host = BASE_PATH
    api_client.set_default_header(header_name="Authorization", header_value=f"Bearer {jwt_values['access_token']}")
   
    tenant = booking.tenant

    envelope_definition = make_envelope(booking)


    envelopes_api = EnvelopesApi(api_client)
    envelope_result = envelopes_api.create_envelope(
        account_id=DOCUSIGN_API_ACCOUNT_ID,
        envelope_definition=envelope_definition
    )
    print("Envelope Created !!!", envelope_result)

    contract_url = create_recipient_view(tenant, envelopes_api, envelope_result.envelope_id)

    envelope_id = envelope_result.envelope_id

    return envelopes_api, envelope_id, contract_url


def create_recipient_view(tenant, envelopes_api, envelope_id):
    recipient_view_request = RecipientViewRequest(
            authentication_method="none",
            client_user_id=tenant.id,
            recipient_id="1",
            return_url=f"{DOCUSIGN_SIGN_CALLBACK_URL}{envelope_id}",
            user_name=tenant.full_name,
            email=tenant.email
        )

    recipient_result = envelopes_api.create_recipient_view(
        account_id=DOCUSIGN_API_ACCOUNT_ID,
        envelope_id=envelope_id,
        recipient_view_request=recipient_view_request
    )
    print("Recipient View Created", recipient_result.url)
    return recipient_result.url

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

def make_envelope(booking):

        tabs = getData(booking)
        tenant = booking.tenant

        tenant = TemplateRole(
            email=tenant.email,
            name=tenant.full_name,
            client_user_id=tenant.id,
            role_name="tenant",
            tabs=tabs
        )

        envelope_definition = EnvelopeDefinition(
            email_subject='Hi, it is Farid, Please sign a renting agreement',
            template_id=DOCUSIGN_TEMPLATE_ID,
            template_roles=[tenant],
            status='created',
        )

        print("Envelope Definition Created")

        return envelope_definition


def process_docusign_email(booking, envelopes_api, envelope_id):
    try:
        print("Sending Email")

        envelope_update = EnvelopeDefinition(status="sent")

        envelopes_api.update(DOCUSIGN_API_ACCOUNT_ID, envelope_id, envelope_update)
        booking.contract_send_status = "Sent by Email"
        booking.update()
        print(f"Envelope {envelope_id} sent successfully.")
    except ApiException as e:
        print(f"Exception when calling EnvelopesApi->update: {e}")
       
        

    except Exception as e:
        print("An error occurred while sending the email:", e)


def get_token(private_key, api_client):

    token_response = get_jwt_token(private_key, SCOPES, DOCUSIGN_AUTH_SERVER, DOCUSIGN_INTEGRATION_KEY,DOCUSIGN_USER_ID)
    access_token = token_response.access_token
    print("access_token", access_token)

    # Save API account ID
    user_info = api_client.get_user_info(access_token)
    print(user_info, "user_info")
    accounts = user_info.get_accounts()
    api_account_id = accounts[0].account_id
    base_path = accounts[0].base_uri + "/restapi"

    return {"access_token": access_token, "api_account_id": api_account_id, "base_path": base_path}


def get_jwt_token(private_key, scopes, auth_server, client_id, impersonated_user_id):
    """Get the jwt token"""
    api_client = ApiClient()
    api_client.set_base_path(auth_server)
    response = api_client.request_jwt_user_token(
        client_id=client_id,
        user_id=impersonated_user_id,
        oauth_host_name=auth_server,
        private_key_bytes=private_key,
        expires_in=4000,
        scopes=scopes
    )
    return response

def get_private_key(private_key_path):

    private_key_file = path.abspath(private_key_path)

    if path.isfile(private_key_file):
        with open(private_key_file) as private_key_file:
            private_key = private_key_file.read()
    else:
        private_key = private_key_path

    return private_key

def create_api_client(base_path, access_token):
    """Create api client and construct API headers"""
    api_client = ApiClient()
    api_client.host = base_path
    api_client.set_default_header(header_name="Authorization", header_value=f"Bearer {access_token}")

    return api_client

def getData(booking):
    text_fields = [
        {"tabLabel": "owner", "value": booking.apartment.owner.full_name},
        {"tabLabel": "occupant", "value": booking.tenant.full_name},
        {"tabLabel": "phone", "value": booking.tenant.phone or ""},
        {"tabLabel": "email", "value": booking.tenant.email or ""},
        {"tabLabel": "start_date", "value": booking.start_date.strftime('%m/%d/%Y')},
        {"tabLabel": "end_date", "value": booking.end_date.strftime('%m/%d/%Y')},
        {"tabLabel": "apartment_address", "value": booking.apartment.address},
        {"tabLabel": "payment_terms", "value": booking.payment_str},
        {"tabLabel": "contract_date", "value": timezone.now().strftime('%m/%d/%Y')}
    ]
    # Convert to Text objects
    text_tabs = [Text(tab_label=item["tabLabel"], value=item["value"]) for item in text_fields]

    # Create a Tabs object containing only text_tabs
    tabs = Tabs(text_tabs=text_tabs)

    return tabs

def get_consent_url():
    url_scopes = "+".join(SCOPES)

    # Construct consent URL
    redirect_uri = "https://developers.docusign.com/platform/auth/consent"
    consent_url = f"https://{DS_JWT['authorization_server']}/oauth/auth?response_type=code&" \
                  f"scope={url_scopes}&client_id={DS_JWT['ds_client_id']}&redirect_uri={redirect_uri}"

    return consent_url