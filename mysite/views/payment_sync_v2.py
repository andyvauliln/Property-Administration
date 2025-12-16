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
from django.db.models import Q

# Separator for grouping multiple payment keys
PAYMENT_KEY_SEPARATOR = "###||###"


@user_has_role('Admin')
def sync_payments_v2(request):
    """
    New version of payment sync with improved matching and UI
    """
    context = {
        'title': "Payments Sync V2",
        'data': {
            'amount_delta': 100,
            'date_delta': 4,
            'with_confirmed': False,
        }
    }
    
    if request.method == 'POST':
        # Handle saving payments
        if request.POST.get('payments_to_update'):
            payments_to_update = json.loads(request.POST.get('payments_to_update'))
            update_payments(request, payments_to_update)
            messages.success(request, f"Successfully processed {len(payments_to_update)} payments")
            
        # Handle CSV upload and matching
        elif request.FILES.get('csv_file'):
            try:
                process_result = process_csv_upload(request)
                context['data'] = process_result
            except Exception as e:
                messages.error(request, f"Error processing CSV: {str(e)}")
    
    return render(request, 'payment_sync/sync_v2.html', context)


def process_csv_upload(request):
    """Process uploaded CSV file and prepare matching data"""
    csv_file = request.FILES['csv_file']
    
    # Validate file type
    if not csv_file.name.endswith('.csv'):
        messages.error(request, 'File is not CSV type')
        return {'error': 'Invalid file type'}
    
    # Get matching criteria from request
    amount_delta = int(request.POST.get('amount_delta', 100))
    date_delta = int(request.POST.get('date_delta', 4))
    with_confirmed = request.POST.get('with_confirmed') == 'on'
    
    # Load reference data
    payment_methods = PaymentMethod.objects.all()
    apartments = Apartment.objects.all()
    payment_types = PaymenType.objects.all()
    
    # Parse CSV file
    file_payments = parse_csv_file(request, csv_file, payment_methods, apartments, payment_types)
    
    if not file_payments:
        messages.warning(request, "No valid payments found in CSV file")
        return {
            'file_payments': [],
            'db_payments': [],
            'matched_groups': [],
            'amount_delta': amount_delta,
            'date_delta': date_delta,
            'with_confirmed': with_confirmed,
        }
    
    # Get date range from file payments
    start_date, end_date = get_date_range(file_payments)
    
    # Query database payments
    db_payments = query_db_payments(start_date, end_date, with_confirmed)
    
    # Remove already handled payments
    db_payments, file_payments = filter_handled_payments(db_payments, file_payments)
    
    # Perform intelligent matching
    matched_groups = match_payments(db_payments, file_payments, amount_delta, date_delta)
    
    # Serialize matched groups for template
    serialized_matched_groups = serialize_matched_groups(matched_groups)
    
    # Prepare data for template
    result = {
        'file_payments_json': json.dumps([serialize_payment(p) for p in file_payments], default=str),
        'db_payments_json': get_json(db_payments),
        'matched_groups': serialized_matched_groups,
        'total_file_payments': len(file_payments),
        'total_db_payments': len(db_payments),
        'model_fields': get_model_fields(PaymentForm(request)),
        'start_date': start_date,
        'end_date': end_date,
        'amount_delta': amount_delta,
        'date_delta': date_delta,
        'with_confirmed': with_confirmed,
        'payment_methods': get_json(payment_methods),
        'apartments': get_json(apartments),
        'payment_types': get_json(payment_types),
    }
    
    return result


def parse_csv_file(request, csv_file, payment_methods, apartments, payment_types):
    """Parse CSV file and extract payment data"""
    file_data = csv_file.read().decode("utf-8")
    lines = file_data.split("\n")
    
    # Preprocess lines
    corrected_lines = [preprocess_csv_line(line) for line in lines if line.strip()]
    reader = csv.reader(corrected_lines)
    lines_list = list(reader)
    
    # Find header row
    try:
        start_index = next(
            i for i, line in enumerate(lines_list) 
            if 'Date' in line and 'Description' in line and 'Amount' in line and 'Running Bal.' in line
        ) + 1
    except StopIteration:
        messages.error(request, "CSV file does not contain the expected header.")
        return []
    
    payment_data = []
    
    # Process each line
    for idx, parts in enumerate(lines_list[start_index:], start=start_index):
        if not parts or all(not part.strip() for part in parts):
            continue
        
        try:
            payment = parse_csv_row(parts, idx, payment_methods, apartments, payment_types)
            if payment:
                payment_data.append(payment)
        except Exception as e:
            messages.warning(request, f"Error parsing row {idx}: {str(e)}")
            continue
    
    return payment_data


def preprocess_csv_line(line):
    """Preprocess CSV line to handle malformed data"""
    splited = line.split(',')
    if len(splited) > 4:
        corrected_line = splited[0] + ',' + ' '.join(splited[1:-2]) + ',' + splited[-2] + ',' + splited[-1]
    else:
        corrected_line = line
    return corrected_line


def parse_csv_row(parts, idx, payment_methods, apartments, payment_types):
    """Parse a single CSV row into payment data"""
    date = parts[0]
    description = parts[1]
    amount = parts[2] if len(parts) > 2 else ''
    running_bal = parts[3] if len(parts) > 3 else ''
    
    # Parse amount
    amount_float = float(amount.strip()) if amount.strip() else 0.0
    if amount_float == 0:
        return None
    
    # Determine payment type based on amount
    if amount_float > 0:
        payment_type = payment_types.filter(name="Other", type="In").first()
    else:
        payment_type = payment_types.filter(name="Other", type="Out").first()
    
    # Match payment method
    payment_method_to_assign = match_payment_method(description, payment_methods)
    
    # Match apartment
    apartment_to_assign = match_apartment(description, apartments)
    
    # Extract ID if present
    extracted_id = extract_id_from_description(description)
    
    # Get bank
    ba_bank = payment_methods.filter(name="BA").first()
    
    return {
        'id': extracted_id or f'id_{idx}',
        'payment_date': datetime.strptime(date.strip(), '%m/%d/%Y'),
        'payment_type': payment_type.id,
        'payment_type_name': f'{payment_type.name} ({payment_type.type})',
        'payment_type_type': payment_type.type,
        'notes': description.strip(),
        'amount': abs(amount_float),
        'payment_method': payment_method_to_assign.id if payment_method_to_assign else None,
        'payment_method_name': payment_method_to_assign.name if payment_method_to_assign else None,
        'merged_payment_key': generate_payment_key(date.strip(), amount_float, description.strip()),
        'bank': ba_bank.id if ba_bank else None,
        'bank_name': ba_bank.name if ba_bank else None,
        'apartment': apartment_to_assign.id if apartment_to_assign else None,
        'apartment_name': apartment_to_assign.name if apartment_to_assign else None,
    }


def match_payment_method(description, payment_methods):
    """Match payment method based on description"""
    description_lower = description.strip().lower()
    
    # Check for direct name match
    for payment_method in payment_methods:
        if payment_method.name.lower() in description_lower:
            return payment_method
    
    # Special case for mobile deposits
    if 'deposit *mobile' in description_lower:
        return payment_methods.filter(name="Check").first()
    
    # Check for keyword match
    for payment_method in payment_methods:
        if payment_method.keywords:
            keywords_array = [keyword.strip() for keyword in payment_method.keywords.split(",")]
            if any(keyword.strip().lower() in description_lower for keyword in keywords_array):
                return payment_method
    
    return None


def match_apartment(description, apartments):
    """Match apartment based on description"""
    for apartment in apartments:
        if apartment.name in description:
            return apartment
    return None


def extract_id_from_description(description):
    """Extract payment ID from description if present"""
    if "@S3" in description:
        match = re.search(r"@S3(\d+)", description)
        if match:
            return int(match.group(1))
    return None


def generate_payment_key(date_str, amount_float, description):
    """Generate unique payment key"""
    date_formatted = datetime.strptime(date_str, '%m/%d/%Y').strftime('%m/%d/%Y').zfill(10)
    amount_formatted = remove_trailing_zeros_from_str(str(amount_float))
    return date_formatted + amount_formatted + description


def remove_trailing_zeros_from_str(amount_str):
    """Remove trailing zeros from amount string"""
    amount_float = float(amount_str)
    return ('%f' % abs(amount_float)).rstrip('0').rstrip('.')


def get_date_range(payment_data):
    """Get start and end dates from payment data"""
    if not payment_data or len(payment_data) < 1:
        return None, None
    
    dates = [p['payment_date'] for p in payment_data]
    return min(dates), max(dates)


def query_db_payments(start_date, end_date, with_confirmed):
    """Query database payments within date range"""
    if start_date is None or end_date is None:
        return Payment.objects.none()
    
    # Add buffer to date range
    date_from = start_date - timedelta(days=45)
    date_to = end_date + timedelta(days=45)
    
    if with_confirmed:
        return Payment.objects.filter(
            payment_date__range=(date_from, date_to)
        ).select_related('payment_type', 'payment_method', 'apartment', 'booking__tenant')
    else:
        return Payment.objects.filter(
            payment_date__range=(date_from, date_to),
            payment_status='Pending'
        ).select_related('payment_type', 'payment_method', 'apartment', 'booking__tenant')


def filter_handled_payments(db_payments, file_payments):
    """Remove already handled/merged payments"""
    # Collect merged payment keys from database
    db_payment_keys = set()
    for payment in db_payments:
        if payment.payment_status == "Merged" and payment.merged_payment_key:
            if PAYMENT_KEY_SEPARATOR in payment.merged_payment_key:
                # Split grouped keys
                individual_keys = payment.merged_payment_key.split(PAYMENT_KEY_SEPARATOR)
                db_payment_keys.update(individual_keys)
            else:
                db_payment_keys.add(payment.merged_payment_key)
    
    # Filter out merged payments from database
    db_payments_cleaned = [p for p in db_payments if p.payment_status != "Merged"]
    
    # Filter out file payments that were already merged
    file_payments_cleaned = [
        p for p in file_payments 
        if not any(db_key in p['merged_payment_key'] for db_key in db_payment_keys)
    ]
    
    return db_payments_cleaned, file_payments_cleaned


def match_payments(db_payments, file_payments, amount_delta, date_delta):
    """
    Intelligent payment matching algorithm
    Returns grouped matches organized by file payment
    """
    matched_groups = []
    
    for file_payment in file_payments:
        matches = find_matches_for_file_payment(
            file_payment, 
            db_payments, 
            amount_delta, 
            date_delta
        )
        
        # Calculate match quality
        match_quality = calculate_match_quality(matches)
        
        matched_groups.append({
            'file_payment': file_payment,
            'matches': matches,
            'match_quality': match_quality,
            'has_high_confidence': match_quality == 'high',
            'has_medium_confidence': match_quality == 'medium',
            'has_no_match': match_quality == 'none',
        })
    
    # Sort by match quality (high confidence first)
    quality_order = {'high': 0, 'medium': 1, 'low': 2, 'none': 3}
    matched_groups.sort(key=lambda x: quality_order.get(x['match_quality'], 4))
    
    return matched_groups


def find_matches_for_file_payment(file_payment, db_payments, amount_delta, date_delta):
    """Find all potential matches for a file payment"""
    matches = []
    
    for db_payment in db_payments:
        # Check payment type compatibility
        if not is_payment_type_compatible(db_payment, file_payment):
            continue
        
        # Check if ID matches
        if db_payment.id == file_payment['id']:
            matches.append({
                'db_payment': db_payment,
                'score': 100,
                'match_type': 'exact_id',
                'details': {
                    'id': 'Exact Match',
                    'amount': 'N/A',
                    'date': 'N/A',
                    'keywords': 'N/A',
                }
            })
            continue
        
        # Calculate match score
        match_result = calculate_match_score(
            db_payment, 
            file_payment, 
            amount_delta, 
            date_delta
        )
        
        if match_result and match_result['score'] > 0:
            matches.append(match_result)
    
    # Sort by score (highest first)
    matches.sort(key=lambda x: x['score'], reverse=True)
    
    return matches


def is_payment_type_compatible(db_payment, file_payment):
    """Check if payment types are compatible"""
    file_type = file_payment['payment_type_type']
    db_type = db_payment.payment_type.type
    return file_type == db_type


def calculate_match_score(db_payment, file_payment, amount_delta, date_delta):
    """Calculate comprehensive match score between payments"""
    score = 0
    details = {}
    
    # Amount matching (weight: high)
    amount_diff = abs(float(db_payment.amount) - abs(float(file_payment['amount'])))
    if amount_diff <= amount_delta:
        if amount_diff < 1:
            score += 30
            details['amount'] = 'Exact Match'
        else:
            score += 20
            details['amount'] = f'Match ±{int(amount_diff)}'
    else:
        return None  # Amount difference too large
    
    # Date matching (weight: high)
    payment_date_datetime = datetime.combine(db_payment.payment_date, datetime.min.time())
    date_diff = file_payment['payment_date'] - payment_date_datetime
    if abs(date_diff.days) <= date_delta:
        if date_diff.days == 0:
            score += 25
            details['date'] = 'Exact Match'
        else:
            score += 15
            details['date'] = f'Match ±{abs(date_diff.days)}d'
    else:
        return None  # Date difference too large
    
    # Keyword matching (weight: medium)
    keywords_matched = []
    keywords_array = collect_all_keywords(db_payment)
    
    for keyword in keywords_array:
        if keyword and file_payment['notes'].lower().find(keyword.lower()) != -1:
            keywords_matched.append(keyword)
            score += 5  # 5 points per keyword
    
    if keywords_matched:
        details['keywords'] = ', '.join(keywords_matched[:5])  # Show first 5
    else:
        details['keywords'] = 'No Match'
    
    # Apartment matching (weight: low-medium)
    if db_payment.apartmentName and file_payment.get('apartment_name'):
        if db_payment.apartmentName == file_payment['apartment_name']:
            score += 10
            details['apartment'] = 'Exact Match'
        else:
            details['apartment'] = 'Different'
    else:
        details['apartment'] = 'N/A'
    
    # Notes similarity (weight: low)
    if db_payment.notes and file_payment['notes']:
        if file_payment['notes'].strip() in db_payment.notes.strip():
            score += 8
            details['notes'] = 'Contains Match'
        elif db_payment.notes.strip() in file_payment['notes'].strip():
            score += 8
            details['notes'] = 'Contained In'
        else:
            details['notes'] = 'Different'
    else:
        details['notes'] = 'N/A'
    
    return {
        'db_payment': db_payment,
        'score': score,
        'match_type': 'calculated',
        'details': details,
    }


def collect_all_keywords(db_payment):
    """Collect all relevant keywords from payment and related objects"""
    keywords = []
    
    # Payment keywords
    if db_payment.keywords:
        keywords.extend([k.strip() for k in db_payment.keywords.split(",") if k.strip()])
    
    # Apartment keywords
    if db_payment.apartment and db_payment.apartment.keywords:
        keywords.extend([k.strip() for k in db_payment.apartment.keywords.split(",") if k.strip()])
    
    # Booking keywords
    if db_payment.booking:
        if db_payment.booking.keywords:
            keywords.extend([k.strip() for k in db_payment.booking.keywords.split(",") if k.strip()])
        
        # Booking apartment keywords
        if db_payment.booking.apartment and db_payment.booking.apartment.keywords:
            keywords.extend([k.strip() for k in db_payment.booking.apartment.keywords.split(",") if k.strip()])
        
        # Tenant information
        if db_payment.booking.tenant:
            keywords.extend([name.strip() for name in db_payment.booking.tenant.full_name.split(" ")])
            if db_payment.booking.tenant.email:
                keywords.append(db_payment.booking.tenant.email.strip())
            if db_payment.booking.tenant.phone:
                keywords.append(db_payment.booking.tenant.phone.strip())
    
    # Payment type keywords
    if db_payment.payment_type and db_payment.payment_type.keywords:
        keywords.extend([k.strip() for k in db_payment.payment_type.keywords.split(",") if k.strip()])
    
    return list(set(keywords))  # Remove duplicates


def calculate_match_quality(matches):
    """Calculate overall match quality"""
    if not matches:
        return 'none'
    
    top_match = matches[0]
    score = top_match['score']
    
    if score >= 70:
        return 'high'
    elif score >= 40:
        return 'medium'
    elif score >= 20:
        return 'low'
    else:
        return 'none'


def serialize_payment(payment_dict):
    """Serialize payment dictionary for JSON"""
    return {
        k: v.isoformat() if isinstance(v, datetime) else v 
        for k, v in payment_dict.items()
    }


def serialize_matched_groups(matched_groups):
    """Serialize matched groups with Django model instances to JSON-serializable format"""
    serialized = []
    
    for group in matched_groups:
        serialized_matches = []
        for match in group['matches']:
            db_payment = match['db_payment']
            
            # Get payment type info
            payment_type_info = {
                'id': db_payment.payment_type.id,
                'name': db_payment.payment_type.name,
                'type': db_payment.payment_type.type,
            } if db_payment.payment_type else None
            
            serialized_match = {
                'db_payment': {
                    'id': db_payment.id,
                    'amount': str(db_payment.amount),
                    'payment_date': db_payment.payment_date.isoformat() if db_payment.payment_date else None,
                    'payment_type': payment_type_info['id'] if payment_type_info else None,
                    'payment_type_name': f"{payment_type_info['name']} ({payment_type_info['type']})" if payment_type_info else None,
                    'payment_type_obj': payment_type_info,
                    'payment_method': db_payment.payment_method.id if db_payment.payment_method else None,
                    'payment_method_name': db_payment.payment_method.name if db_payment.payment_method else None,
                    'notes': db_payment.notes or '',
                    'bank': db_payment.bank.id if db_payment.bank else None,
                    'bank_name': db_payment.bank.name if db_payment.bank else None,
                    'apartment': db_payment.apartment.id if db_payment.apartment else None,
                    'apartment_name': db_payment.apartmentName if hasattr(db_payment, 'apartmentName') else (db_payment.apartment.name if db_payment.apartment else None),
                    'booking': db_payment.booking.id if db_payment.booking else None,
                    'tenant_name': db_payment.booking.tenant.full_name if (db_payment.booking and db_payment.booking.tenant) else '',
                    'payment_status': db_payment.payment_status or '',
                },
                'score': match['score'],
                'match_type': match['match_type'],
                'details': match['details'],
            }
            serialized_matches.append(serialized_match)
        
        serialized_group = {
            'file_payment': group['file_payment'],
            'matches': serialized_matches,
            'match_quality': group['match_quality'],
            'has_high_confidence': group['has_high_confidence'],
            'has_medium_confidence': group['has_medium_confidence'],
            'has_no_match': group['has_no_match'],
        }
        serialized.append(serialized_group)
    
    return serialized


def get_json(db_model):
    """Convert Django queryset to JSON"""
    items_json_data = serializers.serialize('json', db_model)
    data_list = json.loads(items_json_data)
    items_list = [{'id': item['pk'], **item['fields']} for item in data_list]
    
    for item, original_obj in zip(items_list, db_model):
        if hasattr(original_obj, 'booking') and original_obj.booking:
            item["tenant_name"] = original_obj.booking.tenant.full_name
        else:
            item["tenant_name"] = ""
        if hasattr(original_obj, 'apartmentName'):
            item['apartment_name'] = original_obj.apartmentName
    
    return json.dumps(items_list)


def update_payments(request, payments_to_update):
    """Update or create payments in database"""
    for payment_info in payments_to_update:
        payment_id = None
        try:
            if payment_info['id'] and (isinstance(payment_info['id'], int) or 
                                     (isinstance(payment_info['id'], str) and 'id_' not in payment_info['id'])):
                # Update existing payment
                payment = Payment.objects.get(id=payment_info['id'])
                payment_id = payment.id
                update_payment_fields(payment, payment_info)
                payment.save(updated_by=request.user if request.user else None)
                messages.success(request, f"Updated Payment: {payment.id}")
            else:
                # Create new payment
                payment = create_new_payment(payment_info)
                payment.save(updated_by=request.user if request.user else None)
                messages.success(request, f"Created new Payment: {payment.id}")
        except Exception as e:
            messages.error(request, f"Failed to {'update' if payment_id else 'create'} payment: {payment_id or ''} due {str(e)}")


def update_payment_fields(payment, payment_info):
    """Update payment object fields"""
    payment.amount = float(payment_info['amount'])
    
    # Determine payment_date
    date_str = payment_info.get('payment_date') or payment_info.get('file_date', '')
    if not date_str:
        raise ValueError("Payment date is required")
    payment.payment_date = parse_payment_date(date_str)
    
    payment.payment_type_id = payment_info['payment_type']
    payment.notes = payment_info['notes']
    payment.payment_method_id = payment_info['payment_method'] or None
    payment.bank_id = payment_info['bank'] or None
    payment.apartment_id = payment_info['apartment'] or None
    payment.payment_status = "Merged"
    
    # Set merged payment key
    merged_payment_key = payment_info.get('merged_payment_key') or (
        parse_date(payment_info.get('file_date', '')) + 
        remove_trailing_zeros_from_str(payment_info.get('file_amount', '0')) + 
        payment_info.get('file_notes', '')
    )
    payment.merged_payment_key = merged_payment_key


def create_new_payment(payment_info):
    """Create new payment object"""
    date_str = payment_info.get('payment_date') or payment_info.get('file_date', '')
    if not date_str:
        raise ValueError("Payment date is required")
    
    # Validate amount is not zero
    amount = float(payment_info['amount'])
    if amount == 0:
        raise ValueError("Payment amount cannot be 0")
    
    merged_payment_key = payment_info.get('merged_payment_key') or (
        parse_date(payment_info.get('file_date', '')) + 
        remove_trailing_zeros_from_str(payment_info.get('file_amount', '0')) + 
        payment_info.get('file_notes', '')
    )
    
    return Payment(
        amount=amount,
        payment_date=parse_payment_date(date_str),
        payment_type_id=payment_info['payment_type'],
        notes=payment_info['notes'],
        payment_method_id=payment_info['payment_method'] or None,
        bank_id=payment_info['bank'] or None,
        apartment_id=payment_info['apartment'] or None,
        merged_payment_key=merged_payment_key,
        payment_status="Merged",
    )


def parse_payment_date(date_str):
    """Parse date string into a date object"""
    if not date_str:
        return None
    
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%B %d %Y',
        '%m/%d/%Y',
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    
    raise ValueError(f"Date format for '{date_str}' is not supported")


def parse_date(date_str):
    """Parse and format date string"""
    if not date_str:
        return ""
    
    formats = ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%B %d %Y']
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime('%m/%d/%Y')
        except ValueError:
            continue
    
    raise ValueError(f"Date format for '{date_str}' is not supported")

