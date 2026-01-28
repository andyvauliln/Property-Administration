from django.shortcuts import render
from ..models import PaymentMethod, Payment, Apartment, PaymenType
from ..forms import PaymentForm
from datetime import datetime
from ..decorators import user_has_role
from django.contrib import messages
import json
from datetime import timedelta
import re
import csv
import io
from .utils import get_model_fields
from django.core import serializers

# Separator for grouping multiple payment keys
PAYMENT_KEY_SEPARATOR = "###||###"


@user_has_role('Admin')
def sync_payments(request):
    data = {
        'amount_delta': 100,
        'date_delta': 4,
        'with_confirmed': True,
    }
    payments_to_update = []
    if request.method == 'POST': 
        
        if request.POST.get('payments_to_update'):
            payments_to_update = json.loads(request.POST.get('payments_to_update'))
            update_payments(request, payments_to_update)
        else:
            if request.FILES.get('csv_file'):
                csv_file = request.FILES['csv_file']
                if not csv_file.name.endswith('.csv'):
                    messages.error(request, 'File is not CSV type')

                amount_delta = int(request.POST.get('amount_delta') or '100')
                date_delta = int(request.POST.get('date_delta') or 4)
                with_confirmed = request.POST.get('with_confirmed') == 'True'
                
                payment_methods = PaymentMethod.objects.all()
                apartments = Apartment.objects.all()
                payment_types = PaymenType.objects.all()

                file_payments = get_payment_data(request,csv_file, payment_methods, apartments, payment_types)
                
                start_date, end_date = get_start_end_dates(request, file_payments)
                db_payments = []
                if start_date is None or end_date is None:
                    messages.error(request, "Invalid date range for finding matches.")
                    possible_matches_db_to_file = []
                else:
                    if with_confirmed:
                        db_payments = Payment.objects.filter(payment_date__range=(start_date - timedelta(days=45), end_date + timedelta(days=45)))
                        db_payments, file_payments = remove_handled_payments(db_payments, file_payments)
                    else:
                        db_payments = Payment.objects.filter(payment_date__range=(start_date - timedelta(days=45), end_date + timedelta(days=45)), payment_status='Pending')
                        db_payments, file_payments = remove_handled_payments(db_payments, file_payments)
                    lessDataForMatching = [payment for payment in db_payments if (start_date - timedelta(days=10)).date() <= payment.payment_date <= (end_date + timedelta(days=10)).date()]
                    possible_matches_db_to_file = find_possible_matches_db_to_file(lessDataForMatching, file_payments, amount_delta, date_delta)

                db_payments_json = get_json(db_payments)
                file_payments_json = json.dumps(file_payments, default=str)
                model_fields = get_model_fields(PaymentForm(request))

                

                data = {
                    'file_payments_json': file_payments_json,
                    'db_payments_json': db_payments_json,
                    'total_file_payments': len(file_payments),
                    'model_fields': model_fields,
                    'start_date': start_date,
                    'end_date': end_date,
                    'amount_delta': amount_delta,
                    'date_delta': date_delta,
                    'with_confirmed': with_confirmed,
                    'possible_matches_db_to_file': possible_matches_db_to_file,
                    'payment_methods': get_json(payment_methods),
                    'apartments': get_json(apartments),
                    'payment_types': get_json(payment_types),
                    
                }
    context = {
        'data': data,
        'payments_to_update': payments_to_update,
        'title': "Payments Sync"
    }

    return render(request, 'payment_sync/index.html', context)

#05/06/20244500Check 283
def remove_handled_payments(db_payments, file_payments):
    # Collect ALL merged_payment_keys from the database (not just from the date-filtered queryset)
    # This ensures we catch payments that were merged but have dates outside the current file's range
    all_merged_payments = Payment.objects.filter(
        payment_status="Merged",
        merged_payment_key__isnull=False
    ).exclude(merged_payment_key='')
    
    db_payment_keys = set()
    for payment in all_merged_payments:
        # Check if this is a grouped payment key (contains separator)
        if PAYMENT_KEY_SEPARATOR in payment.merged_payment_key:
            # Split the grouped key and add each individual key
            individual_keys = payment.merged_payment_key.split(PAYMENT_KEY_SEPARATOR)
            db_payment_keys.update(individual_keys)
        else:
            # Single payment key
            db_payment_keys.add(payment.merged_payment_key)
    
    # Filter out db_payments with status "Merged"
    db_payments_cleaned = [payment for payment in db_payments if payment.payment_status != "Merged"]
    
    # Filter out file_payments where merged_payment_key matches any of the db keys
    file_payments_cleaned = [
        payment for payment in file_payments 
        if payment['merged_payment_key'] not in db_payment_keys
    ]
    
    return db_payments_cleaned, file_payments_cleaned

def get_json(db_model):
    
    items_json_data = serializers.serialize('json', db_model)

    # Convert the serialized data to a Python list of dictionaries
    data_list = json.loads(items_json_data)

    # Extract the 'fields' from each item in the list
    items_list = [{'id': item['pk'], **item['fields']} for item in data_list]

    for item, original_obj in zip(items_list, db_model):
        if hasattr(original_obj, 'booking') and original_obj.booking:
            item["tenant_name"] = original_obj.booking.tenant.full_name
        else:
            item["tenant_name"] = ""
        if hasattr(original_obj, 'apartmentName'):
            item['apartment_name'] = original_obj.apartmentName


    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list)
    return items_json
  
def preprocess_csv_line(line):
    # Parse the line respecting quoted fields
    reader = csv.reader(io.StringIO(line))
    try:
        parts = next(reader)
    except StopIteration:
        return line
    
    # If more than 4 columns, merge middle columns as description
    if len(parts) > 4:
        date = parts[0]
        description = ' '.join(parts[1:-2])
        amount = parts[-2]
        running_bal = parts[-1]
        # Rebuild with proper CSV quoting
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([date, description, amount, running_bal])
        return output.getvalue().strip()
    return line

def update_payments(request, payments_to_update):
    for payment_info in payments_to_update:
        payment_id = None
        try:
            # Validate amount is not zero
            amount_str = str(payment_info['amount']).replace(',', '')
            amount = float(amount_str)
            if amount == 0:
                messages.error(request, f"Cannot create/update payment with 0 amount. Skipping.")
                continue
                
            if payment_info['id'] and (isinstance(payment_info['id'], int) or (isinstance(payment_info['id'], str) and 'id_' not in payment_info['id'])):
                payment = Payment.objects.get(id=payment_info['id'])
                payment_id = payment.id
                if payment:
                    payment.amount = amount
                    # Determine payment_date, fallback to file_date, error if both empty
                    date_str = payment_info.get('payment_date') or payment_info.get('file_date', '')
                    if not date_str:
                        raise ValueError("Payment date is required (payment_date or file_date)")
                    payment.payment_date = parse_payment_date(date_str)
                    payment.payment_type_id = payment_info['payment_type']
                    payment.notes = payment_info['notes']
                    payment.payment_method_id = payment_info['payment_method']
                    payment.bank_id = payment_info['bank']
                    payment.apartment_id = payment_info['apartment']
                    payment.payment_status = "Merged"
                    # Always construct merged_payment_key from file data if available
                    # This ensures the key is updated when bank format changes (e.g., masked confirmation numbers)
                    if payment_info.get('file_date') and payment_info.get('file_notes'):
                        merged_payment_key = (
                            parse_date(payment_info.get('file_date', '')) + 
                            remove_trailing_zeros_from_str(payment_info.get('file_amount', '')) + 
                            payment_info.get('file_notes', '')
                        )
                    else:
                        merged_payment_key = payment_info.get('merged_payment_key', '')
                    payment.merged_payment_key = merged_payment_key
                    payment.save(updated_by=request.user if request.user else None)
                    messages.success(request, f"Updated Payment: {payment.id}")
                else:
                    messages.error(request, f"Cand Find in DB Payment with Id: {payment.id}")
            else:
                # Determine payment_date, fallback to file_date, error if both empty
                date_str = payment_info.get('payment_date') or payment_info.get('file_date', '')
                if not date_str:
                    raise ValueError("Payment date is required (payment_date or file_date)")
                # Always construct merged_payment_key from file data if available
                if payment_info.get('file_date') and payment_info.get('file_notes'):
                    merged_payment_key = (
                        parse_date(payment_info.get('file_date', '')) + 
                        remove_trailing_zeros_from_str(payment_info.get('file_amount', '')) + 
                        payment_info.get('file_notes', '')
                    )
                else:
                    merged_payment_key = payment_info.get('merged_payment_key', '')
                    
                payment = Payment(
                    amount=amount,
                    payment_date= parse_payment_date(date_str),
                    payment_type_id=payment_info['payment_type'],
                    notes=payment_info['notes'],
                    payment_method_id=payment_info['payment_method'] or None,
                    bank_id=payment_info['bank'] or None,
                    apartment_id=payment_info['apartment'] or None,
                    merged_payment_key=merged_payment_key,
                    payment_status="Merged",
                )
                payment.save(updated_by=request.user if request.user else None)
                messages.success(request, f"Created new Payment: {payment.id}")
        except Exception as e:
            messages.error(request, f"Failed to {'update' if payment_id else 'create'}  payment: {payment_id or ''} due  {str(e)}")

def remove_trailing_zeros_from_str(amount_str):
    amount_float = float(str(amount_str).replace(',', ''))
    return ('%f' % abs(amount_float)).rstrip('0').rstrip('.')

def parse_payment_date(date_str):
    """Parse date string into a date object, handling multiple formats"""
    if not date_str:
        return None
        
    # Try parsing with different formats
    formats = [
        '%Y-%m-%d %H:%M:%S',  # 2025-01-02 00:00:00
        '%Y-%m-%d',           # 2025-01-02
        '%B %d %Y',           # January 02 2025
        '%m/%d/%Y',           # 01/02/2025
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    
    # If none of the formats work, raise an error
    raise ValueError(f"Date format for '{date_str}' is not supported. Expected formats: YYYY-MM-DD HH:MM:SS, YYYY-MM-DD, Month DD YYYY, or MM/DD/YYYY")

def get_payment_data(request, csv_file, payment_methods, apartments, payment_types):
    file_data = csv_file.read().decode("utf-8")
    lines = file_data.split("\n")

    corrected_lines = [preprocess_csv_line(line) for line in lines if line.strip()] 


    reader = csv.reader(corrected_lines)  # Use csv.reader on the corrected lines
    
    # Find the index where the relevant data starts and skip to it
    lines_list = list(reader)
    try:
        start_index = next(i for i, line in enumerate(lines_list) if 'Date' in line and 'Description' in line and 'Amount' in line and 'Running Bal.' in line) + 1
    except StopIteration:
        messages.error(request, "CSV file does not contain the expected header.")
        return []
    payment_data = []
    
    # Process each line after the header
    for idx, parts in enumerate(lines_list[start_index:], start=start_index):
        if not parts or all(not part.strip() for part in parts):
            continue  # Skip empty lines
        date, description, amount, running_bal = parts[0], parts[1], parts[2] if len(parts) > 2 else '', parts[3] if len(parts) > 3 else ''
        # print(f'Date: {date}, Description: {description}, Amount: {float(amount.strip()) if amount.strip() else 0.0}, Running Bal: {running_bal}')
        # print(f'Date: {datetime.strptime(date.strip(), "%m/%d/%Y")}, Description: {description}, Amount: {amount}, Running Bal: {running_bal}')
        payment_method_to_assign = None
        apartment_to_assign = None
        extracted_id = None
        payment_type = None

        if 'paypal' in description.strip().lower():
            print(f'Paypal: {description}')

        for payment_method in payment_methods:
            if payment_method.name.lower() in description.strip().lower():
                payment_method_to_assign = payment_method
                break
        if 'deposit *mobile' in description.strip().lower():
            payment_method_to_assign = payment_methods.filter(name="Check").first()

        if payment_method_to_assign is None:
            for payment_method in payment_methods:
                keywords_array = [keyword.strip() for keyword in payment_method.keywords.split(",")] if payment_method.keywords else []
                if any(keyword.strip().lower() in description.strip().lower() for keyword in keywords_array):
                    payment_method_to_assign = payment_method
                    break

        for apartment in apartments:
            if apartment.name in description:
                apartment_to_assign = apartment
                break
       
        if "@S3" in description:
            match = re.search(r"@S3(\d+)", description)
            if match:
                extracted_id = int(match.group(1))
        
        # Convert amount to float, handling empty strings and comma separators
        amount_str = amount.strip().replace(',', '')
        amount_float = float(amount_str) if amount_str else 0.0
        
        if amount_float == 0:
            continue
        elif amount_float > 0:
            payment_type = payment_types.filter(name="Other", type="In").first()
        else:
            payment_type = payment_types.filter(name="Other", type="Out").first()

        ba_bank = payment_methods.filter(name="BA").first()
        payment_data.append({
            'id': extracted_id or f'id_{idx}',
            'payment_date': datetime.strptime(date.strip(), '%m/%d/%Y'),
            'payment_type': payment_type.id,
            'payment_type_name': f'{payment_type.name} ({payment_type.type})',
            'payment_type_type': payment_type.type,
            'notes': description.strip(),
            'amount': abs(amount_float),
            'payment_method': payment_method_to_assign.id if payment_method_to_assign else None,
            'payment_method_name': payment_method_to_assign.name if payment_method_to_assign else None,
            'merged_payment_key': datetime.strptime(date.strip(), '%m/%d/%Y').strftime('%m/%d/%Y').zfill(10) + remove_trailing_zeros_from_str(amount_float) + description.strip(),
            'bank': ba_bank.id,
            'bank_name': ba_bank.name,
            'apartment': apartment_to_assign.id if apartment_to_assign else None,
            'apartment_name': apartment_to_assign.name if apartment_to_assign else None,
        })
    return payment_data


def get_start_end_dates(request, payment_data):
    if not payment_data and len(payment_data) < 2:
        # If payment_data is empty, log an error and return None or default dates
        messages.error(request, "No payment data available to determine dates.")
        return None, None  # You can return default dates or handle this case as needed in your application

    start_date = payment_data[0]['payment_date']
    end_date = payment_data[-1]['payment_date']
    return start_date, end_date

def parse_date(date_str):
    # Handle empty strings
    if not date_str:
        return ""
        
    # Try parsing with the format '2024-07-01 00:00:00'
    try:
        return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S').strftime('%m/%d/%Y')
    except ValueError:
        try:
            return  datetime.strptime(date_str, '%B %d %Y').strftime('%m/%d/%Y')
        except ValueError:
            pass
    
    # Try parsing with the format '2024-08-01'
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%m/%d/%Y')
    except ValueError:
        pass
    
    # Try parsing with the format 'July 24 2024'
    try:
        return datetime.strptime(date_str, '%B %d %Y').strftime('%m/%d/%Y')
    except ValueError:
        pass
    
    raise ValueError(f"Date format for '{date_str}' is not supported")

def find_possible_matches_db_to_file(db_payments, file_payments, amount_delta, date_delta):
    possible_matches = []
    
    for file_payment in file_payments:
        matches = get_matches_db_to_file(file_payment, db_payments, amount_delta, date_delta)
        possible_matches.append({'file_payment': file_payment, 'matches': matches})
        

    possible_matches.sort(key=lambda x: len(x['matches']), reverse=True)
    return possible_matches


def is_payment_type_match(payment_from_db, file_payment):
   if file_payment['payment_type_name'] == "Other (In)":
       return payment_from_db.payment_type.type == "In"
   elif file_payment['payment_type_name'] == "Other (Out)":
       return payment_from_db.payment_type.type == "Out"


def get_matches_db_to_file(file_payment, db_payments, amount_delta, date_delta):
    matches = []
   
    for payment_from_db in db_payments:
        if not is_payment_type_match(payment_from_db, file_payment):
            continue
        match_obj = {'score': 0}
        if payment_from_db.id == file_payment['id']:
            match_obj['db_payment'] = payment_from_db
            match_obj['id'] = 'Matched'
            match_obj['score'] += 10
            continue

        keywords_array = [keyword.strip() for keyword in payment_from_db.keywords.split(",")] if payment_from_db.keywords else []
        if payment_from_db.apartment and payment_from_db.apartment.keywords and len(payment_from_db.apartment.keywords.strip()) > 0:
            apartment_keywords_array = [keyword.strip() for keyword in payment_from_db.apartment.keywords.split(",")] if payment_from_db.apartment.keywords else []
            keywords_array.extend(apartment_keywords_array)
        if payment_from_db.booking:
            booking_keywords_array = [keyword.strip() for keyword in payment_from_db.booking.keywords.split(",")] if payment_from_db.booking.keywords else []
            keywords_array.extend(booking_keywords_array)
            if payment_from_db.booking.apartment:
                apartment_keywords_array = [keyword.strip() for keyword in payment_from_db.booking.apartment.keywords.split(",")] if payment_from_db.booking.apartment.keywords else []
                keywords_array.extend(apartment_keywords_array)
            if payment_from_db.booking.tenant:
                keywords_array.extend([keyword.strip() for keyword in payment_from_db.booking.tenant.full_name.split(" ")])
                if payment_from_db.booking.tenant.email:
                    keywords_array.append(payment_from_db.booking.tenant.email.strip())
                if payment_from_db.booking.tenant.phone:
                    keywords_array.append(payment_from_db.booking.tenant.phone.strip())
        if payment_from_db.payment_type and payment_from_db.payment_type.keywords:
            keywords_array.extend([keyword.strip() for keyword in payment_from_db.payment_type.keywords.split(",")] if payment_from_db.payment_type.keywords else [])

        match_obj['keywords'] = ""
        for keyword in keywords_array:
            if file_payment['notes'].lower().find(keyword.lower()) != -1:
                
                match_obj["db_payment"] = payment_from_db
                match_obj['keywords'] += f'{keyword} '
                match_obj['score'] += 5
        
        
        payment_diff = abs(float(payment_from_db.amount) - abs(float(file_payment['amount'])))        
        payment_date_datetime = datetime.combine(payment_from_db.payment_date, datetime.min.time())
        date_diff = file_payment['payment_date'] - payment_date_datetime
        payment_diff = round(abs(float(payment_from_db.amount) - abs(float(file_payment['amount']))))
        
        if payment_diff <= amount_delta and abs(date_diff.days) <= date_delta:
            match_obj['db_payment'] = payment_from_db
            match_obj['amount'] = 'Exact Match' if payment_diff < 1 else f'Match +-{str(payment_diff)}'
            match_obj['payment_date'] = 'Exact Match' if date_diff.days == 0 else f'Match +-{abs(date_diff.days)}d'
            match_obj['score'] += 1 if abs(date_diff.days) <= 1 else 0
            match_obj['score'] += 2 if payment_diff == 0 else 1

            if payment_from_db.apartmentName and file_payment['apartment_name'] == payment_from_db.apartmentName:
                match_obj['db_payment'] = payment_from_db
                match_obj['apartment'] = 'Exact Match'
                match_obj['score'] += 1
            
            if payment_from_db.notes and payment_from_db.notes.strip().find(file_payment['notes'].strip()) != -1:
                match_obj['db_payment'] = payment_from_db
                match_obj['notes'] = 'Exact Match'
                match_obj['score'] += 1 if payment_diff == 0 else 0

        if 'db_payment' not in match_obj:
            print("No Matches")
        else:
            matches.append(match_obj)

    # Sort matches by score in descending order
    matches.sort(key=lambda x: x['score'], reverse=True)
    return matches



# def find_possible_matches_file_to_db(db_payments, file_payments, amount_delta, date_delta):
#     possible_matches = []
    
#     for db_payment in db_payments:
#         matches = get_matches_file_to_db(db_payment, file_payments, amount_delta, date_delta)
#         possible_matches.append({'db_payment': db_payment, 'matches': matches})
        

#     possible_matches.sort(key=lambda x: len(x['matches']), reverse=True)
#     return possible_matches

# def get_matches_file_to_db(db_payment, file_payments, amount_delta, date_delta):
#     matches = []
   
#     for payment_from_file in file_payments:
#         match_obj = {}
#         if payment_from_file['id'] == db_payment.id:
#            match_obj['file_payment'] = payment_from_file
#            match_obj['id'] = 'Matched'
        
#         payment_diff = abs(float(payment_from_file['amount']) - abs(float(db_payment.amount)))        
#         payment_date_datetime = datetime.combine(db_payment.payment_date, datetime.min.time())
#         date_diff = payment_from_file["payment_date"] - payment_date_datetime
        
#         if payment_diff <= amount_delta and abs(date_diff.days) <= date_delta:
#             match_obj['file_payment'] = payment_from_file
#             match_obj['amount'] = 'Exact Match' if payment_diff == 0 else f'Match +-{int(payment_diff)}'
#             match_obj['payment_date'] = 'Exact Match' if payment_diff.days == 0 else f'Match +-{abs(payment_diff.days)}d'
            

#         if db_payment.apartmentName and payment_from_file.get('apartment_name') == db_payment.apartmentName:
#             match_obj['file_payment'] = payment_from_file
#             match_obj['apartment'] = 'Exact Match'

#         if 'file_payment' not in match_obj:
#             #print("No Matches")
#             d = 1
#         else:
#             matches.append(match_obj)

#     return matches
    
