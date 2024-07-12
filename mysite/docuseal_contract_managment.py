from django.utils import timezone
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
import os
import requests
import json

SUBMISSION_URL_API_BASE_URL = "https://api.docuseal.co/submissions"
DOCUSEAL_API_KEY = os.environ.get("DOCUSEAL_API_KEY")


def create_contract(booking, template_id):
    print("template_id", template_id)
    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com" and "@example.com" not in booking.tenant.email:
        # Create and send agreement
        create_and_send_agreement(booking, template_id)
    else:
        raise Exception("Client wasn't notified about contract because of missing email, please add correct tenant email")

    return booking.contract_url

def create_and_send_agreement(booking, template_id):
    try:
        # Get Adobe Sign access token
        print("START CREATE AGREEMENT")
        data = prepare_data_for_agreement(booking, template_id)
        print("data", data)

        headers = {
            "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
            "Content-Type": "application/json",
        }
        response = requests.post(SUBMISSION_URL_API_BASE_URL, headers=headers, json=data)

        if response.status_code == 200:
            response_data = response.json()
            print("response_data", response_data)
            booking.contract_id = response_data[0]["submission_id"]
            booking.contract_url = response_data[0]["embed_src"]
            booking.contract_send_status = "Sent by Email"
            booking.update()
        else:
            print("Detailed response:", response.json())
            booking.contract_send_status = "Not Sent"
            booking.update()
            raise Exception("Contract wasn't sent by email")
    except Exception as e:
        print(f"An error occurred: {e}")
        booking.contract_send_status = "Not Sent"
        booking.update()
        raise Exception("Contract wasn't sent by email")
      



def prepare_data_for_agreement(booking, template_id):
    data = {
        "template_id": template_id,
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
                ]
                
            },
            {
                "role": "sender",
                "phone": "", 
                "send_email": False,
                "send_sms": False,
                "completed": True,
                "email": "gfa779@hotmail.com",
                "fields": get_fields(booking, template_id)
            },
        ],
        
    }
    return data


def get_fields(booking, template_id):
    print("template_id", template_id, template_id == 120946)
    
    if template_id == "120946": #application form
        return [
                    {"name": "start_date", "default_value": booking.start_date.strftime('%Y-%m-%d'), "readonly": False},
                    {"name": "end_date", "default_value": booking.end_date.strftime('%Y-%m-%d'), "readonly": False},
                    {"name": 'sender_name', "default_value": f'IT Products development and Marketing LLC',"readonly": False },
                    {"name": 'owner', "default_value": f'{booking.apartment.owner.full_name}',"readonly": False},
                    {"name": 'apartment_number',"default_value": f'{booking.apartment.apartment_n}',"readonly": False},
                    {"name": 'building_number',"default_value": f'{booking.apartment.building_n}',"readonly": False},
                    {"name": "payment_terms", "default_value": booking.payment_str_for_contract, "readonly": False},
                    {"name": "apartment_address", "default_value": booking.apartment.address, "readonly": False},
                    {"name": "owner_signature", "default_value": booking.apartment.owner.full_name, "readonly": False},
                ]
    elif template_id == "118378": #occupancy agreement
        return [                        
                    {"name": 'owner', "default_value": f'{booking.apartment.owner.full_name}',"readonly": False},
                    {"name": 'sender_name',"default_value": f'IT Products development and Marketing LLC',"readonly": False},
                    {"name": "start_date", "default_value": booking.start_date.strftime('%Y-%m-%d'), "readonly": False},
                    {"name": "end_date", "default_value": booking.end_date.strftime('%Y-%m-%d'), "readonly": False},
                    {"name": "apartment_address", "default_value": booking.apartment.address, "readonly": False},
                    {"name": "payment_terms", "default_value": booking.payment_str_for_contract, "readonly": False},
                    {"name": "owner_signature", "default_value": booking.apartment.owner.full_name, "readonly": False},
                ]
    else:
        raise Exception("Template id is not supported")


def delete_contract(id):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.delete(f"{SUBMISSION_URL_API_BASE_URL}/{id}", headers=headers)
    print("response", response)
    if response.status_code != 200:
        print("Detailed response:", response.json())

    print(f"Contract with id {id} was deleted")

def rectreate_contract(booking):
    pass

