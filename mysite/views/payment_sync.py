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
from .utils import get_model_fields
from django.core import serializers


@user_has_role('Admin')
def sync_payments(request):
    data = None
    payments_to_update = []
    if request.method == 'POST': 
        
        if request.POST.get('payments_to_update'):
            payments_to_update = json.loads(request.POST.get('payments_to_update'))
            print('payments_to_update', payments_to_update)
            update_payments(request, payments_to_update)
        else:
            if request.FILES.get('csv_file'):
                csv_file = request.FILES['csv_file']
                if not csv_file.name.endswith('.csv'):
                    messages.error(request, 'File is not CSV type')

                amount_delta = int(request.POST.get('amount_delta', '100'))
                date_delta = int(request.POST.get('date_delta', 4))
                print(amount_delta, date_delta)
                
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
                    db_payments = Payment.objects.filter(payment_date__range=(start_date - timedelta(days=date_delta), end_date + timedelta(days=date_delta)))
                    possible_matches_db_to_file = find_possible_matches_db_to_file(db_payments, file_payments, amount_delta, date_delta)
                    # possible_matches_file_to_db = find_possible_matches_file_to_db(db_payments, file_payments, amount_delta, date_delta)

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
                    'possible_matches_db_to_file': possible_matches_db_to_file,
                    'payment_methods': get_json(payment_methods),
                    'apartments': get_json(apartments),
                    'payment_types': get_json(payment_types),
                    
                }

    context = {
        'data': data,
        'payments_to_update': payments_to_update
    }

    return render(request, 'payment_sync/index.html', context)

def get_json(db_model):
    
    items_json_data = serializers.serialize('json', db_model)

    # Convert the serialized data to a Python list of dictionaries
    data_list = json.loads(items_json_data)

    # Extract the 'fields' from each item in the list
    items_list = [{'id': item['pk'], **item['fields']} for item in data_list]

    for item, original_obj in zip(items_list, db_model):
        # if hasattr(original_obj, 'assigned_cleaner'):
        #     item['assigned_cleaner'] = original_obj.assigned_cleaner.id if original_obj.assigned_cleaner else None
        # if hasattr(original_obj, 'tenant'):
        #     item['tenant_full_name'] = original_obj.tenant.full_name
        #     item['tenant_email'] = original_obj.tenant.email
        #     item['tenant_phone'] = original_obj.tenant.phone
        # item['links'] = original_obj.links
        if hasattr(original_obj, 'apartmentName'):
            item['apartment_name'] = original_obj.apartmentName


    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list)
    return items_json
  

def preprocess_csv_line(line):
    splited = line.split(',')
    if len(splited) > 4:
        corrected_line = splited[0] + ',' + ' '.join(splited[1:-2]) + ',' + splited[-2] + ',' + splited[-1]
    else:
        corrected_line = line
    return corrected_line

def update_payments(request, payments_to_update):
    for payment_info in payments_to_update:
        try:
            if payment_info['id'] and 'id_' not in payment_info['id']:
                payment = Payment.objects.get(id=payment_info['id'])
                if payment:
                    payment.amount = float(payment_info['amount'])
                    payment.payment_date = datetime.strptime(payment_info['payment_date'], '%m/%d/%Y').date()
                    payment.payment_type_id = payment_info['payment_type']
                    payment.notes = payment_info['notes']
                    payment.payment_method_id = payment_info['payment_method']
                    payment.bank_id = payment_info['bank']
                    payment.apartment_id = payment_info['apartment']
                    # payment.booking_id = payment_info['booking']
                    payment.payment_status = payment_info['payment_status']
                    payment.save()
                    messages.success(request, f"Updated Payment: {payment.id}")
                else:
                    messages.error(request, f"Cand Find in DB Payment with Id: {payment.id}")
            else:
                payment = Payment.objects.create(
                    amount=float(payment_info['amount']),
                    payment_date=payment_info['payment_date'],
                    payment_type=payment_info['payment_type'],
                    notes=payment_info['notes'],
                    payment_method_id=payment_info['payment_method'],
                    bank_id=payment_info['bank'],
                    apartment_id=payment_info['apartment'],
                    # booking_id=payment_info['booking'],
                    payment_status=payment_info['payment_status'],
                )
                messages.success(request, f"Added new Payment: {payment.id}")
        except Exception as e:
            messages.error(request, f"Failed to update/create payment: {payment.id} due to {str(e)}")


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

        for payment_method in payment_methods:
            if payment_method.name in description:
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
        
        # Convert amount to float, handling empty strings
        amount_float = float(amount.strip()) if amount.strip() else 0.0
        
        if amount_float == 0:
            continue
        elif amount_float > 0:
            payment_type = payment_types.filter(name="Income").first()
        else:
            payment_type = payment_types.filter(name="Expense").first()

        ba_bank = payment_methods.filter(name="BA").first()
        payment_data.append({
            'id': extracted_id or f'id_{idx}',
            'payment_date': datetime.strptime(date.strip(), '%m/%d/%Y'),
            'payment_type': payment_type.id,
            'payment_type_name': payment_type.name,
            'notes': description.strip(),
            'amount': abs(amount_float),
            'payment_method': payment_method_to_assign.id if payment_method_to_assign else None,
            'payment_method_name': payment_method_to_assign.name if payment_method_to_assign else None,
            'bank': ba_bank.id,
            'bank_name': ba_bank.name,
            'apartment': apartment_to_assign.id if apartment_to_assign else None,
            'apartment_name': apartment_to_assign.name if apartment_to_assign else None,
        })
    #print('retrieved payment_data from csv', payment_data)
    return payment_data


def get_start_end_dates(request, payment_data):
    if not payment_data and len(payment_data) < 2:
        # If payment_data is empty, log an error and return None or default dates
        messages.error(request, "No payment data available to determine dates.")
        return None, None  # You can return default dates or handle this case as needed in your application

    start_date = payment_data[0]['payment_date']
    end_date = payment_data[-1]['payment_date']
    print('start_date and end_date for the period', start_date, end_date)
    return start_date, end_date

def find_possible_matches_file_to_db(db_payments, file_payments, amount_delta, date_delta):
    possible_matches = []
    
    for db_payment in db_payments:
        matches = get_matches_file_to_db(db_payment, file_payments, amount_delta, date_delta)
        possible_matches.append({'db_payment': db_payment, 'matches': matches})
        

    possible_matches.sort(key=lambda x: len(x['matches']), reverse=True)
    return possible_matches

def find_possible_matches_db_to_file(db_payments, file_payments, amount_delta, date_delta):
    possible_matches = []
    
    for file_payment in file_payments:
        matches = get_matches_db_to_file(file_payment, db_payments, amount_delta, date_delta)
        possible_matches.append({'file_payment': file_payment, 'matches': matches})
        

    possible_matches.sort(key=lambda x: len(x['matches']), reverse=True)
    return possible_matches


def get_matches_file_to_db(db_payment, file_payments, amount_delta, date_delta):
    matches = []
   
    for payment_from_file in file_payments:
        match_obj = {}
        if payment_from_file['id'] == db_payment.id:
           match_obj['file_payment'] = payment_from_file
           match_obj['id'] = 'Matched'
        
        payment_diff = abs(float(payment_from_file['amount']) - abs(float(db_payment.amount)))        
        payment_date_datetime = datetime.combine(db_payment.payment_date, datetime.min.time())
        date_diff = payment_from_file["payment_date"] - payment_date_datetime
        
        if payment_diff <= amount_delta and abs(date_diff.days) <= date_delta:
            match_obj['file_payment'] = payment_from_file
            match_obj['amount'] = 'Exact Match' if payment_diff == 0 else f'Match +-{int(payment_diff)}'
            match_obj['payment_date'] = 'Exact Match' if payment_diff.days == 0 else f'Match +-{abs(payment_diff.days)}d'
            

        if db_payment.apartmentName and payment_from_file.get('apartment_name') == db_payment.apartmentName:
            match_obj['file_payment'] = payment_from_file
            match_obj['apartment'] = 'Exact Match'

        if 'file_payment' not in match_obj:
            #print("No Matches")
            d = 1
        else:
            matches.append(match_obj)

    return matches
    

def get_matches_db_to_file(file_payment, db_payments, amount_delta, date_delta):
    matches = []
   
    for payment_from_db in db_payments:
        match_obj = {'score': 0}
        if payment_from_db.id == file_payment['id']:
           match_obj['db_payment'] = payment_from_db
           match_obj['id'] = 'Matched'
           match_obj['score'] += 3
        
        payment_diff = abs(float(payment_from_db.amount) - abs(float(file_payment['amount'])))        
        payment_date_datetime = datetime.combine(payment_from_db.payment_date, datetime.min.time())
        date_diff = file_payment['payment_date'] - payment_date_datetime
        
        if payment_diff <= amount_delta and abs(date_diff.days) <= date_delta:
            match_obj['db_payment'] = payment_from_db
            match_obj['amount'] = 'Exact Match' if payment_diff == 0 else f'Match +-{int(payment_diff)}'
            match_obj['payment_date'] = 'Exact Match' if date_diff.days == 0 else f'Match +-{abs(date_diff.days)}d'
            match_obj['score'] += 1 if abs(date_diff.days) <= 1 else 0
            match_obj['score'] += 2 if payment_diff == 0 else 1

            if payment_from_db.apartmentName and file_payment['apartment_name'] == payment_from_db.apartmentName:
                match_obj['db_payment'] = payment_from_db
                match_obj['apartment'] = 'Exact Match'
                match_obj['score'] += 1 if payment_diff == 0 else 0

        if 'db_payment' not in match_obj:
            #print("No Matches")
            d = 1
        else:
            matches.append(match_obj)

    return matches