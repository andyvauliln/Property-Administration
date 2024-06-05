from docusign_esign import EnvelopesApi, EnvelopeDefinition, TemplateRole, Text, Tabs, EnvelopeEvent
from docusign_esign import ApiClient, AuthenticationApi, RecipientViewRequest, EventNotification, RecipientEvent
import os
from django.utils import timezone
import requests
from django.http import JsonResponse
import logging
import json
from mysite.models import Booking
from django.http import JsonResponse, HttpResponse



def get_user(access_token):
    url = "https://account-d.docusign.com/oauth/userinfo"
    auth = {"Authorization": "Bearer " + access_token}
    response = requests.get(url, headers=auth).json()

    return response


def get_docusign_token():
    api_client = ApiClient()
    api_client.set_base_path(os.environ["DOCUSIGN_API_URL"])
    private_key_content = os.environ["DOCUSIGN_PRIVATE_KEY"]
    private_key = private_key_content.replace("\\n", "\n")

    token = api_client.request_jwt_user_token(
        client_id=os.environ["DOCUSIGN_INTEGRATION_KEY"],
        user_id=os.environ["DOCUSIGN_USER_ID"],
        oauth_host_name=os.environ["DOCUSIGN_AUTH_SERVER"],
        private_key_bytes=private_key.encode('utf-8'),
        expires_in=3600,
        scopes=["signature"]
    )

    if token is None:
        print("Error while requesting token")
    else:
        api_client.set_default_header("Authorization", "Bearer " + token.access_token)
        user = get_user(token.access_token)
        print('user',user)

    return token.access_token


def create_api_client(access_token):
    """Create api client and construct API headers"""
    api_client = ApiClient()
    api_client.host = "https://demo.docusign.net/restapi"
    api_client.set_default_header(header_name="Authorization", header_value=f"Bearer {access_token}")

    return api_client


def docusign_callback(request):
    envelope_id = request.GET.get('envelopeId')
    secret_key = request.GET.get('secret_key')


    try:
        # Initialize your DocuSign API client
        token = get_docusign_token()
        api_client = create_api_client(token)
        envelopes_api = EnvelopesApi(api_client)

        # Fetch form data
        account_id = os.environ["DOCUSIGN_API_ACCOUNT_ID"]
        form_data_response = envelopes_api.get_form_data(account_id=account_id, envelope_id=envelope_id)

        # Process and parse form data
        recipient_form_data = form_data_response.recipient_form_data[0] if form_data_response.recipient_form_data else None
        form_data_items = recipient_form_data.form_data if recipient_form_data and recipient_form_data.form_data else []

        # Create a dictionary from form data items
        form_data_dict = {item.name: item.value for item in form_data_items}
        bookingid = int(form_data_dict['agreement_number'][2:-1])
        booking = Booking.objects.get(id=bookingid)
        if booking:
            booking.status = 'Waiting Payment'
            booking.save()
            tenant = booking.tenant
            tenant.full_name = form_data_dict['occupant']
            tenant.email = form_data_dict['email']
            tenant.phone = form_data_dict['phone']
            tenant.save()
            booking.save()

        return JsonResponse({'status': 'success', 'formData': form_data_dict})
    except Exception as e:
        print(e)
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

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
    response_data = response.json()
    print("response_data_get_token", response_data)

    if response.status_code != 200:
        raise Exception(f"Failed to get Adobe Sign access token: {response_data}")

    return response_data["access_token"]


def adobesign_callback(request):
    # if request.method != 'POST':
    #     return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)
    print(request, "request")
    if request.method == 'GET':
        # Handle verification of intent
        # client_id = request.headers.get('X-AdobeSign-ClientId')
        # if client_id == os.environ["ADOBE_SIGN_CLIENT_ID"]:
        #     response = HttpResponse()
        #     response['X-AdobeSign-ClientId'] = client_id
        #     return response
        # else:
        #     return JsonResponse({'status': 'error', 'message': 'Invalid client ID'}, status=400)
        response = HttpResponse()
        response['X-AdobeSign-ClientId'] = os.environ["ADOBE_SIGN_CLIENT_ID"]
        return response

    elif request.method == 'POST':

        try:
            payload = json.loads(request.body)
            print(payload, "payload")
            agreement_id = payload.get('agreement', {}).get('id')
            print(agreement_id, "agreement_id")
            secret_key = payload.get('secret_key')
            print(secret_key, "secret_key")

            if not agreement_id:
                return JsonResponse({'status': 'error', 'message': 'Agreement ID not found in the payload'}, status=400)

            # Get Adobe Sign access token
            token = get_adobe_sign_access_token()
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }

            # Fetch form data
            account_id = os.environ["ADOBE_SIGN_CLIENT_ID"]
            url = f"https://api.adobesign.com/api/rest/v6/agreements/{agreement_id}/formFields"
            response = requests.get(url, headers=headers)
            print(response, "response")
            form_fields_response = response.json()

            # Process and parse form fields
            form_fields_items = form_fields_response.get('fields', [])

            # Create a dictionary from form fields items
            form_fields_dict = {item['name']: item.get('defaultValue', '') for item in form_fields_items}
            print(form_fields_dict)
            bookingid = int(form_fields_dict['agreement_number'][2:-1])
            print(bookingid)
            booking = Booking.objects.get(id=bookingid)
            if booking:
                booking.status = 'Waiting Payment'
                booking.save()
                tenant = booking.tenant
                tenant.full_name = form_fields_dict['occupant']
                tenant.email = form_fields_dict['email']
                tenant.phone = form_fields_dict['phone']
                tenant.save()
                booking.save()

            return JsonResponse({'status': 'success', 'formData': form_fields_dict})
        except Exception as e:
            print(e)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)