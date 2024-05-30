from docusign_esign import EnvelopesApi, EnvelopeDefinition, TemplateRole, Text, Tabs, EnvelopeEvent
from docusign_esign import ApiClient, AuthenticationApi, RecipientViewRequest, EventNotification, RecipientEvent
import os
from django.utils import timezone
import requests
from django.http import JsonResponse
import logging
from mysite.models import Booking



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
