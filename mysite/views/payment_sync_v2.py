from django.shortcuts import render
from ..models import PaymentMethod, Payment, Apartment, PaymenType, Booking
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


def _format_context_length(n):
    """Format context length for display, e.g. 128000 -> '128k', 1000000 -> '1M'."""
    if n is None:
        return None
    try:
        n = int(n)
        if n >= 1_000_000:
            return f"{n // 1_000_000}M"
        if n >= 1000:
            return f"{n // 1000}k"
        return str(n)
    except (ValueError, TypeError):
        return None


def _get_openrouter_ai_models():
    """Fetch AI models from OpenRouter API. Returns list of {value, label, prompt_price?, completion_price?, context_length_display}."""
    try:
        import requests
        api_key = os.environ.get('OPENROUTER_API_KEY')
        if api_key:
            response = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            if response.status_code == 200:
                data = response.json()
                models = []
                for m in data.get('data', []):
                    pricing = m.get('pricing', {})
                    prompt_price = float(pricing.get('prompt', 0)) * 1000000
                    completion_price = float(pricing.get('completion', 0)) * 1000000
                    ctx = m.get('context_length')
                    models.append({
                        'value': m.get('id'),
                        'label': m.get('name'),
                        'prompt_price': f"{prompt_price:.2f}",
                        'completion_price': f"{completion_price:.2f}",
                        'context_length': ctx,
                        'context_length_display': _format_context_length(ctx),
                    })
                models.sort(key=lambda x: x['label'])
                return models
    except Exception as e:
        _log("_get_openrouter_ai_models.error", error=str(e))
    return [
        {'value': 'google/gemini-3-flash-preview', 'label': 'Gemini 3 Flash Preview', 'context_length_display': '1M'},
        {'value': 'openai/gpt-4o', 'label': 'GPT-4o', 'context_length_display': '128k'},
        {'value': 'openai/gpt-4o-mini', 'label': 'GPT-4o Mini', 'context_length_display': '128k'},
        {'value': 'anthropic/claude-3.5-sonnet', 'label': 'Claude 3.5 Sonnet', 'context_length_display': '200k'},
    ]


@user_has_role('Admin')
def sync_payments_v2(request):
    """Payment sync v2."""
    rid = _request_id(request)
    _log("sync_payments_v2.enter", rid=rid, method=request.method, user=_user_tag(request))
    payment_methods_qs = PaymentMethod.objects.all()
    apartments_qs = Apartment.objects.all()
    payment_types_qs = PaymenType.objects.all()

    ai_models = _get_openrouter_ai_models()

    context = {
        'title': "Payments Sync V2",
        'data': {
            'amount_delta': 100,
            'date_delta': 4,
            'db_days_before': 30,
            'db_days_after': 30,
            'ai_model': 'google/gemini-3-flash-preview',
            'ai_models': ai_models,
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
        # Handle deleting payments
        if request.POST.get('payment_ids_to_delete'):
            try:
                ids = json.loads(request.POST.get('payment_ids_to_delete'))
                ids = [int(x) for x in ids if x is not None and str(x).isdigit()]
                deleted = 0
                for pid in ids:
                    try:
                        Payment.objects.filter(id=pid).delete()
                        deleted += 1
                    except Exception as e:
                        _log("sync_payments_v2.delete_error", rid=rid, payment_id=pid, error=str(e))
                is_ajax = (
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or 'application/json' in (request.headers.get('Accept') or '')
                )
                if is_ajax:
                    return JsonResponse({
                        'success': True,
                        'message': f"Deleted {deleted} payment(s)",
                    })
                messages.success(request, f"Deleted {deleted} payment(s)")
            except Exception as e:
                _log("sync_payments_v2.delete_payments.error", rid=rid, error=str(e))
                is_ajax = (
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or 'application/json' in (request.headers.get('Accept') or '')
                )
                if is_ajax:
                    return JsonResponse(
                        {'success': False, 'message': str(e)},
                        status=500,
                    )
                messages.error(request, str(e))
        # Handle saving payments
        elif request.POST.get('payments_to_update'):
            payments_to_update = json.loads(request.POST.get('payments_to_update'))
            _log("sync_payments_v2.save_payments", rid=rid, count=len(payments_to_update))
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or 'application/json' in (request.headers.get('Accept') or '')
            )
            if is_ajax:
                try:
                    update_payments(request, payments_to_update, add_per_payment_messages=False)
                    return JsonResponse({
                        'success': True,
                        'message': f"Successfully processed {len(payments_to_update)} payments",
                    })
                except Exception as e:
                    _log("sync_payments_v2.save_payments.error", rid=rid, error=str(e))
                    return JsonResponse(
                        {'success': False, 'message': str(e)},
                        status=500,
                    )
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
                context['data']['strip_demo_from_url'] = True
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
    """
    Return demo data for ?demo=1: 20 hardcoded file payments + DB payments from setup_demo.
    Run `python manage.py setup_demo_payment_sync_v2` first to create matching DB payments.
    """
    import json
    from datetime import date, timedelta

    from mysite.demo_file_payments import get_demo_file_payments_raw

    raw = get_demo_file_payments_raw()
    today = date.today()

    def _resolve_bank(name):
        if not name:
            return None
        return PaymentMethod.objects.filter(name__iexact=name, type="Bank").first()

    def _resolve_pm(name):
        if not name:
            return None
        return PaymentMethod.objects.filter(name__iexact=name, type="Payment Method").first()

    def _resolve_pt(name, ptype):
        if not name or not ptype:
            return None
        return PaymenType.objects.filter(name__iexact=name, type=ptype).first()

    def _resolve_apt(name):
        if not name:
            return None
        return Apartment.objects.filter(name=name).first()

    file_payments = []
    for r in raw:
        offset = r.get("date_offset_days", 0)
        pay_date = (today - timedelta(days=offset)).isoformat()
        bank = _resolve_bank(r.get("bank_name"))
        pm = _resolve_pm(r.get("payment_method_name"))
        pt = _resolve_pt(r.get("payment_type_name"), r.get("payment_type_type"))
        apt_candidates = r.get("apartment_candidates") or []
        apt = _resolve_apt(apt_candidates[0]) if apt_candidates else None

        fp = {
            "id": r["id"],
            "amount": float(r["amount"]),
            "payment_date": pay_date,
            "notes": r.get("notes", ""),
            "apartment": apt.id if apt else None,
            "apartment_candidates": apt_candidates,
            "tenant_candidates": r.get("tenant_candidates") or [],
            "bank": bank.id if bank else None,
            "bank_candidates": [r.get("bank_name")] if r.get("bank_name") else [],
            "payment_method": pm.id if pm else None,
            "payment_method_candidates": [r.get("payment_method_name")] if r.get("payment_method_name") else [],
            "payment_type": pt.id if pt else None,
            "payment_type_candidates": [f"{r.get('payment_type_name', '')} ({r.get('payment_type_type', '')})"] if r.get("payment_type_name") else [],
            "payment_type_type": r.get("payment_type_type", ""),
            "merged_payment_key": None,
            "is_merged": False,
        }
        fp["merged_payment_key"] = _generate_merged_payment_key_from_payment_info(
            {"payment_date": pay_date, "amount": fp["amount"], "notes": fp["notes"]}
        )
        file_payments.append(fp)

    DEMO_KEYWORD_TAG = "__demo_v2__"
    try:
        demo_db = list(
            Payment.objects.filter(keywords__contains=DEMO_KEYWORD_TAG)
            .select_related("payment_type", "payment_method", "apartment", "booking__tenant", "bank")
            .order_by("id")
        )
    except Exception as e:
        _log("build_demo_matching_data.db_error", error=str(e))
        demo_db = []

    db_payments = [_payment_to_rich_dict(p) for p in demo_db]
    db_days_before = 60
    db_days_after = 60
    start_date, end_date = extract_date_range(file_payments)
    if start_date and end_date:
        try:
            all_db_qs = query_db_payments_custom(start_date, end_date, db_days_before, db_days_after)
            all_db_payments_list = get_json_list(all_db_qs)
            existing_ids = {p.get("id") for p in all_db_payments_list}
            for p in db_payments:
                if p.get("id") not in existing_ids:
                    all_db_payments_list.append(p)
            db_payments = all_db_payments_list
        except Exception as e:
            _log("build_demo_matching_data.query_error", error=str(e))

    return {
        "file_payments_json": json.dumps(file_payments),
        "db_payments_json": json.dumps(db_payments),
        "matched_groups": json.dumps([]),
        "total_file_payments": len(file_payments),
        "total_db_payments": len(db_payments),
        "amount_delta": 100,
        "date_delta": 4,
        "db_days_before": db_days_before,
        "db_days_after": db_days_after,
        "payment_methods": get_json(PaymentMethod.objects.all()),
        "apartments": get_json(Apartment.objects.all()),
        "payment_types": get_json(PaymenType.objects.all()),
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
    _log(
        "process_csv_upload.settings",
        rid=rid,
        filename=csv_file.name,
        amount_delta=amount_delta,
        date_delta=date_delta,
        db_days_before=db_days_before,
        db_days_after=db_days_after,
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
            'total_file_payments': 0,
            'total_db_payments': 0,
            'model_fields': get_model_fields(PaymentForm(request)),
            'payment_methods': get_json(payment_methods),
            'apartments': get_json(apartments),
            'payment_types': get_json(payment_types),
        }

    # Enrich file payments with booking-derived apartment candidates (same window as matching)
    start_date_raw, end_date_raw = extract_date_range(file_payments)
    if start_date_raw and end_date_raw:
        date_from = start_date_raw - timedelta(days=int(db_days_before))
        date_to = end_date_raw + timedelta(days=int(db_days_after))
        bookings_qs = Booking.objects.filter(
            start_date__lte=date_to,
            end_date__gte=date_from,
        ).select_related("tenant", "apartment")
        file_payments = enrich_file_payments_with_booking_apartments(file_payments, list(bookings_qs))

    # Query all DB payments for period based on file dates + config days
    serialized_file_payments = [serialize_payment(p) for p in file_payments]
    start_date, end_date = extract_date_range(serialized_file_payments)
    db_payments_list = []
    if start_date and end_date:
        all_db_qs = query_db_payments_custom(start_date, end_date, db_days_before, db_days_after)
        db_payments_list = get_json_list(all_db_qs)
    _log("process_csv_upload.db_payments", rid=rid, count=len(db_payments_list))

    return {
        'file_payments_json': json.dumps(serialized_file_payments, default=str),
        'db_payments_json': json.dumps(db_payments_list),
        'matched_groups': json.dumps([]),
        'total_file_payments': len(file_payments),
        'total_db_payments': len(db_payments_list),
        'model_fields': get_model_fields(PaymentForm(request)),
        'amount_delta': amount_delta,
        'date_delta': date_delta,
        'db_days_before': db_days_before,
        'db_days_after': db_days_after,
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


def query_db_payments_custom(start_date, end_date, db_days_before, db_days_after):
    """Query all DB payments in date range (all statuses)."""
    if start_date is None or end_date is None:
        return Payment.objects.none()

    date_from = start_date - timedelta(days=int(db_days_before))
    date_to = end_date + timedelta(days=int(db_days_after))

    return Payment.objects.filter(payment_date__range=(date_from, date_to)).select_related(
        'payment_type', 'payment_method', 'apartment', 'booking__tenant', 'bank'
    )


def get_json_list(db_model):
    items_json_data = serializers.serialize('json', db_model)
    data_list = json.loads(items_json_data)
    items_list = [{'id': item['pk'], **item['fields']} for item in data_list]

    for item, original_obj in zip(items_list, db_model):
        if hasattr(original_obj, 'booking') and original_obj.booking:
            tenant = getattr(original_obj.booking, 'tenant', None)
            item["tenant_name"] = (getattr(tenant, 'full_name', '') or '') if tenant else ''
            item['booking_display'] = _format_booking_display(original_obj.booking)
        else:
            item["tenant_name"] = ""
            item['booking_display'] = None
        if hasattr(original_obj, 'apartmentName'):
            item['apartment_name'] = original_obj.apartmentName
        elif hasattr(original_obj, 'apartment') and original_obj.apartment:
            item['apartment_name'] = original_obj.apartment.name
        elif hasattr(original_obj, 'booking') and original_obj.booking and original_obj.booking.apartment:
            item['apartment_name'] = original_obj.booking.apartment.name
        # Add bank_name
        if hasattr(original_obj, 'bank') and original_obj.bank:
            item['bank_name'] = original_obj.bank.name
        else:
            item['bank_name'] = None
        # Add payment_type_name and payment_type_obj
        if hasattr(original_obj, 'payment_type') and original_obj.payment_type:
            item['payment_type_name'] = f"{original_obj.payment_type.name} ({original_obj.payment_type.type})"
            item['payment_type_obj'] = {
                'id': original_obj.payment_type.id,
                'name': original_obj.payment_type.name,
                'type': original_obj.payment_type.type,
            }
        else:
            item['payment_type_name'] = None
            item['payment_type_obj'] = None
        # Add payment_method_name
        if hasattr(original_obj, 'payment_method') and original_obj.payment_method:
            item['payment_method_name'] = original_obj.payment_method.name
        else:
            item['payment_method_name'] = None

    return items_list


def normalize_file_payments_for_matching(file_payments):
    """Ensure file payments have datetime `payment_date`, numeric `amount`, `payment_type_type`, and list fields."""
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
        # Ensure list fields (migrate legacy singular to list for backward compat)
        for list_key, legacy_key in [
            ('apartment_candidates', 'apartment_name'),
            ('tenant_candidates', 'tenant_name'),
            ('payment_method_candidates', 'payment_method_name'),
            ('bank_candidates', 'bank_name'),
            ('payment_type_candidates', 'payment_type_name'),
        ]:
            existing = p2.get(list_key)
            combined = list(existing) if isinstance(existing, (list, tuple)) else []
            legacy = p2.get(legacy_key)
            if legacy and str(legacy).strip():
                leg = str(legacy).strip()
                if leg not in combined:
                    combined.append(leg)
            p2[list_key] = _unique_non_empty(combined)
        normalized.append(p2)
    return normalized


def _extract_merged_keys_from_file_payments(file_payments):
    keys = []
    for p in file_payments or []:
        key = p.get('merged_payment_key')
        if not key:
            continue
        keys.extend(_split_merged_payment_key(key))
    return _unique_non_empty(keys)


def _split_merged_payment_key(value):
    if not value:
        return []
    parts = str(value).split(PAYMENT_KEY_SEPARATOR)
    return [p.strip() for p in parts if str(p).strip()]


def _generate_merged_payment_key_from_payment_info(payment_info):
    """
    Prefer UI-provided merged_payment_key.
    If missing, generate with the SAME format as CSV parsing:
      MM/DD/YYYY + normalized_amount + notes
    """
    provided = payment_info.get('merged_payment_key')
    if provided and str(provided).strip():
        return str(provided).strip()

    # Prefer original file fields if present
    date_value = payment_info.get('file_date') or payment_info.get('payment_date')
    amount_value = payment_info.get('file_amount') or payment_info.get('amount')
    notes_value = payment_info.get('file_notes') or payment_info.get('notes') or ''

    d = _parse_iso_or_mdy_date(date_value)
    if not d:
        return ''
    date_formatted = d.strftime('%m/%d/%Y').zfill(10)
    amount_norm = remove_trailing_zeros_from_str(str(_safe_float(amount_value, 0.0)))
    return date_formatted + amount_norm + str(notes_value)


def _query_merged_db_payments_for_keys(keys):
    if not keys:
        return Payment.objects.none()
    q = Q()
    for key in keys:
        q |= Q(merged_payment_key__icontains=key)
    return Payment.objects.filter(payment_status='Merged').filter(q).select_related(
        'payment_type', 'payment_method', 'apartment', 'booking__tenant', 'bank'
    )


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

    _log(
        "fetch_db_payments_for_matching.enter",
        rid=rid,
        matching_mode=matching_mode,
        file_payments=len(file_payments),
        db_days_before=db_days_before,
        db_days_after=db_days_after,
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

    db_payments_qs = query_db_payments_custom(start_date, end_date, db_days_before, db_days_after)
    db_payments_list = get_json_list(db_payments_qs)
    merged_keys = _extract_merged_keys_from_file_payments(file_payments)
    merged_db_qs = _query_merged_db_payments_for_keys(merged_keys)
    merged_db_list = get_json_list(merged_db_qs)
    if merged_db_list:
        existing_ids = {p.get('id') for p in db_payments_list}
        for p in merged_db_list:
            if p.get('id') not in existing_ids:
                db_payments_list.append(p)
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


@user_has_role('Admin')
def fetch_merged_db_payments_for_file(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    rid = _request_id(request)
    _trace_event(request, "fetch_merged_db_payments_for_file.request_json", data)
    file_payments = data.get('file_payments') or []
    merged_keys = _extract_merged_keys_from_file_payments(file_payments)
    _log(
        "fetch_merged_db_payments_for_file.enter",
        rid=rid,
        file_payments=len(file_payments),
        merged_keys=len(merged_keys),
    )

    merged_db_qs = _query_merged_db_payments_for_keys(merged_keys)
    merged_db_list = get_json_list(merged_db_qs)
    resp = {'db_payments': merged_db_list}
    _trace_event(request, "fetch_merged_db_payments_for_file.response_json", resp)
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
    """Dedupe and filter blank items. Returns [] when input is empty or all blank."""
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
    tenants = []
    methods = []
    banks = []
    payment_types = []
    notes_list = []

    for p in selected:
        amounts.append(_safe_float(p.get('amount'), 0.0))
        d = _parse_iso_or_mdy_date(p.get('payment_date'))
        if d:
            dates.append(d)
        for cand in (p.get('apartment_candidates') or []):
            apartments.append(cand)
        for cand in (p.get('tenant_candidates') or []):
            tenants.append(cand)
        for cand in (p.get('payment_method_candidates') or []):
            methods.append(cand)
        for cand in (p.get('bank_candidates') or []):
            banks.append(cand)
        for cand in (p.get('payment_type_candidates') or []):
            payment_types.append(cand)
        notes_list.append(p.get('notes') or '')

    amount_total = sum(amounts)
    date_from = min(dates) if dates else None
    date_to = max(dates) if dates else None

    apt_candidates = _unique_non_empty(apartments)
    tenant_candidates = _unique_non_empty(tenants)
    method_candidates = _unique_non_empty(methods)
    bank_candidates = _unique_non_empty(banks)
    payment_type_candidates = _unique_non_empty(payment_types)

    direction = None
    booking_id = None
    for p in selected:
        d = p.get('payment_type_type')
        if d and direction is None:
            direction = str(d)
        bid = p.get('booking')
        if bid is not None and bid and booking_id is None:
            try:
                booking_id = int(bid)
            except (ValueError, TypeError):
                pass

    return {
        'amount_total': amount_total,
        'date_from': date_from,
        'date_to': date_to,
        'apartment_candidates': apt_candidates,
        'tenant_candidates': tenant_candidates,
        'payment_method_candidates': method_candidates,
        'bank_candidates': bank_candidates,
        'payment_type_candidates': payment_type_candidates,
        'notes_list': notes_list,
        'direction': direction,  # 'In' / 'Out' or None
        'booking_id': booking_id,
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

    booking_display = _format_booking_display(p.booking) if getattr(p, 'booking', None) else None
    if not apartment_name and getattr(p, 'booking', None) and getattr(p.booking, 'apartment', None):
        apartment_name = p.booking.apartment.name

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
        'apartment': p.apartment.id if p.apartment else (p.booking.apartment.id if getattr(p, 'booking', None) and getattr(p.booking, 'apartment', None) else None),
        'apartment_name': apartment_name or None,
        'booking': p.booking.id if p.booking else None,
        'booking_display': booking_display,
        'tenant_name': tenant_name or '',
        'payment_status': p.payment_status or '',
        'merged_payment_key': p.merged_payment_key or '',
        'notes': p.notes or '',
        'tenant_notes': p.tenant_notes or '',
        'keywords': p.keywords or '',
    }


def _tokenize(s):
    if not s:
        return set()
    s2 = re.sub(r'[^a-zA-Z0-9]+', ' ', str(s)).strip().lower()
    parts = [p for p in s2.split(' ') if p and len(p) >= 3]
    return set(parts)


def _manual_score_db_payment(db_payment_obj, composite, amount_delta, date_delta=4):
    """
    Returns a dict with score breakdown, or None (filtered out).
    
    Scoring:
    - Amount: 25 if diff <= amount_delta, 0 if more
    - Date: 20 if diff <= date_delta, 0 if more
    - Apartment: 20 if matched
    - Tenant: 20 if matched
    - Bank: 0 pts, but -30 penalty if not BA or Null
    - Payment Type: 20 if matched and not "Other", 5 if "Other" matched
    - Payment Method: 5 if matched
    - Keywords: 50 if payment.keywords matched
    """
    # Type filter (hard) when direction is known
    direction = composite.get('direction')
    if direction and db_payment_obj.payment_type and db_payment_obj.payment_type.type != direction:
        return None

    breakdown_details = []
    penalties = []

    # --- Amount scoring ---
    amount_total = float(composite.get('amount_total') or 0)
    db_amount = float(db_payment_obj.amount or 0)
    selected_count = max(1, len(composite.get('notes_list') or []))
    delta_amount = float(amount_delta) * selected_count
    diff_amount = abs(db_amount - amount_total)

    if delta_amount <= 0:
        delta_amount = 1.0

    if diff_amount <= delta_amount:
        amount_score = 25.0
    else:
        amount_score = 0.0

    breakdown_details.append({
        'field': 'amount',
        'label': 'Amount',
        'value': f"diff ${diff_amount:.2f}",
        'score': amount_score,
        'max': 25,
        'matched': diff_amount <= delta_amount,
    })

    # --- Date scoring ---
    d = db_payment_obj.payment_date
    date_from = composite.get('date_from')
    date_to = composite.get('date_to')
    date_score = 0.0
    date_diff_days = None
    
    if d and date_from and date_to:
        if date_from <= d <= date_to:
            date_diff_days = 0
            date_score = 20.0
        else:
            date_diff_days = (date_from - d).days if d < date_from else (d - date_to).days
            if abs(date_diff_days) <= date_delta:
                date_score = 20.0
            else:
                date_score = 0.0
    
    breakdown_details.append({
        'field': 'date',
        'label': 'Date',
        'value': f"diff {abs(date_diff_days) if date_diff_days is not None else '?'} days",
        'score': date_score,
        'max': 20,
        'matched': date_score > 0,
    })

    # --- Apartment scoring ---
    apt = (db_payment_obj.apartmentName or '').strip() if hasattr(db_payment_obj, 'apartmentName') else ''
    if not apt and getattr(db_payment_obj, 'apartment', None):
        apt = (db_payment_obj.apartment.name or '').strip()

    comp_apt_all = composite.get('apartment_candidates') or []
    apt_matched = False
    apt_score = 0.0
    
    if apt:
        if comp_apt_all and apt in comp_apt_all:
            apt_matched = True
            apt_score = 20.0
    
    breakdown_details.append({
        'field': 'apartment',
        'label': 'Apartment',
        'value': apt if apt else '(none)',
        'score': apt_score,
        'max': 20,
        'matched': apt_matched,
    })

    # --- Tenant scoring ---
    tenant_name = ''
    if getattr(db_payment_obj, 'booking', None) and getattr(db_payment_obj.booking, 'tenant', None):
        tenant_name = (db_payment_obj.booking.tenant.full_name or '').strip()
    
    tenant_score = 0.0
    tenant_matched = False
    composite_notes = ' '.join(str(n) for n in (composite.get('notes_list') or []) if n).lower()
    comp_tenant_all = composite.get('tenant_candidates') or []
    
    if tenant_name:
        # Primary: exact match against selected tenant candidates (if available)
        if comp_tenant_all and tenant_name in comp_tenant_all:
            tenant_matched = True
            tenant_score = 20.0
        else:
            # Fallback: token match in notes (legacy behavior)
            tenant_parts = [p.strip().lower() for p in tenant_name.split() if len(p.strip()) >= 3]
            if tenant_parts:
                matches = sum(1 for p in tenant_parts if p in composite_notes)
                if matches >= 1:
                    tenant_matched = True
                    tenant_score = 20.0
    
    breakdown_details.append({
        'field': 'tenant',
        'label': 'Tenant',
        'value': tenant_name if tenant_name else '(none)',
        'score': tenant_score,
        'max': 20,
        'matched': tenant_matched,
    })

    # --- Bank scoring (penalty if not BA or Null) ---
    db_bank = (db_payment_obj.bank.name if db_payment_obj.bank else '') or ''
    bank_penalty = 0.0
    
    if db_bank and db_bank.upper() != 'BA':
        bank_penalty = -30.0
        penalties.append({
            'field': 'bank',
            'label': 'Bank',
            'value': f"not BA ({db_bank})",
            'penalty': bank_penalty,
        })
    else:
        breakdown_details.append({
            'field': 'bank',
            'label': 'Bank',
            'value': db_bank if db_bank else '(none/BA)',
            'score': 0,
            'max': 0,
            'matched': True if db_bank else False,
        })

    # --- Payment Type scoring ---
    pt_score = 0.0
    pt_matched = False
    pt_name = ''
    pt_type = ''
    
    # Check if file/composite has type "Other" (we can't confirm specific type matches)
    comp_pt_candidates = composite.get('payment_type_candidates') or []
    comp_pt_names = [str(c).split(' (')[0].strip().lower() for c in comp_pt_candidates if c]
    file_has_other_only = comp_pt_names and all(n == 'other' for n in comp_pt_names)

    if db_payment_obj.payment_type:
        pt_name = db_payment_obj.payment_type.name or ''
        pt_type = db_payment_obj.payment_type.type or ''
        
        # Check if payment type matches composite direction (direction is filtered earlier)
        if direction and pt_type == direction:
            if file_has_other_only:
                if pt_name.lower() == 'other':
                    pt_matched = True
                    pt_score = 5.0   # Other + Other: soft match
                else:
                    pt_matched = False  # File said Other - can't confirm specific type
                    pt_score = 0.0
            else:
                if pt_name.lower() == 'other':
                    pt_matched = False
                    pt_score = 0.0   # DB Other, file specific - no score
                else:
                    pt_matched = True
                    pt_score = 20.0
    
    breakdown_details.append({
        'field': 'payment_type',
        'label': 'Payment Type',
        'value': f"{pt_name} ({pt_type})" if pt_name else '(none)',
        'score': pt_score,
        'max': 20,
        'matched': pt_matched,
    })

    # --- Payment Method scoring ---
    db_method = (db_payment_obj.payment_method.name if db_payment_obj.payment_method else '') or ''
    comp_method_candidates = composite.get('payment_method_candidates') or []
    method_score = 0.0
    method_matched = False
    
    if db_method and comp_method_candidates:
        comp_method_lower = [str(c).strip().lower() for c in comp_method_candidates if c]
        if db_method.lower() in comp_method_lower:
            method_matched = True
            method_score = 5.0
    
    breakdown_details.append({
        'field': 'payment_method',
        'label': 'Payment Method',
        'value': db_method if db_method else '(none)',
        'score': method_score,
        'max': 5,
        'matched': method_matched,
    })

    # --- Keywords scoring (from db_payment.keywords field) ---
    db_payment_keywords = (db_payment_obj.keywords or '').strip()
    keywords_score = 0.0
    matched_keywords = []
    
    if db_payment_keywords:
        # Split keywords and check against composite notes
        kw_list = [k.strip().lower() for k in db_payment_keywords.split(',') if k.strip()]
        for kw in kw_list:
            if kw and len(kw) >= 3 and kw in composite_notes:
                matched_keywords.append(kw)
        
        if matched_keywords:
            keywords_score = 50.0
    
    breakdown_details.append({
        'field': 'keywords',
        'label': 'Keywords',
        'value': ', '.join(matched_keywords) if matched_keywords else (f"({db_payment_keywords[:30]}...)" if db_payment_keywords and len(db_payment_keywords) > 30 else f"({db_payment_keywords})" if db_payment_keywords else '(none)'),
        'score': keywords_score,
        'max': 50,
        'matched': len(matched_keywords) > 0,
    })

    # --- Booking scoring (when file payment has N{number} booking and DB payment matches) ---
    booking_score = 0.0
    comp_booking_id = composite.get('booking_id')
    db_booking_id = getattr(db_payment_obj, 'booking_id', None)
    if comp_booking_id is not None and db_booking_id is not None and comp_booking_id == db_booking_id:
        booking_score = 50.0
    breakdown_details.append({
        'field': 'booking',
        'label': 'Booking',
        'value': f"Match (ID {db_booking_id})" if booking_score else (f"ID {db_booking_id}" if db_booking_id else '(none)'),
        'score': booking_score,
        'max': 50,
        'matched': booking_score > 0,
    })

    # --- Calculate total ---
    total = amount_score + date_score + apt_score + tenant_score + pt_score + method_score + keywords_score + booking_score + bank_penalty
    total = max(0.0, total)

    # Apply penalty for Confirmed payments (push to end). Merged excluded at query level.
    status_penalty = False
    payment_status = getattr(db_payment_obj, 'payment_status', None) or ''
    if payment_status == 'Confirmed':
        total = total * 0.5
        status_penalty = True
        penalties.append({
            'field': 'status',
            'label': 'Status',
            'value': payment_status,
            'penalty': -total,  # Already halved
        })

    # Determine quality
    if total >= 70:
        quality = 'high'
    elif total >= 40:
        quality = 'medium'
    else:
        quality = 'low'

    # Build criteria string (legacy format for backward compatibility)
    criteria_parts = []
    for bd in breakdown_details:
        criteria_parts.append(f"{bd['label']}: {bd['value']} [{bd['score']:.0f}]")
    for pen in penalties:
        criteria_parts.append(f"{pen['label']}: {pen['value']} [{pen['penalty']:.0f}]")

    return {
        'total': round(total, 2),
        'match_type': 'manual_composite',
        'criteria': " | ".join(criteria_parts),
        'breakdown': {
            'amount': round(amount_score, 1),
            'date': round(date_score, 1),
            'apartment': round(apt_score, 1),
            'tenant': round(tenant_score, 1),
            'payment_type': round(pt_score, 1),
            'payment_method': round(method_score, 1),
            'keywords': round(keywords_score, 1),
            'booking': round(booking_score, 1),
            'bank_penalty': round(bank_penalty, 1),
        },
        'breakdown_details': breakdown_details,
        'penalties': penalties,
        'matched_keywords': matched_keywords,
        'quality': quality,
        'status_penalty': status_penalty,
    }


def _ai_prefilter_top100(db_payments_qs, composite, amount_delta):
    """
    Returns list of Payment objects filtered by direction only. All pass to AI.
    """
    direction = composite.get('direction')
    candidates = list(db_payments_qs)
    if direction:
        candidates = [p for p in candidates if p.payment_type and p.payment_type.type == direction]
    return candidates


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
            },
        )

    # Minify candidate payload (sparse: only include fields when set, to save tokens)
    candidate_payload = []
    for p in candidate_payments or []:
        tenant_name = ''
        if getattr(p, 'booking', None) and getattr(p.booking, 'tenant', None):
            tenant_name = p.booking.tenant.full_name
        apt = (p.apartmentName or '').strip() if hasattr(p, 'apartmentName') else ''
        if not apt and getattr(p, 'apartment', None):
            apt = (p.apartment.name or '').strip()
        item = {'db_id': p.id, 'amount': float(p.amount or 0)}
        if p.payment_date:
            item['payment_date'] = p.payment_date.isoformat()
        if p.payment_type:
            item['payment_type'] = p.payment_type.name
        if apt:
            item['apartment'] = apt
        if tenant_name:
            item['tenant'] = tenant_name
        if p.bank:
            item['bank'] = p.bank.name
        if p.payment_method:
            item['payment_method'] = p.payment_method.name
        notes = (p.notes or '').strip()
        if notes:
            item['notes'] = notes
        if getattr(p, 'booking', None):
            item['booking_id'] = p.booking_id
        candidate_payload.append(item)

    # Sparse composite_desc: only include non-empty fields
    composite_desc = {'amount_total': float(composite.get('amount_total') or 0)}
    if composite.get('date_from'):
        composite_desc['date_from'] = composite.get('date_from').isoformat()
    if composite.get('date_to'):
        composite_desc['date_to'] = composite.get('date_to').isoformat()
    v = composite.get('apartment_candidates')
    if v:
        composite_desc['apartment_candidates'] = v
    v = composite.get('tenant_candidates')
    if v:
        composite_desc['tenant_candidates'] = v
    v = composite.get('payment_method_candidates')
    if v:
        composite_desc['payment_method_candidates'] = v
    v = composite.get('bank_candidates')
    if v:
        composite_desc['bank_candidates'] = v
    v = composite.get('payment_type_candidates')
    if v:
        composite_desc['payment_type_candidates'] = v
    v = composite.get('notes_list')
    if v:
        composite_desc['notes_list'] = v
    if composite.get('booking_id') is not None:
        composite_desc['booking_id'] = composite['booking_id']

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
    amount_delta = int(data.get('amount_delta', 100))
    date_delta = int(data.get('date_delta', 4))
    ai_model = (data.get('ai_model') or 'google/gemini-3-flash-preview').strip()
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

    # Query payments, excluding Merged (already matched to bank file)
    db_qs = Payment.objects.filter(payment_date__range=(db_from, db_to)).exclude(
        payment_status='Merged'
    ).select_related(
        'payment_type', 'payment_method', 'apartment', 'booking__tenant', 'bank'
    )
    _log("match_selection_v2.db_window", rid=rid, db_from=db_from, db_to=db_to, db_qs_count=db_qs.count())
    _trace_event(request, "match_selection_v2.db_window", {"db_from": db_from, "db_to": db_to, "db_qs_count": db_qs.count()})

    # Manual result
    manual_candidates = None
    if mode in ('manual', 'both'):
        candidates = []
        for p in db_qs:
            scored = _manual_score_db_payment(p, composite, amount_delta, date_delta)
            if not scored:
                continue
            if scored['total'] <= 0:
                continue
            candidates.append({
                'type': 'manual',
                'db_payment': _payment_to_rich_dict(p),
                'score': scored['total'],
                'match_type': scored['match_type'],
                'criteria': scored['criteria'],
                'breakdown': scored['breakdown'],
                'breakdown_details': scored.get('breakdown_details', []),
                'penalties': scored.get('penalties', []),
                'matched_keywords': scored['matched_keywords'],
                'quality': scored['quality'],
                'status_penalty': scored.get('status_penalty', False),
            })
        candidates.sort(key=lambda c: c.get('score', 0), reverse=True)
        candidates = candidates[:50]
        manual_candidates = candidates
        if manual_candidates:
            amt = next((d for d in (manual_candidates[0].get('breakdown_details') or []) if d.get('field') == 'amount'), None)
            _log("match_selection_v2.manual_done", rid=rid, returned=len(manual_candidates), first_amount=amt)
        else:
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

    payload = {
        'selected_key': selection_key,
        'matched_payments': matched_payments,
        'scoring_version': 2,
    }
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
    lines = [line for line in file_data.splitlines() if line.strip()]

    # Find header row
    start_index = None
    for i, line in enumerate(lines):
        if _is_payment_header_line(line):
            start_index = i + 1
            break

    if start_index is None:
        messages.error(request, "CSV file does not contain the expected header.")
        _log("parse_csv_file.no_header", rid=rid, filename=getattr(csv_file, "name", None))
        return []

    payment_data = []

    # Process each line
    for idx, line in enumerate(lines[start_index:], start=start_index):
        if not line or not line.strip():
            continue
        try:
            payment = parse_csv_row(line, idx, payment_methods, apartments, payment_types)
            if payment:
                payment_data.append(payment)
        except Exception as e:
            messages.warning(request, f"Error parsing row {idx}: {str(e)}")
            _log("parse_csv_file.row_error", rid=rid, row=idx, error=str(e))
            continue

    _log("parse_csv_file.done", rid=rid, parsed=len(payment_data))
    return payment_data


def enrich_file_payments_with_booking_apartments(file_payments, bookings):
    """
    Adds booking-derived apartments and tenants into file payment candidates lists.
    Preserves N-extracted booking (from parse) — only adds from match_booking_context.
    """
    for p in file_payments or []:
        desc = p.get("notes") or ""
        existing_apt = p.get("apartment_candidates") or []
        booking_apts, booking_tenants = match_booking_context_candidates(desc, bookings)
        p["apartment_candidates"] = _unique_non_empty(list(existing_apt) + list(booking_apts))

        existing_tenant = p.get("tenant_candidates") or []
        p["tenant_candidates"] = _unique_non_empty(list(existing_tenant) + list(booking_tenants))

    return file_payments


def preprocess_csv_line(line):
    """Preprocess CSV line to handle malformed data"""
    splited = line.split(',')
    if len(splited) > 4:
        corrected_line = splited[0] + ',' + ' '.join(splited[1:-2]) + ',' + splited[-2] + ',' + splited[-1]
    else:
        corrected_line = line
    return corrected_line


def parse_csv_row(line, idx, payment_methods, apartments, payment_types):
    """Parse a single CSV row into payment data"""
    parsed = _split_payment_csv_line(line)
    if not parsed:
        return None

    date_str, description, amount_str, running_bal = parsed

    # Parse amount
    amount_float = _parse_amount_value(amount_str)
    if amount_float == 0:
        return None

    # Determine payment type based on amount + keywords
    payment_type = match_payment_type(description, amount_float, payment_types)

    # Match payment method
    payment_method_to_assign = match_payment_method(description, payment_methods)

    # Match apartment (name/keywords only). Booking-based candidates are added later.
    apartment_candidates = match_apartment_candidates(description, apartments)
    apartment_to_assign = apartment_candidates[0] if len(apartment_candidates) == 1 else None

    # Extract booking ID from notes (N{number} pattern) and attach booking if found
    booking = None
    booking_display = None
    bid = _extract_booking_id_from_notes(description)
    if bid:
        booking = Booking.objects.filter(id=bid).select_related('apartment').first()
        if booking:
            booking_display = _format_booking_display(booking)
            apt_name = getattr(getattr(booking, 'apartment', None), 'name', None) if booking.apartment else None
            if apt_name and apt_name not in [getattr(a, 'name', '') for a in apartment_candidates]:
                apartment_candidates = list(apartment_candidates)
                apartment_candidates.insert(0, booking.apartment)
            if not apartment_to_assign and apartment_candidates:
                apartment_to_assign = apartment_candidates[0]

    # Extract ID if present
    extracted_id = extract_id_from_description(description)

    # Get bank (PaymentMethod rows with type='Bank')
    ba_bank = payment_methods.filter(type="Bank", name__iexact="BA").first()
    if not ba_bank:
        ba_bank = payment_methods.filter(type="Bank", name__icontains="bank of america").first()

    parsed_date = parse_payment_date(date_str.strip())
    if not parsed_date:
        raise ValueError(f"Unsupported date format: {date_str}")
    payment_date = parsed_date if isinstance(parsed_date, datetime) else datetime.combine(parsed_date, datetime.min.time())

    pt_display = f'{payment_type.name} ({payment_type.type})'
    result = {
        'id': extracted_id or f'id_{idx}',
        'payment_date': payment_date,
        'payment_type': payment_type.id,
        'payment_type_candidates': [pt_display],
        'payment_type_type': payment_type.type,
        'notes': description.strip(),
        'amount': abs(amount_float),
        'payment_method': payment_method_to_assign.id if payment_method_to_assign else None,
        'payment_method_candidates': [payment_method_to_assign.name] if payment_method_to_assign else [],
        'merged_payment_key': generate_payment_key(date_str.strip(), amount_float, description.strip()),
        'bank': ba_bank.id if ba_bank else None,
        'bank_candidates': [ba_bank.name] if ba_bank else [],
        'apartment': apartment_to_assign.id if apartment_to_assign else None,
        'apartment_candidates': [a.name for a in apartment_candidates],
        'tenant_candidates': [],
        'booking': booking.id if booking else None,
        'booking_display': booking_display,
    }
    return result


def _is_payment_header_line(line):
    normalized = line.replace('"', '').strip().lower()
    if not normalized.startswith('date,description,amount'):
        return False
    return 'running' in normalized


def _strip_wrapping_quotes(value):
    if value is None:
        return ''
    s = str(value).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _parse_amount_value(value):
    s = _strip_wrapping_quotes(value).replace(',', '').strip()
    if not s:
        return 0.0
    is_paren_negative = s.startswith('(') and s.endswith(')')
    if is_paren_negative:
        s = s[1:-1].strip()
    try:
        amount = float(s)
    except Exception:
        return 0.0
    return -amount if is_paren_negative else amount


def _split_payment_csv_line(line):
    raw = (line or '').strip()
    if not raw:
        return None

    # Try CSV parsing first (handles proper quoting)
    try:
        row = next(csv.reader([raw]))
    except Exception:
        row = None

    if row and len(row) >= 4:
        date_str = row[0]
        amount_str = row[-2] if len(row) >= 2 else ''
        running_bal = row[-1] if len(row) >= 1 else ''
        if len(row) > 4:
            description = ','.join(row[1:-2])
        else:
            description = row[1]
        return date_str, description, amount_str, running_bal

    # Fallback: split by last two comma-separated numeric fields
    base_match = re.match(r'^\s*([^,]+)\s*,(.*)\s*$', raw)
    if not base_match:
        return None
    date_str = base_match.group(1).strip()
    rest = base_match.group(2)

    tail_match = re.match(
        r'^(?P<desc>.*),(?P<amount>"?[-\d,().]*"?),(?P<running>"?[-\d,().]*"?)\s*$',
        rest,
    )
    if not tail_match:
        return None

    description = _strip_wrapping_quotes(tail_match.group('desc'))
    amount_str = tail_match.group('amount')
    running_bal = tail_match.group('running')
    return date_str, description, amount_str, running_bal


def match_payment_method(description, payment_methods):
    """Match payment method based on description"""
    description_lower = description.strip().lower()
    
    # Check for direct name match
    for payment_method in payment_methods:
        if getattr(payment_method, "type", None) != "Payment Method":
            continue
        if payment_method.name.lower() in description_lower:
            return payment_method
    
    # Special case for mobile deposits
    if 'deposit *mobile' in description_lower:
        return payment_methods.filter(name="Check").first()
    
    # Check for keyword match
    for payment_method in payment_methods:
        if getattr(payment_method, "type", None) != "Payment Method":
            continue
        if payment_method.keywords:
            keywords_array = [keyword.strip() for keyword in payment_method.keywords.split(",")]
            if any(keyword.strip().lower() in description_lower for keyword in keywords_array):
                return payment_method
    
    return None


def match_payment_type(description, amount_float, payment_types):
    """
    Choose PaymenType based on:
    - direction from amount sign (In if >0 else Out)
    - keyword/name hits in the description
    - fallback to Other (In/Out)
    """
    description_lower = (description or "").strip().lower()
    direction = "In" if amount_float > 0 else "Out"

    candidates = payment_types.filter(type=direction)

    # 1) direct name match
    for pt in candidates:
        if pt.name and pt.name.strip().lower() in description_lower:
            return pt

    # 2) keyword match - pick best by (hit count, longest hit)
    best = None
    best_hits = 0
    best_longest = 0
    for pt in candidates:
        if not pt.keywords:
            continue
        kws = [k.strip() for k in pt.keywords.split(",") if k.strip()]
        hits = [kw for kw in kws if kw.lower() in description_lower]
        if not hits:
            continue
        longest = max(len(h) for h in hits)
        if len(hits) > best_hits or (len(hits) == best_hits and longest > best_longest):
            best = pt
            best_hits = len(hits)
            best_longest = longest

    if best:
        return best

    # 3) default
    other = payment_types.filter(name="Other", type=direction).first()
    if other:
        return other
    return candidates.first()


def _normalize_match_text(s):
    return (str(s or "")).strip().lower()


def _split_keywords_csv(value):
    if not value:
        return []
    return [k.strip().lower() for k in str(value).split(",") if k and str(k).strip()]


def _best_keyword_hit(candidates, desc_lower):
    """
    candidates: iterable of tuples (obj, keywords_list, name_string)
    returns best obj or None using (hit_count, longest_hit)
    """
    best_obj = None
    best_hits = 0
    best_longest = 0
    for obj, kws, _name in candidates:
        hits = [kw for kw in kws if kw and kw in desc_lower]
        if not hits:
            continue
        longest = max(len(h) for h in hits)
        if len(hits) > best_hits or (len(hits) == best_hits and longest > best_longest):
            best_obj = obj
            best_hits = len(hits)
            best_longest = longest
    return best_obj


def match_apartment_candidates(description, apartments):
    """
    Return list of matching Apartment objects.
    Matching order:
    - full apartment name substring match
    - apartment.keywords substring match (comma-separated)
    """
    desc = _normalize_match_text(description)
    if not desc:
        return []

    full_matches = []
    for apt in apartments:
        full = _normalize_match_text(getattr(apt, "name", ""))
        if full and full in desc:
            full_matches.append(apt)
    if full_matches:
        return full_matches

    keyword_matches = []
    for apt in apartments:
        kws = _split_keywords_csv(getattr(apt, "keywords", None))
        if not kws:
            continue
        if any(kw in desc for kw in kws):
            keyword_matches.append(apt)
    return keyword_matches


def match_apartment(description, apartments):
    """
    Backward-compatible single-apartment matcher.
    Uses full name, then best keyword hit.
    """
    desc = _normalize_match_text(description)
    if not desc:
        return None

    # full name first
    for apt in apartments:
        full = _normalize_match_text(getattr(apt, "name", ""))
        if full and full in desc:
            return apt

    # best keyword match
    candidates = []
    for apt in apartments:
        kws = _split_keywords_csv(getattr(apt, "keywords", None))
        candidates.append((apt, kws, getattr(apt, "name", "")))
    return _best_keyword_hit(candidates, desc)


def match_booking_context_candidates(description, bookings):
    """
    Return (apartment_names, tenant_names) derived from bookings when description matches:
    - booking.keywords (comma-separated)
    - tenant full name (exact substring, no token split)
    """
    desc = _normalize_match_text(description)
    if not desc:
        return [], []

    matched_apartment_names = []
    seen_apts = set()
    matched_tenant_names = []
    seen_tenants = set()

    for b in bookings or []:
        apt = getattr(b, "apartment", None)
        apt_name = _normalize_match_text(getattr(apt, "name", ""))
        if not apt_name:
            continue

        keywords = _split_keywords_csv(getattr(b, "keywords", None))

        tenant = getattr(b, "tenant", None)
        tenant_full = _normalize_match_text(getattr(tenant, "full_name", ""))

        matched = False
        if tenant_full and tenant_full in desc:
            matched = True
        elif keywords and any(k in desc for k in keywords):
            matched = True

        if matched and apt_name not in seen_apts:
            seen_apts.add(apt_name)
            # keep original casing as stored on model if possible
            matched_apartment_names.append(getattr(apt, "name", "") or apt_name)

        if matched:
            tenant_display = (getattr(tenant, "full_name", "") or "").strip()
            tenant_key = _normalize_match_text(tenant_display)
            if tenant_key and tenant_key not in seen_tenants:
                seen_tenants.add(tenant_key)
                matched_tenant_names.append(tenant_display)

    return matched_apartment_names, matched_tenant_names

def _match_apartment_token_split_legacy(description, apartments):
    """
    Legacy token-splitting matcher (kept for reference).
    Current V2 flow uses full name + keywords only via `match_apartment_candidates` / `match_apartment`.
    """
    desc = (description or "").strip().lower()
    if not desc:
        return None
    for apt in apartments:
        full = str(getattr(apt, "name", "") or "").strip().lower()
        if full and full in desc:
            return apt
    return None


def extract_id_from_description(description):
    """Extract payment ID from description if present"""
    if "@S3" in description:
        match = re.search(r"@S3(\d+)", description)
        if match:
            return int(match.group(1))
    return None


def _extract_booking_id_from_notes(notes):
    """Extract booking ID from notes if pattern ' N{number} ' present. Returns int or None."""
    if not notes:
        return None
    match = re.search(r'\s+N(\d+)\s+', str(notes))
    return int(match.group(1)) if match else None


def _format_booking_display(booking):
    """Format booking dates as '3 March - 10 March'. Returns None if booking has no dates."""
    if not booking or not hasattr(booking, 'start_date') or not hasattr(booking, 'end_date'):
        return None
    if not booking.start_date or not booking.end_date:
        return None
    start_str = f"{booking.start_date.day} {booking.start_date.strftime('%B')}"
    end_str = f"{booking.end_date.day} {booking.end_date.strftime('%B')}"
    return f"{start_str} - {end_str}"


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


def query_db_payments(start_date, end_date):
    """Query all database payments within date range."""
    if start_date is None or end_date is None:
        return Payment.objects.none()
    
    # Add buffer to date range
    date_from = start_date - timedelta(days=45)
    date_to = end_date + timedelta(days=45)
    
    return Payment.objects.filter(
        payment_date__range=(date_from, date_to)
    ).select_related('payment_type', 'payment_method', 'apartment', 'booking__tenant')



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
    db_apt = (db_payment.apartmentName or '').strip()
    fp_all = [str(x).strip() for x in (file_payment.get('apartment_candidates') or []) if str(x).strip()]
    if db_apt and fp_all:
        if db_apt in fp_all:
            score += 10
            details['apartment'] = 'Match'
        else:
            details['apartment'] = 'Different'
    else:
        details['apartment'] = 'N/A'

    # Tenant matching (weight: low)
    fp_tenant_all = [str(x).strip() for x in (file_payment.get('tenant_candidates') or []) if str(x).strip()]
    db_tenant = ''
    try:
        if db_payment.booking and db_payment.booking.tenant:
            db_tenant = (db_payment.booking.tenant.full_name or '').strip()
    except Exception:
        db_tenant = ''
    if db_tenant and fp_tenant_all:
        if db_tenant in fp_tenant_all:
            score += 6
            details['tenant'] = 'Match'
        else:
            details['tenant'] = 'No Match'
    else:
        details['tenant'] = 'N/A'
    
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
                    'merged_payment_key': db_payment.merged_payment_key or '',
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
            tenant = getattr(original_obj.booking, 'tenant', None)
            item["tenant_name"] = (getattr(tenant, 'full_name', '') or '') if tenant else ''
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


def _default_payment_type_pk():
    d = Payment._meta.get_field("payment_type").default
    return int(d) if d is not None else 2


def _resolve_payment_type_id(payment_info, existing_type_id=None):
    """Merge modal may send null/empty payment_type for 'New' + file rows without a type."""
    raw = payment_info.get("payment_type")
    if raw is not None and raw != "" and str(raw).lower() != "null":
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
    if existing_type_id:
        return int(existing_type_id)
    return _default_payment_type_pk()


def update_payments(request, payments_to_update, add_per_payment_messages=True):
    """Update or create payments in database"""
    rid = _request_id(request)
    _log("update_payments.start", rid=rid, count=len(payments_to_update or []), user=_user_tag(request))
    # Track per-key which payment ids we've saved in this batch (1→2: multiple share same key)
    key_to_saved_ids = {}
    for payment_info in payments_to_update:
        payment_id = None
        try:
            raw_id = payment_info.get('id')
            try:
                numeric_id = int(raw_id) if raw_id not in (None, '', 'null', 'new') else None
            except (ValueError, TypeError):
                numeric_id = None
            merged_key = _generate_merged_payment_key_from_payment_info(payment_info)
            exclude_ids = set(key_to_saved_ids.get(merged_key, [])) if merged_key else set()
            if numeric_id:
                exclude_ids.add(numeric_id)  # current payment for update
            if merged_key and _merged_payment_key_exists_in_db(merged_key, exclude_payment_ids=exclude_ids):
                raise ValueError(
                    "A payment with this merged_payment_key already exists in the database. "
                    "This bank file payment may already be merged."
                )
            if numeric_id:
                # Update existing payment
                payment = Payment.objects.get(id=numeric_id)
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
                if add_per_payment_messages:
                    messages.success(request, f"Updated Payment: {payment.id}")
                if merged_key:
                    key_to_saved_ids.setdefault(merged_key, set()).add(payment_id)
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
                if add_per_payment_messages:
                    messages.success(request, f"Created new Payment: {payment.id}")
                if merged_key:
                    key_to_saved_ids.setdefault(merged_key, set()).add(payment.id)
        except Exception as e:
            _log(
                "update_payments.error",
                rid=rid,
                payment_id=payment_id or payment_info.get("id"),
                error=str(e),
            )
            messages.error(request, f"Failed to {'update' if payment_id else 'create'} payment: {payment_id or ''} due {str(e)}")
            raise
    _log("update_payments.done", rid=rid)


def update_payment_fields(payment, payment_info):
    """Update payment object fields"""
    payment.amount = float(payment_info['amount'])
    
    # Determine payment_date
    date_str = payment_info.get('payment_date') or payment_info.get('file_date', '')
    if not date_str:
        raise ValueError("Payment date is required")
    payment.payment_date = parse_payment_date(date_str)
    
    payment.payment_type_id = _resolve_payment_type_id(payment_info, getattr(payment, "payment_type_id", None))
    payment.notes = payment_info['notes']
    payment.payment_method_id = payment_info['payment_method'] or None
    payment.bank_id = payment_info['bank'] or None
    booking_id = payment_info.get('booking')
    if booking_id is not None and booking_id != '':
        try:
            payment.booking_id = int(booking_id)
            payment.apartment_id = None
        except (ValueError, TypeError):
            payment.booking_id = None
            payment.apartment_id = payment_info.get('apartment') or None
    else:
        payment.booking_id = None
        payment.apartment_id = payment_info.get('apartment') or None
    payment.payment_status = payment_info.get('payment_status') or "Merged"
    payment.tenant_notes = payment_info.get('tenant_notes') or ''
    payment.keywords = payment_info.get('keywords') or ''
    
    # Set merged payment key (1 bank → 2+ DB: multiple payments can share same key)
    merged_payment_key = _generate_merged_payment_key_from_payment_info(payment_info)
    payment.merged_payment_key = merged_payment_key


def _merged_payment_key_exists_in_db(merged_payment_key, exclude_payment_ids=None):
    """Check if any payment already has this merged_payment_key (exact or as segment).
    exclude_payment_ids: set/list of payment ids to exclude (e.g. same-batch or current)."""
    keys = _split_merged_payment_key(merged_payment_key)
    if not keys:
        return False
    base_qs = Payment.objects.exclude(merged_payment_key__isnull=True).exclude(merged_payment_key='')
    if exclude_payment_ids:
        base_qs = base_qs.exclude(id__in=exclude_payment_ids)
    for key in keys:
        q = (
            Q(merged_payment_key=key)
            | Q(merged_payment_key__startswith=key + PAYMENT_KEY_SEPARATOR)
            | Q(merged_payment_key__endswith=PAYMENT_KEY_SEPARATOR + key)
            | Q(merged_payment_key__contains=PAYMENT_KEY_SEPARATOR + key + PAYMENT_KEY_SEPARATOR)
        )
        if base_qs.filter(q).exists():
            return True
    return False


def create_new_payment(payment_info):
    """Create new payment object"""
    date_str = payment_info.get('payment_date') or payment_info.get('file_date', '')
    if not date_str:
        raise ValueError("Payment date is required")
    
    # Validate amount is not zero
    amount = float(payment_info['amount'])
    if amount == 0:
        raise ValueError("Payment amount cannot be 0")
    
    merged_payment_key = _generate_merged_payment_key_from_payment_info(payment_info)
    # 1 bank → 2+ DB: multiple payments can share same key; no duplicate check
    booking_id = payment_info.get('booking')
    if booking_id is not None and booking_id != '':
        try:
            bid = int(booking_id)
            apartment_id = None
        except (ValueError, TypeError):
            bid = None
            apartment_id = payment_info.get('apartment') or None
    else:
        bid = None
        apartment_id = payment_info.get('apartment') or None
    
    return Payment(
        amount=amount,
        payment_date=parse_payment_date(date_str),
        payment_type_id=_resolve_payment_type_id(payment_info, None),
        notes=payment_info['notes'],
        payment_method_id=payment_info['payment_method'] or None,
        bank_id=payment_info['bank'] or None,
        apartment_id=apartment_id,
        booking_id=bid,
        merged_payment_key=merged_payment_key,
        payment_status="Merged",
        tenant_notes=payment_info.get('tenant_notes') or '',
        keywords=payment_info.get('keywords') or '',
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

