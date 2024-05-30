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
DOCUSIGN_API_ACCOUNT_ID = os.environ["DOCUSIGN_API_ACCOUNT_ID"]


def create_contract(booking):


    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com":
        contract_url = create_contract_docusign(booking)
        booking.contract_url = contract_url
        booking.update()

        if booking.tenant.phone and booking.tenant.phone.startswith("+1"):
            message = f"Hi, it's Farid, I sent you a link to sign a contract. on {booking.tenant.email} Please, fill it out and sign"
            send_sms(booking, message, booking.tenant.phone)

    else:

        raise Exception(
            "Client wasn't notified about contract because of missing email, please add tenant email")

    return booking.contract_url


def create_contract_docusign(booking):
    api_client = ApiClient()
    api_client.set_base_path(DOCUSIGN_AUTH_SERVER)
    api_client.set_oauth_host_name(DOCUSIGN_AUTH_SERVER)
    jwt_values = get_token(DOCUSIGN_PRIVATE_KEY, api_client)
    api_client_with_token = create_api_client(BASE_PATH, jwt_values["access_token"])

   
    tenant = booking.tenant
    envelope_definition = make_envelope(booking)

    # print("Tenant", tenant.id, tenant.email, tenant.full_name)

    envelopes_api = EnvelopesApi(api_client_with_token)
    envelope_result = envelopes_api.create_envelope(
        account_id=DOCUSIGN_API_ACCOUNT_ID,
        envelope_definition=envelope_definition
    )
    if envelope_result.status == "sent":
        booking.contract_send_status = "Sent by Email"
        booking.update()
    else:
        print(f"Envelope status is {envelope_result.status}, email might not be sent.")

    contract_url = generate_contract_url(envelope_result.envelope_id)

    return contract_url

def generate_contract_url(envelope_id):
    return f"https://apps-d.docusign.com/send/documents/details/{envelope_id}"


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
            role_name="tenant",
            tabs=tabs
        )
        envelope_definition = EnvelopeDefinition(
            email_subject='Hi, it is Farid, Please sign a renting agreement',
            template_id=DOCUSIGN_TEMPLATE_ID,
            template_roles=[tenant],
            status='sent',
        )

        print("Envelope Definition Created")

        return envelope_definition



def get_token(private_key, api_client):

    private_key = private_key.replace("\\n", "\n")
    # private_key_obj = serialization.load_pem_private_key(
    #         private_key.encode(),
    #         password=None,
    #         backend=default_backend()
    #     )
        
    logger_common.debug("Private Key successfully loaded")


    try:
        token_response = get_jwt_token(private_key, SCOPES, DOCUSIGN_AUTH_SERVER, DOCUSIGN_INTEGRATION_KEY,DOCUSIGN_USER_ID)
    except Exception as e:
        print(e)
    
    
    access_token = token_response.access_token

    # Save API account ID
    user_info = api_client.get_user_info(access_token)
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
        {"tabLabel": "payment_terms", "value": booking.payment_str_for_contract},
        {"tabLabel": "contract_date", "value": timezone.now().strftime('%m/%d/%Y')},
        {"tabLabel": "agreement_number", "value": f"#A{booking.id}F"}
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


#https://apps-d.docusign.com/send/documents/details/8e7e7cb0-01e1-4943-a9ee-21dd430a98a4
#https://apps-d.docusign.com/send/prepare/8e7e7cb0-01e1-4943-a9ee-21dd430a98a4/correct/

