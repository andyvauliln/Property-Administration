from django.utils import timezone
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
import os
import requests
import json
import logging
import time
import re
from twilio.base.exceptions import TwilioRestException

SUBMISSION_URL_API_BASE_URL = "https://api.docuseal.co/submissions"
DOCUSEAL_API_KEY = os.environ.get("DOCUSEAL_API_KEY")

logger_sms = logging.getLogger('mysite.sms_webhooks')
account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
manager_phone = os.environ["MANAGER_PHONE"]
manager_phone2 = os.environ["MANAGER_PHONE2"]
twilio_phone_secondary = os.environ["TWILIO_PHONE_SECONDARY"]

client = Client(account_sid, auth_token)

def print_info(message):
    print(message)
    logger_sms.debug(message)

def validate_phone_number(phone):
    """
    Validate and format phone number for Twilio
    Returns None if invalid, formatted phone if valid
    """
    if not phone:
        return None
   
    # Remove any whitespace
    phone = str(phone).strip()
   
    # If it's already in E.164 format, validate it
    if phone.startswith('+'):
        # Must be + followed by 1-15 digits
        if re.match(r'^\+[1-9]\d{1,14}$', phone):
            return phone
        else:
            return None
   
    # If it's a US number without +1, add it
    # Remove any non-digit characters first
    digits_only = re.sub(r'\D', '', phone)
   
    # If it's 10 digits, assume US number
    if len(digits_only) == 10:
        return f"+1{digits_only}"
   
    # If it's 11 digits and starts with 1, assume US number
    if len(digits_only) == 11 and digits_only.startswith('1'):
        return f"+{digits_only}"
   
    # Otherwise, return None as invalid
    return None

def create_conversation_with_participants(friendly_name, participants_config):
    """
    Create a conversation with multiple participants in a single API call
    
    Args:
        friendly_name (str): Name for the conversation
        participants_config (list): List of participant configurations
            Each participant can be:
            - {"phone": "+1234567890"} for phone number participants
            - {"identity": "ASSISTANT", "projected_address": "+1234567890"} for identity-based participants
    
    Returns:
        str: conversation_sid of the created conversation
    """
    try:
        participant_list = []
        
        for config in participants_config:
            # Validate phone numbers before creating JSON
            if "phone" in config:
                validated_phone = validate_phone_number(config["phone"])
                if not validated_phone:
                    print_info(f"Invalid phone number in config: {config}")
                    continue
                config["phone"] = validated_phone
            
            if "projected_address" in config:
                validated_projected = validate_phone_number(config["projected_address"])
                if not validated_projected:
                    print_info(f"Invalid projected address in config: {config}")
                    continue
                config["projected_address"] = validated_projected
            
            if "identity" in config and "projected_address" in config:
                # Identity-based participant with projected address
                participant_json = f'{{"identity": "{config["identity"]}", "messaging_binding": {{"projected_address": "{config["projected_address"]}"}}}}'
            elif "phone" in config:
                # Phone number participant
                participant_json = f'{{"messaging_binding": {{"address": "{config["phone"]}"}}}}'
            else:
                print_info(f"Invalid participant config: {config}")
                continue
                
            participant_list.append(participant_json)
        
        print_info(f"Participant list: {participant_list}")
        if not participant_list:
            raise ValueError("No valid participants provided in create_conversation_with_participants")
        
        conversation_with_participant = client.conversations.v1.conversation_with_participants.create(
            friendly_name=friendly_name,
            participant=participant_list,
        )
        
        conversation_sid = conversation_with_participant.sid
        print_info(f'Created conversation "{friendly_name}" with {len(participant_list)} participants: {conversation_sid}')
        return conversation_sid
        
    except Exception as e:
        print_info(f"Error creating conversation with participants: {e}")
        raise

def create_conversation_config(friendly_name, tenant_phone):
    """
    Create a conversation with all participants in a single API call
    
    Args:
        friendly_name (str): Name for the conversation
        tenant_phone (str): Tenant's phone number
    
    Returns:
        str: conversation_sid of the created conversation
    """
    try:
         # Create new group conversation with all participants
        participants_config = []
        twilio_phone = "+13153524379"
        manager_phone = "+17282001917"
        
        # Add customer
        participants_config.append({
            "phone": tenant_phone
        })
        
        # Add manager  
        participants_config.append({
            "phone": manager_phone
        })
        
        # Add assistant
        participants_config.append({
            "identity": "ASSISTANT",
            "projected_address": twilio_phone
        })
        
        conversation_sid = create_conversation_with_participants(friendly_name, participants_config)
        
        print_info(f'Created conversation "{friendly_name}" with {len(participants_config)} participants: {conversation_sid}')
        return conversation_sid
        
    except Exception as e:
        print_info(f"Error creating conversation with participants: {e}")
        raise


def create_contract(booking, template_id, send_sms=False):
    if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com" and "@example.com" not in booking.tenant.email:
        # Create and send agreement
        create_and_send_agreement(booking, template_id, send_sms)
    else:
        raise Exception("Client wasn't notified about contract because of missing email or phone, please add correct tenant email or phone")

    return booking.contract_url

def create_and_send_agreement(booking, template_id, send_sms=False):
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
            if send_sms and booking.tenant.phone and booking.tenant.phone.startswith("+1"):
                sendContractToTwilio(booking, response_data[0]["embed_src"])
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
      

def sendContractToTwilio(booking, contract_url):
    try:
        conversation_sid = create_conversation_config(
            f" {booking.tenant.full_name or 'Tenant'} Chat Apt: {booking.apartment.name}",
            booking.tenant.phone
        )
        
        if conversation_sid:
            print_info(f"Conversation created: {conversation_sid}")
            send_messsage_by_sid(conversation_sid, "ASSISTANT", f"Hi, {booking.tenant.full_name or 'Dear guest'}, this is your contract for booking apartment {booking.apartment.name}. from {booking.start_date} to {booking.end_date}. Please sign it here: {contract_url}", twilio_phone_secondary, booking.tenant.phone)
        else:
            print_info("Conversation wasn't created")
    except TwilioException as e:
        print_info(f"Error sending contract message: {e}")
        raise Exception(f"Error sending contract message: {e}")

def sendWelcomeMessageToTwilio(booking):
    try:
        if booking.tenant.phone and booking.tenant.phone.startswith("+1"):
            conversation_sid = create_conversation_config(
                f" {booking.tenant.full_name or 'Tenant'} Chat Apt: {booking.apartment.name}",
                booking.tenant.phone
            )
            
            if conversation_sid:
                print_info(f"Conversation created: {conversation_sid}")
                send_messsage_by_sid(conversation_sid, "ASSISTANT", f"Hi, {booking.tenant.full_name or 'Dear guest'}, This chat for renting apartment {booking.apartment.name}. from {booking.start_date} to {booking.end_date}.", twilio_phone_secondary, booking.tenant.phone)
            else:
                print_info("Conversation wasn't created")
        else:
            print_info("Tenant phone is not valid")
            raise Exception("Tenant phone is not valid")
    except TwilioException as e:
        print_info(f"Error sending contract message: {e}")
        raise Exception(f"Error sending contract message: {e}")

def send_messsage_by_sid(conversation_sid, author, message, sender_phone, receiver_phone):
    try:
        # Retry sending in case the conversation is still initializing
        max_attempts = 6
        delay_seconds = 0.5
        for attempt in range(1, max_attempts + 1):
            try:
                twilio_message = client.conversations.v1.conversations(
                    conversation_sid
                ).messages.create(
                    body=message,
                    author=author,
                )
                print_info(f"Message sent via Twilio: {twilio_message.sid}")
                break
            except TwilioRestException as e:
                is_initializing = (getattr(e, "status", None) == 409) or ("initializing" in str(e).lower())
                if is_initializing and attempt < max_attempts:
                    print_info(
                        f"Conversation {conversation_sid} is initializing; retrying in {delay_seconds}s (attempt {attempt}/{max_attempts})"
                    )
                    time.sleep(delay_seconds)
                    delay_seconds = min(delay_seconds * 2, 4.0)
                    continue
                else:
                    print_info(f"Error sending message via Twilio (attempt {attempt}): {e}")
                    raise
        # Save message to database - using a mock conversation object for compatibility
        # class MockConversation:
        #     def __init__(self, sid):
        #         self.sid = sid
        
        # mock_conversation = MockConversation(conversation_sid)
        # if twilio_message and twilio_message.sid:
        #     create_db_message(mock_conversation, twilio_message, sender_phone, receiver_phone, message, sender_type='GPT BOT', message_type='CONTRACT')
        # else:
        #     create_db_message(mock_conversation, twilio_message, sender_phone, receiver_phone, message, sender_type='GPT BOT', message_type='CONTRACT', message_status="ERROR")
        #     raise Exception("Message wasn't sent")
        
    except TwilioException as e:
        print_info(f"Error sending message via Twilio: {e}")
        raise Exception(f"Error sending message via Twilio: {e}")

def create_db_message(conversation, twilio_message, sender_phone, receiver_phone, message, booking=None, context=None, sender_type='GPT BOT', message_type='NO_NEED_ACTION', message_status="SENDED"):
    try:
        from .models import Chat  # Local import to avoid circular dependency
        chat = Chat.objects.create(
            twilio_conversation_sid=conversation.sid,
            twilio_message_sid=twilio_message.sid,
            booking=booking,
            sender_phone=sender_phone,
            receiver_phone=receiver_phone,
            message=message,
            context=context,
            sender_type=sender_type,
            message_type=message_type,
            message_status=message_status,
        )
        chat.save()
        print_info(
            f"\n Message Saved to DB. Sender: {chat.sender_phone} Receiver: {chat.receiver_phone}. Message Status: {message_status}, Message Type: {message_type} Context: {context}  Sender Type: {sender_type} \n{message}\n")
    except Exception as e:
        print_info(f"Error creating DB message: {e}")
        raise Exception(f"Error creating DB message: {e}")

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
            # {
            #     "role": "sender",
            #     "phone": "",
            #     "name": "Farid Gazizov",
            #     "send_email": False,
            #     "send_sms": False,
            #     "completed": True,
            #     "email": "gfa779@hotmail.com",
            #     "fields": get_fields(booking, template_id)
            # },
        ],
        
    }
    return data


def get_fields(booking, template_id):
    print("template_id", template_id, template_id == 120946)
    
    if template_id == "120946": #application form
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
    elif template_id == "118378": #occupancy agreement
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


def update_contract(booking):
    submitter_id  = get_submitter_id(booking)
    print("submitter_id", submitter_id)
    if submitter_id:
        update_submitter(booking, submitter_id)
    else:
        raise Exception("Submitter id is not found")



def get_submitter_id(booking):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.get(f"{SUBMISSION_URL_API_BASE_URL}/{booking.contract_id}", headers=headers)
    print("response", response)
    if response.status_code == 200:
        submitters = response.json()["submitters"]
        #tenant_submitter = next((s for s in submitters if s["name"] == "Farid Gazizov"), None)
        submitter = submitters[0]
        if submitter:
            print(f"Found tenant submitter: {submitter} {submitter['id']}")
            return submitter['id']
        else:
            print(f"No submitter found for tenant email: {booking.tenant.email}")
    else:
        print("Detailed error response:", response.json())
        return None
        


    print(f"Contract with id {id} was deleted")


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
    # print("response", response)
    if response.status_code == 200:
        response = response.json()
        print("Successfully updated submitters", response)
        
    else:
        print("Detailed error response:", response.json())



