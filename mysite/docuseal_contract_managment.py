from django.utils import timezone
import os
import requests
import json
import logging

SUBMISSION_URL_API_BASE_URL = "https://api.docuseal.co/submissions"
DOCUSEAL_API_KEY = os.environ.get("DOCUSEAL_API_KEY")

logger_sms = logging.getLogger('mysite.sms_webhooks')

def print_info(*args):
    message = ' '.join(str(arg) for arg in args)
    print(message)
    logger_sms.debug(message)




def create_contract(booking, template_id, send_sms=False):
    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com" and "@example.com" not in booking.tenant.email:
        # Create and send agreement
        create_and_send_agreement(booking, template_id, send_sms)
    else:
        raise Exception("Client wasn't notified about contract because of missing email or phone, please add correct tenant email or phone")

    return booking.contract_url

def create_and_send_agreement(booking, template_id, send_sms=False):
    data = None
    response = None
    try:
        # Get Adobe Sign access token
        print_info("START CREATE AGREEMENT")
        data = prepare_data_for_agreement(booking, template_id)
        print_info("data", data)

        headers = {
            "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
            "Content-Type": "application/json",
        }
        response = requests.post(SUBMISSION_URL_API_BASE_URL, headers=headers, json=data)
        print_info("DocuSeal API response status code:", response.status_code)
        print_info("DocuSeal API response status code:", response)

        if 200 <= response.status_code < 300:
            response_data = response.json()
            print_info("response_data", response_data)
            booking.contract_id = str(response_data[0]["submission_id"])
            booking.contract_url = response_data[0]["embed_src"]
            booking.contract_send_status = "Sent by Email"
            print_info(f"Saving contract_id {booking.contract_id} to booking {booking.id}")
            
            # Try to send SMS but don't fail the entire process if it fails
            if send_sms and booking.tenant.phone and booking.tenant.phone.startswith("+1"):
                try:
                    from mysite.views.messaging import sendContractToTwilio
                    sendContractToTwilio(booking, response_data[0]["embed_src"])
                    print_info(f"SMS notification sent successfully to {booking.tenant.phone}")
                except Exception as sms_error:
                    print_info(f"Warning: SMS notification failed but contract was created successfully. Error: {str(sms_error)}")
                    # Don't re-raise - contract was created successfully
            
            # Save only the contract-related fields to avoid triggering complex save logic
            from django.db import models
            models.Model.save(booking, update_fields=['contract_id', 'contract_url', 'contract_send_status', 'updated_at'])
            print_info(f"Contract saved - Booking {booking.id} now has contract_id: {booking.contract_id}")
            print_info(f"✓ Contract successfully created and sent for booking {booking.id}")
            return True  # Success
        else:
            error_detail = ""
            try:
                error_detail = response.json()
            except:
                error_detail = response.text
            print_info("Detailed response:", error_detail)
            booking.contract_send_status = "Not Sent"
            from django.db import models
            models.Model.save(booking, update_fields=['contract_send_status', 'updated_at'])
            raise Exception(f"Contract wasn't sent by email. Status code: {response.status_code}. Error: {error_detail}")
    except Exception as e:
        print_info(f"An error occurred in create_and_send_agreement: {str(e)}")
        booking.contract_send_status = "Not Sent"
        from django.db import models
        models.Model.save(booking, update_fields=['contract_send_status', 'updated_at'])
        
        # Build detailed error message
        error_details = [
            f"Booking ID: {booking.id}",
            f"Template ID: {template_id}",
            f"Tenant: {booking.tenant.full_name}",
            f"Tenant Email: {booking.tenant.email}",
            f"Tenant Phone: {booking.tenant.phone}",
        ]
        
        if response is not None:
            error_details.append(f"Response Status: {response.status_code}")
            try:
                error_details.append(f"Response Body: {response.json()}")
            except:
                error_details.append(f"Response Text: {response.text[:200]}")
        else:
            error_details.append("Response: None (request may have failed before getting response)")
        
        if data is not None:
            error_details.append(f"Request Data: {json.dumps(data, indent=2)[:500]}")
        
        error_details.append(f"Original Error: {str(e)}")
        error_details.append(f"Error Type: {type(e).__name__}")
        
        detailed_message = "Contract wasn't sent by email. Details:\n" + "\n".join(f"  - {detail}" for detail in error_details)
        raise Exception(detailed_message)
      




def prepare_data_for_agreement(booking, template_id):
    data = {
        "template_id": template_id,
        "send_email": True if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com" and "@example.com" not in booking.tenant.email else False,
        "send_sms": False,
        "submitters": [
            {
                "role": "tenant",
                "metadata": {
                            "booking_id":  f"#A{booking.id}F",
                        },   
                "phone": booking.tenant.phone or "", 
                "email": booking.tenant.email or "",
                "fields": get_fields(booking, template_id)
                
            },
        ],
        
    }
    return data


def get_fields(booking, template_id):
    # Convert template_id to string for consistent comparison
    template_id_str = str(template_id)
    print_info("template_id", template_id, "converted to", template_id_str)
    
    if template_id_str == "120946": #application form
        return [
                    {"name": "tenant", "default_value": "" if booking.tenant.full_name == "Not Availabale" or booking.tenant.full_name == "" else booking.tenant.full_name, "readonly": False},
                    {"name": "phone", "default_value": booking.tenant.phone or "", "readonly": False},
                    {"name": "email", "default_value": booking.tenant.email if booking.tenant.email != "" or booking.tenant.email != "not_availabale@gmail.com" or booking.tenant.email != "@example.com" else "", "readonly": False},
                    {"name": "start_date", "default_value": booking.start_date.strftime('%B %d %Y'), "readonly": True},
                    {"name": "end_date", "default_value": booking.end_date.strftime('%B %d %Y'), "readonly": True},
                    {"name": 'sender_name', "default_value": f'IT Products development and Marketing LLC',"readonly": True },
                    {"name": 'owner', "default_value": f'{booking.apartment.owner.full_name}',"readonly": True},
                    {"name": 'apartment_number',"default_value": f'{booking.apartment.apartment_n}',"readonly": True},
                    {"name": 'building_number',"default_value": f'{booking.apartment.building_n}',"readonly": True},
                    {"name": "payment_terms", "default_value": booking.payment_str_for_contract, "readonly": True},
                    {"name": "apartment_address", "default_value": booking.apartment.address, "readonly": True},
                    # {"name": "owner_signature", "default_value": booking.apartment.owner.full_name, "readonly": True},
                ]
    elif template_id_str == "118378": #occupancy agreement
        return [           
                    {"name": "tenant", "default_value": "" if booking.tenant.full_name == "Not Availabale" or booking.tenant.full_name == "" else booking.tenant.full_name, "readonly": False},
                    {"name": "phone", "default_value": booking.tenant.phone or "", "readonly": False},
                    {"name": "email", "default_value": booking.tenant.email if booking.tenant.email != "" or booking.tenant.email != "not_availabale@gmail.com" or booking.tenant.email != "@example.com" else "", "readonly": False},
                    {"name": 'owner', "default_value": f'{booking.apartment.owner.full_name}',"readonly": True},
                    {"name": 'sender_name',"default_value": f'IT Products development and Marketing LLC',"readonly": True},
                    {"name": "start_date", "default_value": booking.start_date.strftime('%B %d %Y'), "readonly": True},
                    {"name": "end_date", "default_value": booking.end_date.strftime('%B %d %Y'), "readonly": True},
                    {"name": "apartment_address", "default_value": booking.apartment.address, "readonly": True},
                    {"name": "payment_terms", "default_value": booking.payment_str_for_contract, "readonly": True},
                    # {"name": "owner_signature", "default_value": booking.apartment.owner.full_name, "readonly": True},
                ]
    else:
        raise Exception(f"Template id '{template_id}' (type: {type(template_id).__name__}) is not supported. Expected '120946' or '118378'")


def delete_contract(id):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.delete(f"{SUBMISSION_URL_API_BASE_URL}/{id}", headers=headers)
    print_info("Delete contract response status code:", response.status_code)
    if not (200 <= response.status_code < 300):
        print_info("Detailed error response:", response.json())
    else:
        print_info(f"Contract with id {id} was deleted successfully")


def update_contract(booking):
    submitter_id, status = get_submitter_id(booking)
    print_info("submitter_id", submitter_id, "status", status)
    if submitter_id:
        if status == "completed":
            print_info(f"Skipping update - submitter {submitter_id} has already completed the submission")
            return False
        update_submitter(booking, submitter_id)
        return True
    else:
        raise Exception("Submitter id is not found")



def get_submitter_id(booking):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.get(f"{SUBMISSION_URL_API_BASE_URL}/{booking.contract_id}", headers=headers)
    print_info("Get submitter response status code:", response.status_code)
    if 200 <= response.status_code < 300:
        submitters = response.json()["submitters"]
        #tenant_submitter = next((s for s in submitters if s["name"] == "Farid Gazizov"), None)
        submitter = submitters[0]
        if submitter:
            print_info(f"Found tenant submitter: {submitter} {submitter['id']}")
            return submitter['id'], submitter.get('status')
        else:
            print_info(f"No submitter found for tenant email: {booking.tenant.email}")
    else:
        print_info("Detailed error response:", response.json())
        return None, None
        


    print_info(f"Contract with id {id} was deleted")


def update_submitter(booking, submitter_id):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    data = { "fields": [
        {"name": "start_date", "default_value": booking.start_date.strftime('%B %d %Y'), "readonly": True},
        {"name": "end_date", "default_value": booking.end_date.strftime('%B %d %Y'), "readonly": True},
        {"name": "payment_terms", "default_value": booking.payment_str_for_contract, "readonly": True},
    ]}

    response = requests.put(f"https://api.docuseal.co/submitters/{submitter_id}", headers=headers, json=data)
    print_info("Update submitter response status code:", response.status_code)
    if 200 <= response.status_code < 300:
        response = response.json()
        print_info("Successfully updated submitters", response)
        
    else:
        print_info("Detailed error response:", response.json())



