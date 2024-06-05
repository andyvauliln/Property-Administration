from django.utils import timezone
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
import os
import requests
import json

# Adobe Sign API endpoints
#ADOBE_SIGN_API_BASE_URL = "https://api.na4.echosign.com/api/rest/v6"
ADOBE_SIGN_API_BASE_URL = "https://api.na4.adobesign.com/api/rest/v6"

ADOBE_SIGN_AUTH_URL = f"https://api.na4.adobesign.com/oauth/v2/token"
ADOBE_SIGN_AGREEMENTS_URL = f"{ADOBE_SIGN_API_BASE_URL}/agreements"

print("Adobe Sign Client ID:", os.environ.get("ADOBE_SIGN_CLIENT_ID"))
print("Adobe Sign Client Secret:", os.environ.get("ADOBE_SIGN_CLIENT_SECRET"))
print("Adobe Sign Refresh Token:", os.environ.get("ADOBE_SIGN_REFRESH_TOKEN"))
print("Adobe Sign Template ID:", os.environ.get("ADOBE_SIGN_TEMPLATE_ID"))

def create_contract(booking):
    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com":
        # Create and send agreement
        contract_url = create_and_send_agreement(booking)
        booking.contract_url = contract_url
        booking.update()

        # Send SMS notification
        if booking.tenant.phone and booking.tenant.phone.startswith("+1"):
            message = f"Hi, it's Farid, I sent you a link to sign a contract on {booking.tenant.email}. Please, fill it out and sign."
            send_sms(booking, message, booking.tenant.phone)
    else:
        raise Exception("Client wasn't notified about contract because of missing email, please add tenant email")

    return booking.contract_url

def create_and_send_agreement(booking):
    # Get Adobe Sign access token
    print("START CREATE AGREEMENT")
    access_token = get_adobe_sign_access_token()
    print("access_token", access_token)

    # Create agreement from template
    agreement = create_agreement_from_template(booking, access_token)
    print(agreement, "agreement")

    # Send agreement
    contract_url = send_agreement(agreement, access_token)
    print("contract_url", contract_url)

    return contract_url

def get_adobe_sign_access_token():
    client_id = os.environ["ADOBE_SIGN_CLIENT_ID"]
    client_secret = os.environ["ADOBE_SIGN_CLIENT_SECRET"]
    refresh_token = os.environ["ADOBE_SIGN_REFRESH_TOKEN"]

    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }

    response = requests.post("https://api.na4.adobesign.com/oauth/v2/refresh", data=payload)
    #response = requests.post(ADOBE_SIGN_AUTH_URL, data=payload)
    print("response", response)
    response_data = response.json()
    print("response_data_get_token", response_data)

    if response.status_code != 200:
        raise Exception(f"Failed to get Adobe Sign access token: {response_data}")

    return response_data["access_token"]

def create_agreement_from_template(booking, access_token):
    try:
        template_id = os.environ["ADOBE_SIGN_TEMPLATE_ID"]
        prefilled_data = prepare_data_for_agreement(booking)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload ={
            "name": f"Rent Agreement for Booking # {booking.id}",
            "fileInfos": [{"libraryDocumentId": template_id}],
            "participantSetsInfo": [
                {
                    "memberInfos":[{"email": booking.tenant.email}],
                    "order": 1,
                    "role": "SIGNER"
                }
            ],
            "signatureType": "ESIGN",
            "state": "IN_PROCESS",
            "status": "OUT_FOR_SIGNATURE",
            "mergeFieldInfo": prefilled_data,
            "message": "Hi it's Farid, Please, fill out, sign and make payment for this renting agreement"
            }
        print("Payload being sent to Adobe Sign API:", json.dumps(payload, indent=4))

        response = requests.post(ADOBE_SIGN_AGREEMENTS_URL, headers=headers, data=json.dumps(payload))
        print("create_agreement_response", response)
        response_data = response.json()
        print("create_agreement_response", response_data)

        if not response_data["id"]:
            raise Exception(f"Failed to create agreement: {response_data}")

        return response_data

    except requests.RequestException as e:
        raise Exception(f"Request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"An error occurred while creating agreement: {str(e)}")

def send_agreement(agreement, access_token):
    try:
        agreement_id = agreement["id"]
        agreement_url = f"{ADOBE_SIGN_AGREEMENTS_URL}/{agreement_id}/views"
        print("agreement_url", agreement_url)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # Check the status of the agreement
        status_url = f"{ADOBE_SIGN_AGREEMENTS_URL}/{agreement_id}"
        status_response = requests.get(status_url, headers=headers)
        status_data = status_response.json()
        print("Agreement status data:", json.dumps(status_data, indent=4))

        # Ensure the agreement is in a state where it can be authored and signed
        # if status_data.get("status") != "IN_PROCESS":
        #     raise Exception(f"Agreement is not in an authorable state: {status_data.get('status')}")

        payload = {
            "name": "ALL",
            "commonViewConfiguration": {
                "autoLoginUser": False
            }
        }

        response = requests.post(agreement_url, headers=headers, data=json.dumps(payload))
        response_data = response.json()
        print("send_response_data", response_data)

        # if response.status_code != 200:
        #     raise Exception(f"Failed to get agreement URL: {response_data}")

        return response_data["agreementViewList"][1]["url"]

    except requests.RequestException as e:
        raise Exception(f"Request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"An error occurred when sending agreement: {str(e)}")


def prepare_data_for_agreement(booking):
    prefilled_inputs = [
        {"fieldName": "owner", "defaultValue": booking.apartment.owner.full_name},
        {"fieldName": "tenant", "defaultValue": "" if booking.tenant.full_name == "Not Availabale" or booking.tenant.full_name == "" else booking.tenant.full_name},
        {"fieldName": "phone", "defaultValue": booking.tenant.phone or ""},
        {"fieldName": "email", "defaultValue": booking.tenant.email or ""},
        {"fieldName": "start_date", "defaultValue": booking.start_date.strftime('%m/%d/%Y')},
        {"fieldName": "end_date", "defaultValue": booking.end_date.strftime('%m/%d/%Y')},
        {"fieldName": "apartment_address", "defaultValue": booking.apartment.address},
        {"fieldName": "payment_terms", "defaultValue": booking.payment_str_for_contract},
        {"fieldName": "contract_date", "defaultValue": timezone.now().strftime('%m/%d/%Y')},
        {"fieldName": "agreement_number", "defaultValue": f"#A{booking.id}F"}
    ]

    return prefilled_inputs

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



