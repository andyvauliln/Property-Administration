from django.utils import timezone
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
import os
import requests
import json

SUBMISSION_URL_API_BASE_URL = "https://api.docuseal.co/submissions"
DOCUSEAL_API_KEY = os.environ.get("DOCUSEAL_API_KEY")


def create_contract(booking):
    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com":
        # Create and send agreement
        contract_url = create_and_send_agreement(booking)
        if contract_url:
            booking.contract_url = contract_url
            booking.contract_send_status = "Sent by Email"
            booking.update()
        else:
            booking.contract_send_status = "Not Sent"
            booking.update()
            raise Exception("Contract wasn't sent by email")
    else:
        raise Exception("Client wasn't notified about contract because of missing email, please add tenant email")

    return booking.contract_url

def create_and_send_agreement(booking):
    try:
        # Get Adobe Sign access token
        print("START CREATE AGREEMENT")
        data = prepare_data_for_agreement(booking)

        headers = {
            "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
            "Content-Type": "application/json",
        }
        print("headers", headers, SUBMISSION_URL_API_BASE_URL)
        response = requests.post(SUBMISSION_URL_API_BASE_URL, headers=headers, json=data)
        print("response", response)
        if response.status_code != 200:
            print("Detailed response:", response.json())

        if response.status_code == 200:
            response_data = response.json()
            print("response_data", response_data)
            print("response_data[0]", response_data[0]["embed_src"])
            return response_data[0]["embed_src"]
        else:
            return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None



def prepare_data_for_agreement(booking):
    data = {
        "template_id": os.environ.get("DOCUSEAL_TEMPLATE_ID"),
        "send_email": True,
        "send_sms": True if booking.tenant.phone and booking.tenant.phone.startswith("+1") else False,
        "submitters": [
            {
                "role": "tenant",
                "metadata": {
                            "booking_id":  f"#A{booking.id}F",
                        },   
                "phone": booking.tenant.phone or "", 
                "email": booking.tenant.email or "",
                "fields": [                        
                        {"name": "tenant", "default_value": "" if booking.tenant.full_name == "Not Availabale" or booking.tenant.full_name == "" else booking.tenant.full_name, "readonly": False},
                        {"name": "phone", "default_value": booking.tenant.phone or "", "readonly": False},
                        {"name": "email", "default_value": booking.tenant.email or "", "readonly": False},
                        {
                            "name": 'sender_name_1',
                            "default_value": f'IT Products development and Marketing LLC',
                            "readonly": True
                        },
                        {
                            "name": 'sender_name_2',
                            "default_value": f'IT Products development and Marketing LLC',
                            "readonly": True
                        },
                        {"name": "contract_date", "default_value": timezone.now().strftime('%m/%d/%Y'), "readonly": True},
                        {"name": "start_date", "default_value": booking.start_date.strftime('%m/%d/%Y'), "readonly": True},
                        {"name": "start_date_1", "default_value": booking.start_date.strftime('%m/%d/%Y'), "readonly": True},
                        {"name": "end_date", "default_value": booking.end_date.strftime('%m/%d/%Y'), "readonly": True},
                        {"name": "apartment_address", "default_value": booking.apartment.address, "readonly": True},
                        {"name": "payment_terms", "default_value": booking.payment_str_for_contract, "readonly": True},
                    ]
            },
            # {
            #     "role": "sender",  
            #     "phone": os.environ.get("TWILIO_MANAGER_PHONE"), 
            #     "email": os.environ.get("MANAGER_MAIL2"),
            #     "fields": [
            #             {
            #                 "name": 'sender_signature',
            #                 "default_value": f'http://{os.environ.get("PGHOST")}/static/signature.png',
            #                 "readonly": True
            #             },
                        
            #         ]
            # }
        ],
        
    }
    print("contract_data", data)

    return data
