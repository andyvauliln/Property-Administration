from django.utils import timezone
from calendar import monthrange
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import os
import requests
import json
from mysite.unified_logger import log_error, log_info, log_warning, logger

SUBMISSION_URL_API_BASE_URL = "https://api.docuseal.co/submissions"
DOCUSEAL_API_KEY = os.environ.get("DOCUSEAL_API_KEY")




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
        log_info("Creating DocuSeal contract", category='contract', details={'booking_id': booking.id})
        data = prepare_data_for_agreement(booking, template_id)

        headers = {
            "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
            "Content-Type": "application/json",
        }
        response = requests.post(SUBMISSION_URL_API_BASE_URL, headers=headers, json=data)

        if 200 <= response.status_code < 300:
            response_data = response.json()
            booking.contract_id = str(response_data[0]["submission_id"])
            booking.contract_url = response_data[0]["embed_src"]
            booking.contract_send_status = "Sent by Email"
            
            # Try to send SMS but don't fail the entire process if it fails
            if send_sms and booking.tenant.phone and booking.tenant.phone.startswith("+1"):
                try:
                    from mysite.views.messaging import sendContractToTwilio
                    sendContractToTwilio(booking, response_data[0]["embed_src"])
                    log_info("SMS notification sent with contract", category='sms', details={'phone': booking.tenant.phone})
                except Exception as sms_error:
                    log_warning(
                        f"SMS notification failed but contract created successfully",
                        category='sms',
                        details={'error': str(sms_error), 'phone': booking.tenant.phone}
                    )
            
            # Save only the contract-related fields to avoid triggering complex save logic
            from django.db import models
            models.Model.save(booking, update_fields=['contract_id', 'contract_url', 'contract_send_status', 'updated_at'])
            log_info(
                f"Contract created successfully",
                category='contract',
                details={'booking_id': booking.id, 'contract_id': booking.contract_id}
            )
            return True  # Success
        else:
            error_detail = ""
            try:
                error_detail = response.json()
            except:
                error_detail = response.text
            booking.contract_send_status = "Not Sent"
            from django.db import models
            models.Model.save(booking, update_fields=['contract_send_status', 'updated_at'])
            raise Exception(f"Contract wasn't sent by email. Status code: {response.status_code}. Error: {error_detail}")
    except Exception as e:
        booking.contract_send_status = "Not Sent"
        from django.db import models
        models.Model.save(booking, update_fields=['contract_send_status', 'updated_at'])
        
        # Build detailed error message
        error_details = {
            'booking_id': booking.id,
            'template_id': template_id,
            'tenant': booking.tenant.full_name,
            'tenant_email': booking.tenant.email,
            'tenant_phone': booking.tenant.phone,
        }
        
        if response is not None:
            error_details['response_status'] = response.status_code
            try:
                error_details['response_body'] = response.json()
            except:
                error_details['response_text'] = response.text[:200]
        
        if data is not None:
            error_details['request_data'] = json.dumps(data, indent=2)[:500]
        
        log_error(
            e,
            f"Contract Creation Failed - Booking {booking.id}",
            source='web',
            severity='high',
            additional_info=error_details
        )
        raise
      




def _format_date(value):
    return value.strftime('%B %d %Y') if value else ""


def _format_amount(value):
    if value is None:
        return ""
    return str(value)


def _prefill_email(email):
    if not email or email == "not_availabale@gmail.com" or "@example.com" in email:
        return ""
    return email


def _get_payment_amount(booking, type_name):
    amount = _get_payment_amount_value(booking, type_name)
    return _format_amount(amount)


def _get_payment_amount_value(booking, type_name):
    payment = booking.payments.filter(
        payment_type__name=type_name,
        payment_type__type="In",
    ).order_by("payment_date", "id").first()
    return payment.amount if payment else None


def _get_monthly_price_value(booking):
    rent_amount = _get_payment_amount_value(booking, "Rent")
    if rent_amount is not None:
        return rent_amount
    if booking.apartment:
        price_on_start = booking.apartment.get_price_on_date(booking.start_date)
        if price_on_start is not None:
            return price_on_start
        if booking.apartment.default_price:
            return booking.apartment.default_price
    return None


def _get_lease_term(booking):
    if not booking.start_date or not booking.end_date:
        return ""
    months = (
        (booking.end_date.year - booking.start_date.year) * 12
        + booking.end_date.month
        - booking.start_date.month
    )
    return str(max(1, months))


def _get_month_end(value):
    if not value:
        return ""
    return value.replace(day=monthrange(value.year, value.month)[1])


def _is_prorated_first_month_start(start_date):
    return bool(start_date and start_date.day != 1)


def _get_double_amount(value):
    if value in (None, ""):
        return ""
    return _format_amount(value * 2)


def _get_first_month_price(start_date, monthly_price):
    if not start_date or monthly_price in (None, ""):
        return ""
    days_in_month = Decimal(monthrange(start_date.year, start_date.month)[1])
    remaining_days = Decimal(days_in_month - start_date.day + 1)
    daily_price = Decimal(monthly_price) / days_in_month
    first_month_price = (daily_price * remaining_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return _format_amount(first_month_price)


def prepare_data_for_agreement(booking, template_id):
    template_id_str = str(template_id)
    data = {
        "template_id": template_id,
        "send_email": True if booking.tenant.email and booking.tenant.email != "not_availabale@gmail.com" and "@example.com" not in booking.tenant.email else False,
        "send_sms": False,
        "submitters": [
            {
                "role": "Tenant" if template_id_str == "3538155" else "tenant",
                "metadata": {
                            "booking_id":  f"#A{booking.id}F",
                        },   
                "phone": booking.tenant.phone or "", 
                "email": booking.tenant.email or "",
                "fields": get_fields(booking, template_id)
                
            },
        ],
        
    }
    if template_id_str == "3538155":
        data["submitters"].append({
            "role": "Owner",
            "metadata": {
                "booking_id": f"#A{booking.id}F",
            },
            "email": "apartmentincityplace@gmail.com" # booking.apartment.owner.email or "",
            # "fields": [
            #     {"name": "owner_signature", "default_value": "FG", "readonly": True},
            #     {"name": "owner_initials", "default_value": "FG", "readonly": True},
            # ],
        })
    return data


def get_fields(booking, template_id):
    # Convert template_id to string for consistent comparison
    template_id_str = str(template_id)  
    
    if template_id_str == "120946" or template_id_str == "3465654": #application form
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
    elif template_id_str == "3538155":
        monthly_price_value = _get_monthly_price_value(booking)
        monthly_price = _format_amount(monthly_price_value)
        prorate_first_month = _is_prorated_first_month_start(booking.start_date)
        return [
                    {"name": "lease_term", "default_value": _get_lease_term(booking), "readonly": True},
                    {"name": "lease_start_day", "default_value": _format_date(booking.start_date), "readonly": True},
                    {"name": "lease_end_day", "default_value": _format_date(booking.end_date), "readonly": True},
                    {"name": "landlord_name", "default_value": "Farid Gazizov", "readonly": True},
                    {"name": "tenant_name", "default_value": "" if booking.tenant.full_name == "Not Availabale" or booking.tenant.full_name == "" else booking.tenant.full_name, "readonly": False},
                    {"name": "tenant_email", "default_value": _prefill_email(booking.tenant.email), "readonly": False},
                    {"name": "tenant_phone", "default_value": booking.tenant.phone or "", "readonly": False},
                    {"name": "apartment_number", "default_value": booking.apartment.apartment_n or "", "readonly": True},
                    {"name": "apartment_street", "default_value": booking.apartment.street or "", "readonly": True},
                    {"name": "apartment_name", "default_value": booking.apartment.name or "", "readonly": True},
                    {"name": "city", "default_value": booking.apartment.city or "", "readonly": True},
                    {"name": "zip_code", "default_value": booking.apartment.zip_index or "", "readonly": True},
                    {"name": "monthly_price", "default_value": monthly_price, "readonly": True},
                    {"name": "rent_start", "default_value": _format_date(booking.start_date) if prorate_first_month else "", "readonly": True},
                    {"name": "prorated_end_date", "default_value": _format_date(_get_month_end(booking.start_date)) if prorate_first_month else "", "readonly": True},
                    {"name": "prorated_before_date", "default_value": _format_date(booking.start_date - timedelta(days=1)) if prorate_first_month else "", "readonly": True},
                    {"name": "first_month_price", "default_value": _get_first_month_price(booking.start_date, monthly_price_value) if prorate_first_month else "", "readonly": True},
                    {"name": "taxes_amount", "default_value": monthly_price, "readonly": True},
                    {"name": "security_deposit", "default_value": _get_payment_amount(booking, "Damage Deposit"), "readonly": True},
                    {"name": "advance_rent_amount", "default_value": monthly_price, "readonly": True},
                    {"name": "early_termination_fee", "default_value": _get_double_amount(monthly_price_value), "readonly": True},
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
        raise Exception(f"Template id '{template_id}' (type: {type(template_id).__name__}) is not supported. Expected '120946' or '118378' or '3465654' or '3538155'")


def delete_contract(id):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.delete(f"{SUBMISSION_URL_API_BASE_URL}/{id}", headers=headers)
    
    if not (200 <= response.status_code < 300):
        log_error(
            Exception(f"Delete contract failed with status {response.status_code}"),
            f"Delete Contract {id}",
            source='contract',
            severity='medium',
            additional_info={'response': response.json()}
        )
    else:
        log_info(f"Contract deleted successfully", category='contract', details={'contract_id': id})


def update_contract(booking):
    submitter_id, status, role = get_submitter_id(booking)
    
    if submitter_id:
        if status == "completed":
            log_info(f"Contract already completed", category='contract', details={'submitter_id': submitter_id})
            return False
        update_submitter(booking, submitter_id, role)
        return True
    else:
        raise Exception("Submitter id is not found")



def get_submitter_id(booking):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.get(f"{SUBMISSION_URL_API_BASE_URL}/{booking.contract_id}", headers=headers)
    
    if 200 <= response.status_code < 300:
        submitters = response.json()["submitters"]
        submitter = submitters[0]
        if submitter:
            return submitter['id'], submitter.get('status'), submitter.get('role')
        else:
            log_warning(f"No submitter found", category='contract', details={'tenant_email': booking.tenant.email})
            return None, None, None
    else:
        log_error(
            Exception(f"Get submitter failed with status {response.status_code}"),
            f"Get Submitter - Contract {booking.contract_id}",
            source='contract',
            additional_info={'response': response.json()}
        )
        return None, None, None


def update_submitter(booking, submitter_id, role=None):
    headers = {
        "X-Auth-Token": f"{DOCUSEAL_API_KEY}",
        "Content-Type": "application/json",
    }

    if role == "Tenant":
        data = {"fields": get_fields(booking, "3538155")}
    else:
        data = { "fields": [
            {"name": "start_date", "default_value": booking.start_date.strftime('%B %d %Y'), "readonly": True},
            {"name": "end_date", "default_value": booking.end_date.strftime('%B %d %Y'), "readonly": True},
            # DO WE NEED IN LTR THIS? CHECK LATER UPDATE
            {"name": "payment_terms", "default_value": booking.payment_str_for_contract, "readonly": True},
        ]}

    response = requests.put(f"https://api.docuseal.co/submitters/{submitter_id}", headers=headers, json=data)
    
    if 200 <= response.status_code < 300:
        log_info("Contract submitter updated", category='contract', details={'submitter_id': submitter_id})
    else:
        log_error(
            Exception(f"Update submitter failed with status {response.status_code}"),
            f"Update Submitter {submitter_id}",
            source='contract',
            additional_info={'response': response.json()}
        )



