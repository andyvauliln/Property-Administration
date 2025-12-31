from django.shortcuts import render
from ..models import PaymentMethod, Payment, Apartment, PaymenType
from ..forms import PaymentForm
from datetime import datetime
from ..decorators import user_has_role
from django.contrib import messages
import json
import os
from datetime import timedelta
import re
import csv
from .utils import get_model_fields
from django.core import serializers
from django.db.models import Q
from django.http import JsonResponse
from openai import OpenAI
import logging
import uuid
from pathlib import Path
from django.conf import settings

# Separator for grouping multiple payment keys
PAYMENT_KEY_SEPARATOR = "###||###"

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("mysite.payment_sync_v2_trace")


def _log(step, **fields):
    """
    Lightweight request logging (prints + logs).
    Keep payloads small; don't include secrets.
    """
    try:
        payload = json.dumps(fields, default=str, ensure_ascii=False)
    except Exception:
        payload = str(fields)
    msg = f"[payment_sync_v2] {step} {payload}"
    # "print logs" requested by user; also send through Django logger.
    print(msg)
    logger.info(msg)


def _request_id(request):
    rid = getattr(request, "_payment_sync_v2_rid", None)
    if rid:
        return rid
    incoming = request.headers.get("X-Request-Id") if hasattr(request, "headers") else None
    rid = incoming or uuid.uuid4().hex[:10]
    setattr(request, "_payment_sync_v2_rid", rid)
    return rid


def _user_tag(request):
    u = getattr(request, "user", None)
    if not u or not getattr(u, "is_authenticated", False):
        return "anon"
    return f"{getattr(u, 'id', '')}:{getattr(u, 'username', '')}".strip(":")


def _trace_enabled():
    # Default ON so UI requests always create a trace line.
    # Set PAYMENT_SYNC_V2_TRACE=0 to disable.
    v = str(os.getenv("PAYMENT_SYNC_V2_TRACE", "1")).strip().lower()
    return v not in ("0", "false", "no", "off")


def _trace_full():
    return str(os.getenv("PAYMENT_SYNC_V2_TRACE_FULL", "")).strip().lower() in ("1", "true", "yes", "on")


def _trace_path():
    # Write into repo-local logs/ by default (stable regardless of CWD)
    default_path = str(Path(getattr(settings, "BASE_DIR", Path("."))) / "logs" / "payment_sync_v2_trace.jsonl")
    return os.getenv("PAYMENT_SYNC_V2_TRACE_PATH") or default_path


def _trace_max_chars():
    try:
        return int(os.getenv("PAYMENT_SYNC_V2_TRACE_MAX_CHARS") or "20000")
    except Exception:
        return 20000


def _clip(value, max_chars):
    if value is None:
        return None
    s = str(value)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"...<clipped {len(s) - max_chars} chars>"


def _trace_event(request, step, payload):
    """
    Structured JSONL trace:
    - Records real request/response + intermediate transformed data
    - Gated by PAYMENT_SYNC_V2_TRACE=1
    """
    if not _trace_enabled():
        return

    rid = _request_id(request)
    base = {
        "rid": rid,
        "step": step,
        "user": _user_tag(request),
        "path": getattr(request, "path", None),
        "method": getattr(request, "method", None),
    }

    full = _trace_full()
    max_chars = _trace_max_chars()

    def normalize(obj):
        # Ensure JSON-serializable while preserving structure
        try:
            if full:
                return obj
            # non-full mode: avoid huge structures
            if isinstance(obj, (list, tuple)):
                return {"_type": "list", "len": len(obj), "preview": obj[:10]}
            if isinstance(obj, dict):
                # clip long strings
                out = {}
                for k, v in obj.items():
                    if isinstance(v, str):
                        out[k] = _clip(v, max_chars)
                    else:
                        out[k] = v
                return out
            if isinstance(obj, str):
                return _clip(obj, max_chars)
            return obj
        except Exception:
            return {"_type": type(obj).__name__, "repr": _clip(repr(obj), max_chars)}

    event = {**base, "data": normalize(payload)}

    # Primary: Django logger (configured in settings.py to write JSONL file)
    try:
        trace_logger.info(json.dumps(event, default=str, ensure_ascii=False))
        return
    except Exception:
        pass

    # Fallback: direct file write (in case logger isn't configured)
    try:
        trace_file = Path(_trace_path())
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        with trace_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")
    except Exception as e:
        # Never break the request because tracing failed
        _log("trace.write_failed", rid=rid, step=step, error=str(e), trace_path=_trace_path())


@user_has_role('Admin')
def sync_payments_v2(request):
    """Payment sync v2."""
    rid = _request_id(request)
    _log("sync_payments_v2.enter", rid=rid, method=request.method, user=_user_tag(request))
    payment_methods_qs = PaymentMethod.objects.all()
    apartments_qs = Apartment.objects.all()
    payment_types_qs = PaymenType.objects.all()

    context = {
        'title': "Payments Sync V2",
        'data': {
            'amount_delta': 100,
            'date_delta': 4,
            'with_confirmed': False,
            'db_days_before': 30,
            'db_days_after': 30,
            'file_payments_json': '',
            'db_payments_json': json.dumps([]),
            'matched_groups': json.dumps([]),
            'total_file_payments': 0,
            'total_db_payments': 0,
            'payment_methods': get_json(payment_methods_qs),
            'apartments': get_json(apartments_qs),
            'payment_types': get_json(payment_types_qs),
            'model_fields': get_model_fields(PaymentForm(request)),
        }
    }
    
    if request.method == 'POST':
        # Handle saving payments
        if request.POST.get('payments_to_update'):
            payments_to_update = json.loads(request.POST.get('payments_to_update'))
            _log("sync_payments_v2.save_payments", rid=rid, count=len(payments_to_update))
            update_payments(request, payments_to_update)
            messages.success(request, f"Successfully processed {len(payments_to_update)} payments")
            
        # Handle CSV upload and matching
        elif request.FILES.get('csv_file'):
            try:
                _log(
                    "sync_payments_v2.csv_upload",
                    rid=rid,
                    filename=getattr(request.FILES.get('csv_file'), "name", None),
                    size=getattr(request.FILES.get('csv_file'), "size", None),
                )
                process_result = process_csv_upload(request)
                context['data'].update(process_result)
            except Exception as e:
                _log("sync_payments_v2.csv_upload.error", rid=rid, error=str(e))
                messages.error(request, f"Error processing CSV: {str(e)}")
    
    # Allow demo data preview without CSV upload
    if request.method != 'POST' and request.GET.get('demo') == '1':
        _log("sync_payments_v2.demo", rid=rid)
        context['data'].update(build_demo_matching_data())
    
    _log(
        "sync_payments_v2.render",
        rid=rid,
        total_file_payments=context["data"].get("total_file_payments"),
        total_db_payments=context["data"].get("total_db_payments"),
    )
    return render(request, 'payment_sync/sync_v2.html', context)


def build_demo_matching_data():
    payment_methods = [
        {"id": 1, "name": "Wire"},
        {"id": 2, "name": "Zelle"},
        {"id": 3, "name": "Check"},
    ]
    apartments = [
        {"id": 1, "name": "630-205"},
        {"id": 2, "name": "780-306"},
        {"id": 3, "name": "PH-402"},
    ]
    payment_types = [
        {"id": 1, "name": "Rent", "type": "In"},
        {"id": 2, "name": "Other", "type": "Out"},
    ]
    
    file_payments = [
        {"id": "bank-001", "amount": 3300.00, "payment_date": "2025-12-01", "notes": "Wire payment from Michael Steinhardt for apt. 630-205", "apartment_name": "630-205", "payment_method": 1, "payment_method_name": "Wire", "bank": 1, "bank_name": "Bank of America", "payment_type": 1, "payment_type_name": "Rent", "payment_type_type": "In", "is_merged": False},
        {"id": "bank-002", "amount": 2100.00, "payment_date": "2025-11-23", "notes": "Zelle payment from Darren Wiggins for November rent (780-306)", "apartment_name": "780-306", "payment_method": 2, "payment_method_name": "Zelle", "bank": 1, "bank_name": "Bank of America", "payment_type": 1, "payment_type_name": "Rent", "payment_type_type": "In", "is_merged": False},
        {"id": "bank-003", "amount": 178.85, "payment_date": "2025-11-19", "notes": "City of West Palm payment for PH-402 garage", "apartment_name": "PH-402", "payment_method": 3, "payment_method_name": "Check", "bank": 2, "bank_name": "Chase", "payment_type": 2, "payment_type_name": "Other (Out)", "payment_type_type": "Out", "is_merged": False},
        {"id": "bank-004", "amount": 500.00, "payment_date": "2025-11-15", "notes": "Utility payment for 780-306", "apartment_name": "780-306", "payment_method": 1, "payment_method_name": "Wire", "bank": 1, "bank_name": "Bank of America", "payment_type": 2, "payment_type_name": "Other (Out)", "payment_type_type": "Out", "is_merged": False},
        {"id": "bank-005", "amount": 1250.00, "payment_date": "2025-11-28", "notes": "ACH deposit Venmo transfer ref 88X - 450-110", "apartment_name": "450-110", "payment_method": None, "payment_method_name": "ACH", "bank": 2, "bank_name": "Chase", "payment_type": 1, "payment_type_name": "Rent", "payment_type_type": "In", "is_merged": False},
        {"id": "bank-006", "amount": 250.00, "payment_date": "2025-11-27", "notes": "Cash deposit - parking fee 890-215", "apartment_name": "890-215", "payment_method": None, "payment_method_name": "Cash", "bank": 1, "bank_name": "Bank of America", "payment_type": 1, "payment_type_name": "Other (In)", "payment_type_type": "In", "is_merged": False},
        {"id": "bank-007", "amount": 2100.00, "payment_date": "2025-11-25", "notes": "Check deposit - December rent 780-306", "apartment_name": "780-306", "payment_method": 3, "payment_method_name": "Check", "bank": 1, "bank_name": "Bank of America", "payment_type": 1, "payment_type_name": "Rent", "payment_type_type": "In", "is_merged": False},
        {"id": "bank-008", "amount": 990.00, "payment_date": "2025-12-02", "notes": "Wire from vendor - refund", "apartment_name": None, "payment_method": 1, "payment_method_name": "Wire", "bank": 2, "bank_name": "Chase", "payment_type": 2, "payment_type_name": "Other (In)", "payment_type_type": "In", "is_merged": False},
        {"id": "bank-009", "amount": 330.00, "payment_date": "2025-11-30", "notes": "Zelle - partial payment 630-205", "apartment_name": "630-205", "payment_method": 2, "payment_method_name": "Zelle", "bank": 1, "bank_name": "Bank of America", "payment_type": 1, "payment_type_name": "Rent", "payment_type_type": "In", "is_merged": False},
        {"id": "bank-010", "amount": 500.00, "payment_date": "2025-11-17", "notes": "ACH debit - maintenance PH-402", "apartment_name": "PH-402", "payment_method": None, "payment_method_name": "ACH", "bank": 2, "bank_name": "Chase", "payment_type": 2, "payment_type_name": "Other (Out)", "payment_type_type": "Out", "is_merged": False},
        {"id": "bank-011", "amount": 1500.00, "payment_date": "2025-12-03", "notes": "Venmo payout - cleaning services", "apartment_name": None, "payment_method": None, "payment_method_name": "Venmo", "bank": 1, "bank_name": "Bank of America", "payment_type": 2, "payment_type_name": "Other (Out)", "payment_type_type": "Out", "is_merged": False},
    ]
    
    # DB payments will be loaded via "Match Manually" or "Match with AI" buttons
    # Start with empty DB payments - user can click Match buttons to populate
    db_payments = []
    
    return {
        'file_payments_json': json.dumps(file_payments),
        'db_payments_json': json.dumps(db_payments),
        'matched_groups': json.dumps([]),
        'total_file_payments': len(file_payments),
        'total_db_payments': len(db_payments),
        'amount_delta': 100,
        'date_delta': 4,
        'with_confirmed': False,
        'db_days_before': 30,
        'db_days_after': 30,
        'payment_methods': json.dumps(payment_methods),
        'apartments': json.dumps(apartments),
        'payment_types': json.dumps(payment_types),
    }


def process_csv_upload(request):
    """Process uploaded CSV file and return file payments only."""
    csv_file = request.FILES['csv_file']
    rid = _request_id(request)
    
    # Validate file type
    if not csv_file.name.endswith('.csv'):
        messages.error(request, 'File is not CSV type')
        _log("process_csv_upload.invalid_type", rid=rid, filename=csv_file.name)
        return {'error': 'Invalid file type'}
    
    # Get settings from request
    amount_delta = int(request.POST.get('amount_delta', 100))
    date_delta = int(request.POST.get('date_delta', 4))
    db_days_before = int(request.POST.get('db_days_before', 30))
    db_days_after = int(request.POST.get('db_days_after', 30))
    with_confirmed = request.POST.get('with_confirmed') == 'on'
    _log(
        "process_csv_upload.settings",
        rid=rid,
        filename=csv_file.name,
        amount_delta=amount_delta,
        date_delta=date_delta,
        db_days_before=db_days_before,
        db_days_after=db_days_after,
        with_confirmed=with_confirmed,
    )
    
    # Load reference data
    payment_methods = PaymentMethod.objects.all()
    apartments = Apartment.objects.all()
    payment_types = PaymenType.objects.all()
    
    # Parse CSV file
    file_payments = parse_csv_file(request, csv_file, payment_methods, apartments, payment_types)
    _log("process_csv_upload.parsed", rid=rid, file_payments=len(file_payments))
    
    if not file_payments:
        messages.warning(request, "No valid payments found in CSV file")
        _log("process_csv_upload.no_valid_payments", rid=rid)
        return {
            'file_payments_json': '',
            'db_payments_json': json.dumps([]),
            'matched_groups': json.dumps([]),
            'amount_delta': amount_delta,
            'date_delta': date_delta,
            'db_days_before': db_days_before,
            'db_days_after': db_days_after,
            'with_confirmed': with_confirmed,
            'total_file_payments': 0,
            'total_db_payments': 0,
            'model_fields': get_model_fields(PaymentForm(request)),
            'payment_methods': get_json(payment_methods),
            'apartments': get_json(apartments),
            'payment_types': get_json(payment_types),
        }

    return {
        'file_payments_json': json.dumps([serialize_payment(p) for p in file_payments], default=str),
        'db_payments_json': json.dumps([]),
        'matched_groups': json.dumps([]),
        'total_file_payments': len(file_payments),
        'total_db_payments': 0,
        'model_fields': get_model_fields(PaymentForm(request)),
        'amount_delta': amount_delta,
        'date_delta': date_delta,
        'db_days_before': db_days_before,
        'db_days_after': db_days_after,
        'with_confirmed': with_confirmed,
        'payment_methods': get_json(payment_methods),
        'apartments': get_json(apartments),
        'payment_types': get_json(payment_types),
    }


def _parse_file_payment_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        return value
    if isinstance(value, str):
        return parse_payment_date(value)
    return None


def extract_date_range(file_payments):
    if not file_payments:
        return None, None
    dates = []
    for p in file_payments:
        d = _parse_file_payment_date(p.get('payment_date'))
        if d:
            dates.append(d)
    if not dates:
        return None, None
    return min(dates), max(dates)


def query_db_payments_custom(start_date, end_date, db_days_before, db_days_after, with_confirmed):
    if start_date is None or end_date is None:
        return Payment.objects.none()

    date_from = start_date - timedelta(days=int(db_days_before))
    date_to = end_date + timedelta(days=int(db_days_after))

    qs = Payment.objects.filter(payment_date__range=(date_from, date_to)).select_related(
        'payment_type', 'payment_method', 'apartment', 'booking__tenant', 'bank'
    )
    if with_confirmed:
        return qs
    return qs.filter(payment_status='Pending')


def get_json_list(db_model):
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
        elif hasattr(original_obj, 'apartment') and original_obj.apartment:
            item['apartment_name'] = original_obj.apartment.name
        # Add bank_name
        if hasattr(original_obj, 'bank') and original_obj.bank:
            item['bank_name'] = original_obj.bank.name
        else:
            item['bank_name'] = None

    return items_list


def normalize_file_payments_for_matching(file_payments):
    """Ensure file payments have datetime `payment_date`, numeric `amount`, and `payment_type_type`."""
    payment_type_map = {pt.id: pt.type for pt in PaymenType.objects.all()}
    normalized = []
    for p in file_payments or []:
        p2 = dict(p)
        d = _parse_file_payment_date(p2.get('payment_date'))
        if d:
            p2['payment_date'] = datetime.combine(d, datetime.min.time())
        # amount
        try:
            p2['amount'] = float(p2.get('amount', 0) or 0)
        except Exception:
            p2['amount'] = 0
        # payment_type_type
        if not p2.get('payment_type_type'):
            pt_id = p2.get('payment_type')
            if pt_id is not None:
                p2['payment_type_type'] = payment_type_map.get(int(pt_id))
        normalized.append(p2)
    return normalized


def apply_ai_suggestions_to_db_payments(db_payments_list, matched_groups):
    """Adds `is_matched` + `matched_criteria` fields to db payment dicts based on best suggested match."""
    best_by_db_id = {}
    for group in matched_groups or []:
        file_payment = group.get('file_payment') or {}
        file_id = file_payment.get('id')
        for match in group.get('matches') or []:
            db_payment = match.get('db_payment')
            if not db_payment:
                continue
            db_id = getattr(db_payment, 'id', None)
            if db_id is None:
                continue
            score = match.get('score', 0)
            prev = best_by_db_id.get(db_id)
            if prev is None or score > prev['score']:
                best_by_db_id[db_id] = {'score': score, 'file_id': file_id}

    for p in db_payments_list:
        db_id = p.get('id')
        best = best_by_db_id.get(db_id)
        if best and best['score'] >= 40:
            p['is_matched'] = True
            p['matched_criteria'] = f"AI suggestion: file {best['file_id']} (score {best['score']})"
        else:
            p['is_matched'] = False
            p['matched_criteria'] = p.get('matched_criteria') or ''


@user_has_role('Admin')
def fetch_db_payments_for_matching(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    rid = _request_id(request)
    _trace_event(request, "fetch_db_payments_for_matching.request_json", data)
    matching_mode = data.get('matching_mode', 'manual')
    file_payments = data.get('file_payments') or []
    db_days_before = int(data.get('db_days_before', 30))
    db_days_after = int(data.get('db_days_after', 30))
    with_confirmed = bool(data.get('with_confirmed', False))

    _log(
        "fetch_db_payments_for_matching.enter",
        rid=rid,
        matching_mode=matching_mode,
        file_payments=len(file_payments),
        db_days_before=db_days_before,
        db_days_after=db_days_after,
        with_confirmed=with_confirmed,
    )

    start_date, end_date = extract_date_range(file_payments)
    if start_date is None or end_date is None:
        _log("fetch_db_payments_for_matching.no_date_range", rid=rid)
        return JsonResponse({'error': 'Could not determine date range from file payments'}, status=400)

    _trace_event(
        request,
        "fetch_db_payments_for_matching.derived_date_range",
        {"start_date": start_date, "end_date": end_date},
    )

    db_payments_qs = query_db_payments_custom(start_date, end_date, db_days_before, db_days_after, with_confirmed)
    db_payments_list = get_json_list(db_payments_qs)
    _trace_event(
        request,
        "fetch_db_payments_for_matching.db_payments_list",
        db_payments_list,
    )

    _log(
        "fetch_db_payments_for_matching.db_loaded",
        rid=rid,
        start_date=start_date,
        end_date=end_date,
        db_payments=len(db_payments_list),
    )

    if matching_mode == 'manual':
        # No matching, just return DB payments
        for p in db_payments_list:
            p['is_matched'] = False
            p['matched_criteria'] = p.get('matched_criteria') or ''
        _log("fetch_db_payments_for_matching.return_manual", rid=rid, db_payments=len(db_payments_list))
        resp = {'db_payments': db_payments_list, 'matched_groups': []}
        _trace_event(request, "fetch_db_payments_for_matching.response_json", resp)
        return JsonResponse(resp)

    # Auto mode: compute matches and suggestions
    amount_delta = int(data.get('amount_delta', 100))
    date_delta = int(data.get('date_delta', 4))

    _log(
        "fetch_db_payments_for_matching.auto_params",
        rid=rid,
        amount_delta=amount_delta,
        date_delta=date_delta,
    )

    normalized_file_payments = normalize_file_payments_for_matching(file_payments)
    _trace_event(request, "fetch_db_payments_for_matching.normalized_file_payments", normalized_file_payments)
    matched_groups = match_payments(db_payments_qs, normalized_file_payments, amount_delta, date_delta)
    _trace_event(
        request,
        "fetch_db_payments_for_matching.matched_groups_raw",
        serialize_matched_groups(matched_groups),
    )
    apply_ai_suggestions_to_db_payments(db_payments_list, matched_groups)
    _trace_event(request, "fetch_db_payments_for_matching.db_payments_after_suggestions", db_payments_list)

    matched_count = sum(1 for p in db_payments_list if p.get("is_matched"))
    _log(
        "fetch_db_payments_for_matching.return_auto",
        rid=rid,
        matched_groups=len(matched_groups or []),
        db_matched=matched_count,
        db_payments=len(db_payments_list),
    )

    resp = {
        'db_payments': db_payments_list,
        'matched_groups': serialize_matched_groups(matched_groups),
    }
    _trace_event(request, "fetch_db_payments_for_matching.response_json", resp)
    return JsonResponse(resp)


def _safe_float(v, default=0.0):
    try:
        if v is None or v == '':
            return default
        return float(v)
    except Exception:
        return default


def _parse_iso_or_mdy_date(value):
    """
    Accepts:
    - datetime/date objects
    - 'YYYY-MM-DD' or ISO datetimes
    - 'MM/DD/YYYY'
    Returns a date or None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).date()
    except Exception:
        return None


def _unique_non_empty(values):
    out = []
    seen = set()
    for v in values or []:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _build_composite_from_selected_file_payments(selected_file_payments):
    selected = selected_file_payments or []
    amounts = []
    dates = []
    apartments = []
    methods = []
    banks = []
    notes_list = []
    directions = []

    for p in selected:
        amounts.append(_safe_float(p.get('amount'), 0.0))
        d = _parse_iso_or_mdy_date(p.get('payment_date'))
        if d:
            dates.append(d)
        apartments.append(p.get('apartment_name') or '')
        methods.append(p.get('payment_method_name') or '')
        banks.append(p.get('bank_name') or '')
        notes_list.append(p.get('notes') or '')
        if p.get('payment_type_type'):
            directions.append(str(p.get('payment_type_type')))

    amount_total = sum(amounts)
    date_from = min(dates) if dates else None
    date_to = max(dates) if dates else None

    apt_candidates = _unique_non_empty(apartments)
    method_candidates = _unique_non_empty(methods)
    bank_candidates = _unique_non_empty(banks)

    apartment_name = apt_candidates[0] if len(apt_candidates) == 1 else ''
    payment_method_name = method_candidates[0] if len(method_candidates) == 1 else ''
    bank_name = bank_candidates[0] if len(bank_candidates) == 1 else ''

    notes_combined = ' | '.join([n.strip() for n in notes_list if str(n).strip()])

    direction = directions[0] if directions and all(d == directions[0] for d in directions) else None

    return {
        'amount_total': amount_total,
        'date_from': date_from,
        'date_to': date_to,
        'apartment_name': apartment_name,
        'apartment_candidates': apt_candidates if apartment_name == '' else [],
        'payment_method_name': payment_method_name,
        'payment_method_candidates': method_candidates if payment_method_name == '' else [],
        'bank_name': bank_name,
        'bank_candidates': bank_candidates if bank_name == '' else [],
        'notes_list': notes_list,
        'notes_combined': notes_combined,
        'direction': direction,  # 'In' / 'Out' or None
        'selected_count': len(selected),
    }


def _payment_to_rich_dict(p):
    payment_type_info = {
        'id': p.payment_type.id,
        'name': p.payment_type.name,
        'type': p.payment_type.type,
        'keywords': p.payment_type.keywords or '',
    } if p.payment_type else None

    tenant_name = ''
    if getattr(p, 'booking', None) and getattr(p.booking, 'tenant', None):
        tenant_name = p.booking.tenant.full_name

    apartment_name = ''
    if hasattr(p, 'apartmentName'):
        apartment_name = p.apartmentName
    elif getattr(p, 'apartment', None):
        apartment_name = p.apartment.name

    return {
        'id': p.id,
        'amount': str(p.amount),
        'payment_date': p.payment_date.isoformat() if p.payment_date else None,
        'payment_type': payment_type_info['id'] if payment_type_info else None,
        'payment_type_name': f"{payment_type_info['name']} ({payment_type_info['type']})" if payment_type_info else None,
        'payment_type_obj': payment_type_info,
        'payment_method': p.payment_method.id if p.payment_method else None,
        'payment_method_name': p.payment_method.name if p.payment_method else None,
        'bank': p.bank.id if p.bank else None,
        'bank_name': p.bank.name if p.bank else None,
        'apartment': p.apartment.id if p.apartment else None,
        'apartment_name': apartment_name or None,
        'booking': p.booking.id if p.booking else None,
        'tenant_name': tenant_name or '',
        'payment_status': p.payment_status or '',
        'notes': p.notes or '',
        'keywords': p.keywords or '',
    }


def _tokenize(s):
    if not s:
        return set()
    s2 = re.sub(r'[^a-zA-Z0-9]+', ' ', str(s)).strip().lower()
    parts = [p for p in s2.split(' ') if p and len(p) >= 3]
    return set(parts)


def _manual_score_db_payment(db_payment_obj, composite, amount_delta):
    """
    Returns (score, match_type, criteria) or None (filtered out).
    """
    # Type filter (hard) when direction is known
    direction = composite.get('direction')
    if direction and db_payment_obj.payment_type and db_payment_obj.payment_type.type != direction:
        return None

    amount_total = float(composite.get('amount_total') or 0)
    db_amount = float(db_payment_obj.amount or 0)
    selected_count = int(composite.get('selected_count') or 1)
    delta_amount = float(amount_delta) * max(1, selected_count)
    diff_amount = abs(db_amount - amount_total)

    if delta_amount <= 0:
        delta_amount = 1.0

    # Amount score: 0..45
    if diff_amount > delta_amount:
        amount_score = max(0.0, 45.0 - (diff_amount - delta_amount) * 2.0)
    else:
        amount_score = 45.0 * (1.0 - (diff_amount / delta_amount))

    # Date score: 0..30
    d = db_payment_obj.payment_date
    date_from = composite.get('date_from')
    date_to = composite.get('date_to')
    date_score = 0.0
    date_reason = ''
    if d and date_from and date_to:
        if date_from <= d <= date_to:
            date_score = 30.0
            date_reason = f"date inside range {date_from.isoformat()}..{date_to.isoformat()}"
        else:
            # distance to nearest edge
            dist = (date_from - d).days if d < date_from else (d - date_to).days
            date_score = max(0.0, 30.0 - float(abs(dist)) * 2.0)
            date_reason = f"date {d.isoformat()} is {abs(dist)}d outside range"
    else:
        date_reason = "missing date"

    # Apartment / method / bank scoring: 0..15
    meta_score = 0.0
    meta_reasons = []

    apt = (db_payment_obj.apartmentName or '').strip() if hasattr(db_payment_obj, 'apartmentName') else ''
    if not apt and getattr(db_payment_obj, 'apartment', None):
        apt = (db_payment_obj.apartment.name or '').strip()

    comp_apt = (composite.get('apartment_name') or '').strip()
    comp_apt_candidates = composite.get('apartment_candidates') or []
    if comp_apt:
        if apt and apt == comp_apt:
            meta_score += 8.0
            meta_reasons.append(f"apt match {apt}")
    elif comp_apt_candidates:
        if apt and apt in comp_apt_candidates:
            meta_score += 6.0
            meta_reasons.append(f"apt match {apt}")

    comp_method = (composite.get('payment_method_name') or '').strip()
    comp_method_candidates = composite.get('payment_method_candidates') or []
    db_method = (db_payment_obj.payment_method.name if db_payment_obj.payment_method else '') or ''
    if comp_method:
        if db_method and db_method == comp_method:
            meta_score += 4.0
            meta_reasons.append(f"method match {db_method}")
    elif comp_method_candidates:
        if db_method and db_method in comp_method_candidates:
            meta_score += 3.0
            meta_reasons.append(f"method match {db_method}")

    comp_bank = (composite.get('bank_name') or '').strip()
    comp_bank_candidates = composite.get('bank_candidates') or []
    db_bank = (db_payment_obj.bank.name if db_payment_obj.bank else '') or ''
    if comp_bank:
        if db_bank and db_bank == comp_bank:
            meta_score += 3.0
            meta_reasons.append(f"bank match {db_bank}")
    elif comp_bank_candidates:
        if db_bank and db_bank in comp_bank_candidates:
            meta_score += 2.0
            meta_reasons.append(f"bank match {db_bank}")

    # Keyword overlap: 0..10
    composite_tokens = set()
    composite_tokens |= _tokenize(composite.get('notes_combined') or '')
    for s in (composite.get('apartment_candidates') or []):
        composite_tokens |= _tokenize(s)
    for s in (composite.get('payment_method_candidates') or []):
        composite_tokens |= _tokenize(s)
    for s in (composite.get('bank_candidates') or []):
        composite_tokens |= _tokenize(s)
    if composite.get('apartment_name'):
        composite_tokens |= _tokenize(composite.get('apartment_name'))
    if composite.get('payment_method_name'):
        composite_tokens |= _tokenize(composite.get('payment_method_name'))
    if composite.get('bank_name'):
        composite_tokens |= _tokenize(composite.get('bank_name'))

    db_tokens = set()
    db_tokens |= _tokenize(db_payment_obj.notes or '')
    db_tokens |= _tokenize(db_payment_obj.keywords or '')
    if getattr(db_payment_obj, 'booking', None) and getattr(db_payment_obj.booking, 'tenant', None):
        db_tokens |= _tokenize(db_payment_obj.booking.tenant.full_name or '')
        db_tokens |= _tokenize(db_payment_obj.booking.tenant.email or '')
        db_tokens |= _tokenize(db_payment_obj.booking.tenant.phone or '')
    if getattr(db_payment_obj, 'apartment', None):
        db_tokens |= _tokenize(db_payment_obj.apartment.name or '')
        db_tokens |= _tokenize(db_payment_obj.apartment.keywords or '')
    if getattr(db_payment_obj, 'payment_type', None):
        db_tokens |= _tokenize(db_payment_obj.payment_type.name or '')
        db_tokens |= _tokenize(db_payment_obj.payment_type.keywords or '')

    overlap = composite_tokens.intersection(db_tokens)
    overlap_score = min(10.0, float(len(overlap)) * 2.0) if overlap else 0.0

    total = amount_score + date_score + meta_score + overlap_score
    total = max(0.0, min(100.0, total))

    criteria_parts = [
        f"composite amount_total={amount_total:.2f} (Δ={delta_amount:.2f}, diff={diff_amount:.2f})",
    ]
    if date_from and date_to:
        criteria_parts.append(f"composite dates={date_from.isoformat()}..{date_to.isoformat()} ({date_reason})")
    if composite.get('apartment_name'):
        criteria_parts.append(f"apt={composite.get('apartment_name')}")
    elif composite.get('apartment_candidates'):
        criteria_parts.append(f"apts={', '.join(composite.get('apartment_candidates')[:6])}")
    if composite.get('payment_method_name'):
        criteria_parts.append(f"method={composite.get('payment_method_name')}")
    elif composite.get('payment_method_candidates'):
        criteria_parts.append(f"methods={', '.join(composite.get('payment_method_candidates')[:6])}")
    if composite.get('bank_name'):
        criteria_parts.append(f"bank={composite.get('bank_name')}")
    elif composite.get('bank_candidates'):
        criteria_parts.append(f"banks={', '.join(composite.get('bank_candidates')[:6])}")
    if meta_reasons:
        criteria_parts.append("meta: " + "; ".join(meta_reasons))
    if overlap:
        criteria_parts.append("tokens: " + ", ".join(sorted(list(overlap))[:8]))

    return round(total, 2), 'manual_composite', " | ".join(criteria_parts)


def _ai_prefilter_top100(db_payments_qs, composite, amount_delta):
    """
    Returns list of Payment objects (<=100) sorted by heuristic desc.
    """
    selected_count = int(composite.get('selected_count') or 1)
    amount_total = float(composite.get('amount_total') or 0)
    delta_amount = float(amount_delta) * max(1, selected_count)
    if delta_amount <= 0:
        delta_amount = 1.0

    direction = composite.get('direction')
    candidates = list(db_payments_qs)

    # Type filter hard if we can
    if direction:
        candidates = [p for p in candidates if p.payment_type and p.payment_type.type == direction]

    # Amount filter hard, but fallback if it wipes out everything
    amount_filtered = [p for p in candidates if abs(float(p.amount or 0) - amount_total) <= delta_amount]
    if amount_filtered:
        candidates = amount_filtered

    date_from = composite.get('date_from')
    date_to = composite.get('date_to')

    composite_tokens = _tokenize(composite.get('notes_combined') or '')
    for s in (composite.get('apartment_candidates') or []):
        composite_tokens |= _tokenize(s)
    if composite.get('apartment_name'):
        composite_tokens |= _tokenize(composite.get('apartment_name'))
    if composite.get('payment_method_name'):
        composite_tokens |= _tokenize(composite.get('payment_method_name'))
    if composite.get('bank_name'):
        composite_tokens |= _tokenize(composite.get('bank_name'))

    def heuristic(p):
        score = 0.0
        diff = abs(float(p.amount or 0) - amount_total)
        score += max(0.0, 50.0 * (1.0 - (diff / delta_amount)))

        if p.payment_date and date_from and date_to:
            if date_from <= p.payment_date <= date_to:
                score += 25.0
            else:
                dist = (date_from - p.payment_date).days if p.payment_date < date_from else (p.payment_date - date_to).days
                score += max(0.0, 25.0 - float(abs(dist)) * 1.5)

        # Apartment boost
        apt = (p.apartmentName or '').strip() if hasattr(p, 'apartmentName') else ''
        if not apt and getattr(p, 'apartment', None):
            apt = (p.apartment.name or '').strip()
        if composite.get('apartment_name') and apt and apt == composite.get('apartment_name'):
            score += 10.0
        elif composite.get('apartment_candidates') and apt and apt in (composite.get('apartment_candidates') or []):
            score += 7.0

        # Keyword overlap
        db_tokens = _tokenize(p.notes or '') | _tokenize(p.keywords or '')
        if getattr(p, 'booking', None) and getattr(p.booking, 'tenant', None):
            db_tokens |= _tokenize(p.booking.tenant.full_name or '')
        overlap = composite_tokens.intersection(db_tokens)
        if overlap:
            score += min(15.0, float(len(overlap)) * 3.0)
        return score

    candidates.sort(key=heuristic, reverse=True)
    return candidates[:100]


def _openrouter_client():
    api_key = os.getenv('OPENROUTER_API_KEY') or ''
    if not api_key:
        return None, "OPENROUTER_API_KEY is not set"
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
    return client, None


def _extract_json_array(text):
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    # Try to salvage the first JSON array in the response
    start = s.find('[')
    end = s.rfind(']')
    if start >= 0 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            return None
    return None


def _ai_match_with_openrouter(model, base_prompt, custom_prompt, composite, candidate_payments, request=None):
    client, err = _openrouter_client()
    if err:
        if request is not None:
            _trace_event(request, "_ai_match_with_openrouter.no_client", {"error": err, "model": model})
        return None, err

    _log(
        "_ai_match_with_openrouter.start",
        model=model,
        base_prompt_len=len(base_prompt or ""),
        custom_prompt_len=len(custom_prompt or ""),
        candidate_count=len(candidate_payments or []),
        amount_total=composite.get("amount_total"),
        date_from=composite.get("date_from"),
        date_to=composite.get("date_to"),
        selected_count=composite.get("selected_count"),
    )
    if request is not None:
        _trace_event(
            request,
            "_ai_match_with_openrouter.start",
            {
                "model": model,
                "base_prompt_len": len(base_prompt or ""),
                "custom_prompt_len": len(custom_prompt or ""),
                "candidate_count": len(candidate_payments or []),
                "amount_total": composite.get("amount_total"),
                "date_from": composite.get("date_from"),
                "date_to": composite.get("date_to"),
                "selected_count": composite.get("selected_count"),
            },
        )

    # Minify candidate payload
    candidate_payload = []
    for p in candidate_payments or []:
        tenant_name = ''
        if getattr(p, 'booking', None) and getattr(p.booking, 'tenant', None):
            tenant_name = p.booking.tenant.full_name
        apt = (p.apartmentName or '').strip() if hasattr(p, 'apartmentName') else ''
        if not apt and getattr(p, 'apartment', None):
            apt = (p.apartment.name or '').strip()
        candidate_payload.append({
            'db_id': p.id,
            'amount': float(p.amount or 0),
            'payment_date': p.payment_date.isoformat() if p.payment_date else None,
            'type': p.payment_type.type if p.payment_type else None,
            'payment_type': p.payment_type.name if p.payment_type else None,
            'apartment': apt or None,
            'tenant': tenant_name or None,
            'bank': p.bank.name if p.bank else None,
            'payment_method': p.payment_method.name if p.payment_method else None,
            'notes': (p.notes or '')[:180],
        })

    composite_desc = {
        'amount_total': float(composite.get('amount_total') or 0),
        'date_from': composite.get('date_from').isoformat() if composite.get('date_from') else None,
        'date_to': composite.get('date_to').isoformat() if composite.get('date_to') else None,
        'direction': composite.get('direction'),
        'apartment_name': composite.get('apartment_name') or None,
        'apartment_candidates': composite.get('apartment_candidates') or [],
        'payment_method_name': composite.get('payment_method_name') or None,
        'payment_method_candidates': composite.get('payment_method_candidates') or [],
        'bank_name': composite.get('bank_name') or None,
        'bank_candidates': composite.get('bank_candidates') or [],
        'notes_combined': (composite.get('notes_combined') or '')[:500],
        'selected_count': int(composite.get('selected_count') or 0),
    }

    instructions = (base_prompt or '').strip()
    if custom_prompt:
        instructions = (instructions + "\n\nCustom instructions:\n" + str(custom_prompt).strip()).strip()

    system_msg = (
        "You are matching bank file payment selections to DB payments.\n"
        "Return ONLY strict JSON (no markdown, no code fences).\n"
        "Output must be a JSON array of objects: "
        "[{db_id:number, score:number(0-100), match_type:string, criteria:string}].\n"
        "Only use db_id values that appear in the provided candidates list.\n"
        "Prefer fewer, higher-quality matches; score 0-100."
    )

    user_msg = (
        f"{instructions}\n\n"
        f"Composite selection:\n{json.dumps(composite_desc, ensure_ascii=False)}\n\n"
        f"DB candidates (choose best matches):\n{json.dumps(candidate_payload, ensure_ascii=False)}"
    )
    if request is not None:
        # Record the actual prompts/messages we send (clipped by trace settings).
        _trace_event(
            request,
            "_ai_match_with_openrouter.prompts",
            {
                "model": model,
                "base_prompt": (base_prompt or ""),
                "custom_prompt": (custom_prompt or ""),
                "instructions_final": instructions,
                "system_msg": system_msg,
                "user_msg": user_msg,
                "composite_desc": composite_desc,
                "candidate_payload": candidate_payload,
            },
        )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
        )
    except Exception as e:
        _log("_ai_match_with_openrouter.request_failed", model=model, error=str(e))
        if request is not None:
            _trace_event(request, "_ai_match_with_openrouter.request_failed", {"model": model, "error": str(e)})
        return None, f"OpenRouter request failed: {e}"

    content = None
    try:
        content = resp.choices[0].message.content
    except Exception:
        content = None

    parsed = _extract_json_array(content)
    if not isinstance(parsed, list):
        _log(
            "_ai_match_with_openrouter.bad_response",
            model=model,
            content_preview=(str(content)[:220] if content is not None else None),
        )
        if request is not None:
            _trace_event(
                request,
                "_ai_match_with_openrouter.bad_response",
                {"model": model, "content_preview": (str(content)[:220] if content is not None else None)},
            )
        return None, "AI did not return a JSON array"

    _log("_ai_match_with_openrouter.ok", model=model, returned=len(parsed))
    if request is not None:
        _trace_event(request, "_ai_match_with_openrouter.ok", {"model": model, "returned": len(parsed)})
    return parsed, None


@user_has_role('Admin')
def match_selection_v2(request):
    """
    POST selection-based matching endpoint for V2.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    rid = _request_id(request)
    _trace_event(request, "match_selection_v2.request_json", data)
    mode = (data.get('mode') or 'manual').strip().lower()
    if mode not in ('manual', 'ai', 'both'):
        _log("match_selection_v2.invalid_mode", rid=rid, mode=mode)
        return JsonResponse({'error': 'Invalid mode'}, status=400)

    selected_file_ids = data.get('selected_file_ids') or []
    selected_file_ids = [str(x) for x in selected_file_ids if str(x).strip()]
    if not selected_file_ids:
        _log("match_selection_v2.no_selected_file_ids", rid=rid, mode=mode)
        return JsonResponse({'error': 'No selected_file_ids provided'}, status=400)

    selection_key = '+'.join(sorted(selected_file_ids))
    selected_file_payments = data.get('selected_file_payments') or []

    # Settings / knobs
    with_confirmed = bool(data.get('with_confirmed', False))
    amount_delta = int(data.get('amount_delta', 100))
    ai_model = (data.get('ai_model') or 'anthropic/claude-3.5-sonnet').strip()
    ai_base_prompt = (data.get('ai_base_prompt') or data.get('ai_prompt') or '').strip()
    ai_custom_prompt = (data.get('ai_custom_prompt') or '').strip()

    composite = _build_composite_from_selected_file_payments(selected_file_payments)
    _trace_event(request, "match_selection_v2.composite", composite)
    if not composite.get('date_from') or not composite.get('date_to'):
        _log("match_selection_v2.no_date_range", rid=rid, selection_key=selection_key)
        return JsonResponse({'error': 'Could not determine date range from selected_file_payments'}, status=400)

    _log(
        "match_selection_v2.enter",
        rid=rid,
        mode=mode,
        selection_key=selection_key,
        selected_file_ids=len(selected_file_ids),
        selected_file_payments=len(selected_file_payments),
        with_confirmed=with_confirmed,
        amount_delta=amount_delta,
        ai_model=ai_model,
        ai_custom_prompt_len=len(ai_custom_prompt or ""),
        composite_amount_total=composite.get("amount_total"),
        composite_date_from=composite.get("date_from"),
        composite_date_to=composite.get("date_to"),
    )

    # DB window: ±1 month (30 days) around selected range
    db_from = composite['date_from'] - timedelta(days=30)
    db_to = composite['date_to'] + timedelta(days=30)

    db_qs = Payment.objects.filter(payment_date__range=(db_from, db_to)).select_related(
        'payment_type', 'payment_method', 'apartment', 'booking__tenant', 'bank'
    )
    if not with_confirmed:
        db_qs = db_qs.filter(payment_status='Pending')
    _log("match_selection_v2.db_window", rid=rid, db_from=db_from, db_to=db_to, db_qs_count=db_qs.count())
    _trace_event(request, "match_selection_v2.db_window", {"db_from": db_from, "db_to": db_to, "db_qs_count": db_qs.count()})

    # Manual result
    manual_candidates = None
    if mode in ('manual', 'both'):
        candidates = []
        for p in db_qs:
            scored = _manual_score_db_payment(p, composite, amount_delta)
            if not scored:
                continue
            score, match_type, criteria = scored
            if score <= 0:
                continue
            candidates.append({
                'type': 'manual',
                'db_payment': _payment_to_rich_dict(p),
                'score': score,
                'match_type': match_type,
                'criteria': criteria,
            })
        candidates.sort(key=lambda c: c.get('score', 0), reverse=True)
        candidates = candidates[:50]
        manual_candidates = candidates
        _log("match_selection_v2.manual_done", rid=rid, returned=len(manual_candidates))

    # AI result
    ai_candidates = None
    ai_error = None
    if mode in ('ai', 'both'):
        top_candidates = _ai_prefilter_top100(db_qs, composite, amount_delta)
        _log("match_selection_v2.ai_prefilter", rid=rid, candidates=len(top_candidates))
        _trace_event(
            request,
            "match_selection_v2.ai_prefilter_candidates",
            [_payment_to_rich_dict(p) for p in top_candidates],
        )
        ai_json, ai_error = _ai_match_with_openrouter(
            model=ai_model,
            base_prompt=ai_base_prompt,
            custom_prompt=ai_custom_prompt,
            composite=composite,
            candidate_payments=top_candidates,
            request=request,
        )
        _trace_event(request, "match_selection_v2.ai_raw_json", {"ai_json": ai_json, "ai_error": ai_error})
        if ai_json is not None:
            by_id = {p.id: p for p in top_candidates}
            out_candidates = []
            for item in ai_json:
                try:
                    db_id = int(item.get('db_id'))
                except Exception:
                    continue
                p = by_id.get(db_id)
                if not p:
                    continue
                score = _safe_float(item.get('score'), 0.0)
                score = max(0.0, min(100.0, score))
                out_candidates.append({
                    'type': 'ai',
                    'db_payment': _payment_to_rich_dict(p),
                    'score': round(score, 2),
                    'match_type': str(item.get('match_type') or 'ai'),
                    'criteria': str(item.get('criteria') or ''),
                })
            out_candidates.sort(key=lambda c: c.get('score', 0), reverse=True)
            ai_candidates = out_candidates
            _log("match_selection_v2.ai_done", rid=rid, returned=len(ai_candidates))
            _trace_event(request, "match_selection_v2.ai_candidates", ai_candidates)
        else:
            _log("match_selection_v2.ai_failed", rid=rid, error=ai_error)

    matched_payments = []
    if manual_candidates:
        matched_payments.extend(manual_candidates)
    if ai_candidates:
        matched_payments.extend(ai_candidates)

    # EXACT schema requested: ONLY these 2 keys
    payload = {'selected_key': selection_key, 'matched_payments': matched_payments}
    _log(
        "match_selection_v2.return",
        rid=rid,
        total=len(matched_payments),
        manual=len(manual_candidates or []),
        ai=len(ai_candidates or []),
    )
    _trace_event(request, "match_selection_v2.response_json", payload)

    return JsonResponse(payload)


def parse_csv_file(request, csv_file, payment_methods, apartments, payment_types):
    """Parse CSV file and extract payment data"""
    rid = _request_id(request)
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
        _log("parse_csv_file.no_header", rid=rid, filename=getattr(csv_file, "name", None))
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
            _log("parse_csv_file.row_error", rid=rid, row=idx, error=str(e))
            continue
    
    _log("parse_csv_file.done", rid=rid, parsed=len(payment_data))
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
    result = {}
    for k, v in payment_dict.items():
        if isinstance(v, datetime):
            # Convert datetime to ISO format string (YYYY-MM-DD)
            result[k] = v.strftime('%Y-%m-%d')
        else:
            result[k] = v
    return result


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
        # Add bank_name
        if hasattr(original_obj, 'bank') and original_obj.bank:
            item['bank_name'] = original_obj.bank.name
        else:
            item['bank_name'] = None
    
    return json.dumps(items_list)


def update_payments(request, payments_to_update):
    """Update or create payments in database"""
    rid = _request_id(request)
    _log("update_payments.start", rid=rid, count=len(payments_to_update or []), user=_user_tag(request))
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
                _log(
                    "update_payments.updated",
                    rid=rid,
                    payment_id=payment_id,
                    amount=payment.amount,
                    payment_date=payment.payment_date,
                    merged_payment_key_preview=(str(getattr(payment, "merged_payment_key", ""))[:120]),
                )
                messages.success(request, f"Updated Payment: {payment.id}")
            else:
                # Create new payment
                payment = create_new_payment(payment_info)
                payment.save(updated_by=request.user if request.user else None)
                _log(
                    "update_payments.created",
                    rid=rid,
                    payment_id=payment.id,
                    amount=payment.amount,
                    payment_date=payment.payment_date,
                    merged_payment_key_preview=(str(getattr(payment, "merged_payment_key", ""))[:120]),
                )
                messages.success(request, f"Created new Payment: {payment.id}")
        except Exception as e:
            _log(
                "update_payments.error",
                rid=rid,
                payment_id=payment_id or payment_info.get("id"),
                error=str(e),
            )
            messages.error(request, f"Failed to {'update' if payment_id else 'create'} payment: {payment_id or ''} due {str(e)}")
    _log("update_payments.done", rid=rid)


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

