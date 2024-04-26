from django.shortcuts import render
from ..models import PaymentMethod, Payment, Apartment, PaymenType
from datetime import datetime
import calendar
from ..decorators import user_has_role
from .utils import aggregate_data, aggregate_summary, assign_color_classes
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from django.http import HttpResponseRedirect
from django.urls import reverse
from urllib.parse import urlencode
from django.contrib import messages
import json
from datetime import timedelta
import re
import csv
from io import StringIO

@user_has_role('Admin')
def sync_payments(request):
    data = None
    if request.method == 'POST': 
        if request.FILES.get('csv_file'):
            csv_file = request.FILES['csv_file']
            if not csv_file.name.endswith('.csv'):
                messages.error(request, 'File is not CSV type')
            
            payment_methods = PaymentMethod.objects.all()
            apartments = Apartment.objects.all()
            payment_types = PaymenType.objects.all()

            payment_data = get_payment_data(request,csv_file, payment_methods, apartments, payment_types)
            
            start_date, end_date = get_start_end_dates(payment_data)
            possible_matches, not_matched_payments = find_possible_matches(payment_data, start_date, end_date)
            data = {
                
                'payment_data': payment_data,
                'start_date': start_date,
                'end_date': end_date,
                'not_matched_payments': not_matched_payments,
                'possible_matches': possible_matches,
                'payment_methods': payment_methods,
                'apartments': apartments,
                'payment_types': payment_types,
            }
        if request.POST.get('payments_to_update'):
            payments_to_update = json.loads(request.POST.get('payments_to_update'))
            print('payments_to_update', payments_to_update)
            update_payments(request, payments_to_update)


    context = {
        'data': data,
    }

    return render(request, 'payment_sync.html', context)

def preprocess_csv_line(line):
    # This function attempts to correct common CSV formatting issues in a single line
    # Escape improperly escaped quotes within the data
    corrected_line = re.sub(r'(?<!"),(?!")', '","', line)
    corrected_line = corrected_line.replace('""', '"')  # Handle double double-quotes
    return corrected_line

def update_payments(request, payments_to_update):
    for payment_info in payments_to_update:
        try:
            if payment_info['id']:
                payment = Payment.objects.get(id=payment_info['id'])
                if payment:
                    payment.amount = float(payment_info['amount'])
                    #add other fields
                    payment.save()
                    messages.success(request, f"Updated Payment: {payment.id}")
                else:
                    messages.error(request, f"Cand Find in DB Payment with Id: {payment.id}")
            else:
                payment = Payment.objects.create(
                    amount=float(payment_info['amount'])
                    # add other fields
                )
                messages.success(request, f"Added new Payment: {payment.id}")
        except Exception as e:
            messages.error(request, f"Failed to update/create payment: {payment.id} due to {str(e)}")



def get_payment_data(request, csv_file, payment_methods, apartments, payment_types):
    file_data = csv_file.read().decode("utf-8")
    lines = file_data.split("\n")
    corrected_lines = [preprocess_csv_line(line) for line in lines if line.strip()]  # Preprocess each line

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
        
        if amount_float > 0:
            payment_type = payment_types.filter(type="Income").first()
        else:
            payment_type = payment_types.filter(type="Expense").first()

        payment_data.append({
            'id': extracted_id or f'file_{idx}',
            'payment_date': datetime.strptime(date.strip(), '%m/%d/%Y'),
            'payment_type': payment_type,
            'notes': description.strip(),
            'amount': amount_float,
            'payment_method': payment_method_to_assign,
            'bank': payment_methods.filter(type="Bank", name="BA").first(),
            'apartment': apartment_to_assign,
        })
    print('retrieved payment_data from csv', payment_data)
    return payment_data


def get_start_end_dates(payment_data):
    start_date = datetime.strptime(payment_data[0]['payment_date'], '%Y-%m-%d').date()
    end_date = datetime.strptime(payment_data[-1]['payment_date'], '%Y-%m-%d').date()
    print('start_date and end_date for the period', start_date, end_date)
    return start_date, end_date

def find_possible_matches(payment_data, start_date, end_date):
    possible_matches = []
    not_matched_file_payments = []
    db_payments = Payment.objects.filter(date__range=(start_date - timedelta(days=10), end_date + timedelta(days=10)))
    matched_payment_ids = set()
    for db_payment in db_payments:
        matches = get_matches(db_payment, payment_data, matched_payment_ids)
        possible_matches.append({'db_payment': db_payment, 'matches': matches})
        

    not_matched_file_payments = [payment for payment in payment_data if payment['id'] not in matched_payment_ids]
    return possible_matches, not_matched_file_payments


def get_matches(payment, payment_data, matched_payment_ids):
    matches = []
   
    for payment_from_file in payment_data:
        if payment_from_file['id'] == payment.id:
            matches.append({
                'file_payment': payment_from_file,
                'field_name': 'id',
                'file_value': payment_from_file['id'],
                'db_value': payment.id,
                'type': 'exact match',
            })
            matched_payment_ids.add(payment_from_file['id'])
            print(f'matches for {payment.id}', matches)
        payment_delta = abs(payment_from_file['amount'] - abs(payment_data['amount']))
        if payment_delta <= 100:
            matches.append({
                'file_payment': payment_from_file,
                'field_name': 'amount',
                'file_value': payment_from_file['amount'],
                'db_value': payment.amount,
                'type': 'exact match' if payment_delta == 0 else 'delta match +-100$',
            })
            matched_payment_ids.add(payment_from_file['id'])
        date_diff = payment_from_file.payment_date - payment.payment_date
        print('date_diff', date_diff)
        print('timedelta(days=4)', timedelta(days=4))
        if date_diff == timedelta(days=4):
            matches.append({
                'file_payment': payment_from_file,
                'field_name': 'payment_date',
                'file_value': payment_from_file['payment_date'],
                'db_value': payment.payment_date,
                'type': 'exact match' if date_diff == timedelta(days=0) else 'delta match +-4 days',
            })
            matched_payment_ids.add(payment_from_file['id'])

    return matches, matched_payment_ids
    

#Example
# possbile_matches:[db_payment, payment_from_csv] : [Payment, Matches[]]
# matches: [{payment:db_payment, matches: [{fieldName: "", fieldValue: "", type: "exact match/close match"}]}]


# Examples of table
# Date,Description,Amount,Running Bal.
# 02/01/2024,Beginning balance as of 02/01/2024,,"47897.23"
# 02/01/2024,"Zelle payment from LLC AG SPORTS for "Felicia Pelligrini Rent"; Conf# 99a8sizgj","4200.00","52097.23"
# 02/01/2024,"Zelle payment from DANIEL GRDADOLNIK for "Rent"; Conf# 99a8scnp8","4000.00","56097.23"
# 02/01/2024,"Zelle payment from ANGELICA Y PIKE for "rent-February"; Conf# 99a8rz6q8","2000.00","58097.23"
# 02/01/2024,"BofA Merchant Services","-993.15","57104.08"
# 02/01/2024,"Zelle payment to OLES SAFONOV for "Moving 720-408"; Conf# uuvoypsbj","-500.00","56604.08"
# 02/01/2024,"Zelle payment to SUPERB MAIDS BROWARD LLC Conf# tcmxzx0zb","-220.00","56384.08"
# 02/01/2024,"Check 607","-3500.00","52884.08"
# 02/01/2024,"Check 618","-2350.00","50534.08"
# 02/01/2024,"Check 807","-3500.00","47034.08"
# 02/01/2024,"AMERICAN EXPRESS DES:ACH PMT ID:M8588 INDN:Farid Gazizov CO ID:1133133497 CCD","-2000.00","45034.08"
# 02/02/2024,"Zelle payment from ANGELICA Y PIKE for ""rent - February""; Conf# 99a8ubbfn","900.00","45934.08"
# 02/02/2024,"Zelle payment from CHARLES CHIN Conf# 01VL34KYJ","83.00","46017.08"
# 02/02/2024,"Check 645","-3500.00","42517.08"