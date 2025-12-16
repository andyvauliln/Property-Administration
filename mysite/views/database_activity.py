"""
Database Activity Monitoring View
Provides a comprehensive interface to monitor all database changes, errors, and system logs.
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, F
from django.utils import timezone
from datetime import datetime, timedelta
from mysite.models import AuditLog, ErrorLog, SystemLog, Payment, Booking, Cleaning, Apartment, User
from mysite.unified_logger import log_info
import json


def get_data_integrity_issues(orphaned_page=1, zero_amount_page=1, mismatch_page=1, merged_page=1):
    """
    Run data integrity checks and return categorized issues.
    Only shows issues related to recent data (last 1 month) to focus on relevant problems.
    Returns a dict with issues grouped by category.
    
    Args:
        orphaned_page: Page number for orphaned payments pagination (100 per page)
        zero_amount_page: Page number for zero amount payments pagination (100 per page)
        mismatch_page: Page number for payment mismatch pagination (100 per page)
        merged_page: Page number for merged payments pagination (100 per page)
    """
    from datetime import date
    
    # Only show issues for data from the last month (relevant/recent data)
    one_month_ago = date.today() - timedelta(days=30)
    
    # Pagination settings
    ITEMS_PER_PAGE = 50  # Reduced for better performance on low-memory servers
    
    issues = {
        'bookings_without_apartment': [],
        'booking_overlaps': [],
        'payment_apartment_mismatch': [],
        'orphaned_payments': [],
        'zero_amount_payments': [],
        'merged_payments_without_key': [],
        'missing_phones': [],
        'date_filter': one_month_ago,
        'summary': {
            'total': 0,
            'critical': 0,
            'high': 0,
            'medium': 0,
        }
    }
    
    # 1. Bookings without apartment (CRITICAL)
    # Only bookings that end after one_month_ago (recent or ongoing/future)
    bookings_no_apt = Booking.objects.filter(
        apartment__isnull=True,
        end_date__gte=one_month_ago
    ).select_related('tenant')[:50]
    
    for booking in bookings_no_apt:
        issues['bookings_without_apartment'].append({
            'id': booking.id,
            'start_date': booking.start_date,
            'end_date': booking.end_date,
            'status': booking.status,
            'tenant': booking.tenant.full_name if booking.tenant else 'N/A',
            'tenant_email': booking.tenant.email if booking.tenant else 'N/A',
            'created_at': booking.created_at,
        })
    issues['summary']['critical'] += len(issues['bookings_without_apartment'])
    
    # 2. Booking date overlaps (CRITICAL) - optimized to reduce queries
    # Only check bookings that end after one_month_ago (recent or ongoing/future)
    overlaps_found = set()
    bookings = list(Booking.objects.filter(
        end_date__gte=one_month_ago,
        apartment__isnull=False
    ).exclude(status='Blocked').select_related('apartment', 'tenant')[:100])  # Reduced from 500
    
    # Group bookings by apartment for efficient overlap detection
    bookings_by_apartment = {}
    for booking in bookings:
        apt_id = booking.apartment_id
        if apt_id not in bookings_by_apartment:
            bookings_by_apartment[apt_id] = []
        bookings_by_apartment[apt_id].append(booking)
    
    # Check overlaps within each apartment group (no extra queries needed)
    for apt_id, apt_bookings in bookings_by_apartment.items():
        for i, booking in enumerate(apt_bookings):
            for overlap in apt_bookings[i+1:]:
                # Check if dates overlap
                if booking.start_date < overlap.end_date and booking.end_date > overlap.start_date:
                    key = tuple(sorted([booking.id, overlap.id]))
                    if key not in overlaps_found:
                        overlaps_found.add(key)
                        issues['booking_overlaps'].append({
                            'apartment': booking.apartment.name,
                            'booking1': {
                                'id': booking.id,
                                'dates': f"{booking.start_date} to {booking.end_date}",
                                'tenant': booking.tenant.full_name if booking.tenant else 'N/A',
                                'status': booking.status,
                            },
                            'booking2': {
                                'id': overlap.id,
                                'dates': f"{overlap.start_date} to {overlap.end_date}",
                                'tenant': overlap.tenant.full_name if overlap.tenant else 'N/A',
                                'status': overlap.status,
                            }
                        })
                        if len(issues['booking_overlaps']) >= 50:
                            break
            if len(issues['booking_overlaps']) >= 50:
                break
        if len(issues['booking_overlaps']) >= 50:
            break
    
    issues['summary']['critical'] += len(issues['booking_overlaps'])
    
    # 3. Payment apartment mismatch (HIGH) - with pagination
    # Only payments from the last month where payment.apartment != booking.apartment
    # We need to filter in Python since this comparison is complex
    mismatch_queryset = Payment.objects.filter(
        booking__isnull=False,
        apartment__isnull=False,
        booking__apartment__isnull=False,
        payment_date__gte=one_month_ago
    ).exclude(
        apartment=F('booking__apartment')
    ).select_related('booking', 'apartment', 'booking__apartment', 'booking__tenant').order_by('-payment_date')
    
    # Pagination for mismatch payments
    mismatch_total = mismatch_queryset.count()
    mismatch_total_pages = (mismatch_total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    mismatch_start = (mismatch_page - 1) * ITEMS_PER_PAGE
    mismatch_end = mismatch_start + ITEMS_PER_PAGE
    mismatch_payments = mismatch_queryset[mismatch_start:mismatch_end]
    
    for payment in mismatch_payments:
        issues['payment_apartment_mismatch'].append({
            'id': payment.id,
            'amount': payment.amount,
            'payment_date': payment.payment_date,
            'payment_apartment': payment.apartment.name,
            'booking_id': payment.booking.id,
            'booking_apartment': payment.booking.apartment.name if payment.booking.apartment else 'N/A',
            'tenant': payment.booking.tenant.full_name if payment.booking.tenant else 'N/A',
        })
    
    # Store pagination info
    issues['mismatch_pagination'] = {
        'current_page': mismatch_page,
        'total_pages': mismatch_total_pages,
        'total_count': mismatch_total,
        'has_prev': mismatch_page > 1,
        'has_next': mismatch_page < mismatch_total_pages,
        'prev_page': mismatch_page - 1 if mismatch_page > 1 else None,
        'next_page': mismatch_page + 1 if mismatch_page < mismatch_total_pages else None,
    }
    
    issues['summary']['high'] += mismatch_total
    
    # 4. Orphaned payments (MEDIUM) - payments without booking or apartment
    # Only payments from the last month - with pagination
    orphaned_queryset = Payment.objects.filter(
        booking__isnull=True,
        apartment__isnull=True,
        payment_date__gte=one_month_ago
    ).exclude(payment_status='Completed').select_related('payment_type').order_by('-payment_date')
    
    # Pagination for orphaned payments
    orphaned_total = orphaned_queryset.count()
    orphaned_total_pages = (orphaned_total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE  # Ceiling division
    orphaned_start = (orphaned_page - 1) * ITEMS_PER_PAGE
    orphaned_end = orphaned_start + ITEMS_PER_PAGE
    orphaned = orphaned_queryset[orphaned_start:orphaned_end]
    
    for payment in orphaned:
        issues['orphaned_payments'].append({
            'id': payment.id,
            'amount': payment.amount,
            'payment_date': payment.payment_date,
            'status': payment.payment_status,
            'type': payment.payment_type.name if payment.payment_type else 'N/A',
            'notes': (payment.notes[:100] + '...') if payment.notes and len(payment.notes) > 100 else payment.notes,
        })
    
    # Store pagination info
    issues['orphaned_pagination'] = {
        'current_page': orphaned_page,
        'total_pages': orphaned_total_pages,
        'total_count': orphaned_total,
        'has_prev': orphaned_page > 1,
        'has_next': orphaned_page < orphaned_total_pages,
        'prev_page': orphaned_page - 1 if orphaned_page > 1 else None,
        'next_page': orphaned_page + 1 if orphaned_page < orphaned_total_pages else None,
    }
    
    issues['summary']['medium'] += orphaned_total  # Use total count for summary
    
    # 4b. Zero amount payments (MEDIUM) - payments with 0 amount - with pagination
    zero_amount_queryset = Payment.objects.filter(
        amount=0,
        payment_date__gte=one_month_ago
    ).exclude(payment_status='Completed').select_related('payment_type', 'booking', 'apartment', 'booking__apartment').order_by('-payment_date')
    
    # Pagination for zero amount payments
    zero_amount_total = zero_amount_queryset.count()
    zero_amount_total_pages = (zero_amount_total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    zero_amount_start = (zero_amount_page - 1) * ITEMS_PER_PAGE
    zero_amount_end = zero_amount_start + ITEMS_PER_PAGE
    zero_amount = zero_amount_queryset[zero_amount_start:zero_amount_end]
    
    for payment in zero_amount:
        apt_name = 'N/A'
        if payment.booking and payment.booking.apartment:
            apt_name = payment.booking.apartment.name
        elif payment.apartment:
            apt_name = payment.apartment.name
            
        issues['zero_amount_payments'].append({
            'id': payment.id,
            'payment_date': payment.payment_date,
            'status': payment.payment_status,
            'type': payment.payment_type.name if payment.payment_type else 'N/A',
            'apartment': apt_name,
            'notes': (payment.notes[:100] + '...') if payment.notes and len(payment.notes) > 100 else payment.notes,
        })
    
    # Store pagination info
    issues['zero_amount_pagination'] = {
        'current_page': zero_amount_page,
        'total_pages': zero_amount_total_pages,
        'total_count': zero_amount_total,
        'has_prev': zero_amount_page > 1,
        'has_next': zero_amount_page < zero_amount_total_pages,
        'prev_page': zero_amount_page - 1 if zero_amount_page > 1 else None,
        'next_page': zero_amount_page + 1 if zero_amount_page < zero_amount_total_pages else None,
    }
    
    issues['summary']['medium'] += zero_amount_total
    
    # 5. Merged payments without key (HIGH) - with pagination
    # Only payments from the last month
    merged_queryset = Payment.objects.filter(
        payment_status='Merged',
        payment_date__gte=one_month_ago
    ).filter(
        Q(merged_payment_key__isnull=True) | Q(merged_payment_key='')
    ).select_related('booking', 'apartment', 'payment_type', 'booking__apartment').order_by('-payment_date')
    
    # Pagination for merged payments
    merged_total = merged_queryset.count()
    merged_total_pages = (merged_total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    merged_start = (merged_page - 1) * ITEMS_PER_PAGE
    merged_end = merged_start + ITEMS_PER_PAGE
    merged_no_key = merged_queryset[merged_start:merged_end]
    
    for payment in merged_no_key:
        apt_name = 'N/A'
        if payment.booking and payment.booking.apartment:
            apt_name = payment.booking.apartment.name
        elif payment.apartment:
            apt_name = payment.apartment.name
            
        issues['merged_payments_without_key'].append({
            'id': payment.id,
            'amount': payment.amount,
            'payment_date': payment.payment_date,
            'type': payment.payment_type.name if payment.payment_type else 'N/A',
            'apartment': apt_name,
        })
    
    # Store pagination info
    issues['merged_pagination'] = {
        'current_page': merged_page,
        'total_pages': merged_total_pages,
        'total_count': merged_total,
        'has_prev': merged_page > 1,
        'has_next': merged_page < merged_total_pages,
        'prev_page': merged_page - 1 if merged_page > 1 else None,
        'next_page': merged_page + 1 if merged_page < merged_total_pages else None,
    }
    
    issues['summary']['high'] += merged_total
    
    # 7. Missing phone numbers for tenants (MEDIUM)
    # Only tenants who have bookings in the last month (relevant tenants)
    # Exclude placeholder names like "Blocked" and "Not Available"
    recent_tenant_ids = Booking.objects.filter(
        end_date__gte=one_month_ago,
        tenant__isnull=False
    ).values_list('tenant_id', flat=True).distinct()
    
    tenants_no_phone = User.objects.filter(
        role='Tenant',
        id__in=recent_tenant_ids
    ).filter(
        Q(phone__isnull=True) | Q(phone='')
    ).exclude(
        full_name__iexact='Blocked'
    ).exclude(
        full_name__iexact='Not Available'
    ).exclude(
        full_name__icontains='Not Availab'  # Handle typos like "Not Availabale"
    )[:50]
    
    for user in tenants_no_phone:
        issues['missing_phones'].append({
            'id': user.id,
            'name': user.full_name,
            'email': user.email,
            'created_at': user.created_at,
        })
    
    issues['summary']['medium'] += len(issues['missing_phones'])
    
    # Calculate total
    issues['summary']['total'] = (
        issues['summary']['critical'] + 
        issues['summary']['high'] + 
        issues['summary']['medium']
    )
    
    # Get full counts (use already calculated totals)
    issues['counts'] = {
        'bookings_without_apartment': Booking.objects.filter(
            apartment__isnull=True,
            end_date__gte=one_month_ago
        ).count(),
        'orphaned_payments': orphaned_total,
        'zero_amount_payments': zero_amount_total,
        'mismatch_payments': mismatch_total,
        'merged_payments_without_key': merged_total,
        'missing_phones': User.objects.filter(
            role='Tenant',
            id__in=recent_tenant_ids
        ).filter(
            Q(phone__isnull=True) | Q(phone='')
        ).exclude(
            full_name__iexact='Blocked'
        ).exclude(
            full_name__iexact='Not Available'
        ).exclude(
            full_name__icontains='Not Availab'
        ).count(),
    }
    
    return issues


@login_required
def database_activity(request):
    """
    Display database activity logs, errors, and system logs with filtering capabilities.
    Shows last 3 days by default, grouped appropriately.
    """
    log_info("Database activity page accessed", category='system', context='database_activity view')
    
    # Get date range (default: last 3 days)
    days = int(request.GET.get('days', 3))
    start_date = timezone.now() - timedelta(days=days)
    
    # Get filter parameters
    action_filter = request.GET.get('action', '')
    model_filter = request.GET.get('model', '')
    user_filter = request.GET.get('user', '')
    search_query = request.GET.get('search', '')
    
    # Error filters
    severity_filter = request.GET.get('severity', '')
    source_filter = request.GET.get('source', '')
    error_type_filter = request.GET.get('error_type', '')
    resolved_filter = request.GET.get('resolved', '')
    
    # Log filters
    log_level_filter = request.GET.get('log_level', '')
    log_category_filter = request.GET.get('log_category', '')
    
    # === AUDIT LOGS ===
    audit_logs = AuditLog.objects.filter(timestamp__gte=start_date)
    
    if action_filter:
        audit_logs = audit_logs.filter(action=action_filter)
    if model_filter:
        audit_logs = audit_logs.filter(model_name__icontains=model_filter)
    if user_filter:
        audit_logs = audit_logs.filter(changed_by__icontains=user_filter)
    if search_query:
        audit_logs = audit_logs.filter(
            Q(object_repr__icontains=search_query) |
            Q(model_name__icontains=search_query) |
            Q(changed_by__icontains=search_query)
        )
    
    # Get statistics grouped by date and model - LIMIT to prevent OOM
    # Use aggregation for stats instead of loading all logs
    logs_by_date = {}
    MAX_LOGS_DISPLAY = 500  # Limit to prevent memory issues on low-memory servers
    
    for log in audit_logs.order_by('-timestamp')[:MAX_LOGS_DISPLAY]:
        date_key = log.timestamp.date()
        if date_key not in logs_by_date:
            logs_by_date[date_key] = {}
        
        model_name = log.model_name
        if model_name not in logs_by_date[date_key]:
            logs_by_date[date_key][model_name] = {
                'creates': 0,
                'updates': 0,
                'deletes': 0,
                'logs': []
            }
        
        logs_by_date[date_key][model_name][f"{log.action}s"] += 1
        # Only store first 20 logs per model per day for display
        if len(logs_by_date[date_key][model_name]['logs']) < 20:
            logs_by_date[date_key][model_name]['logs'].append(log)
    
    # Summary statistics for audit logs
    total_audit_logs = audit_logs.count()
    creates_count = audit_logs.filter(action='create').count()
    updates_count = audit_logs.filter(action='update').count()
    deletes_count = audit_logs.filter(action='delete').count()
    
    # === ERROR LOGS ===
    error_logs = ErrorLog.objects.filter(timestamp__gte=start_date)
    
    if severity_filter:
        error_logs = error_logs.filter(severity=severity_filter)
    if source_filter:
        error_logs = error_logs.filter(source=source_filter)
    if error_type_filter:
        error_logs = error_logs.filter(error_type__icontains=error_type_filter)
    if resolved_filter:
        error_logs = error_logs.filter(resolved=(resolved_filter == 'true'))
    if user_filter:
        error_logs = error_logs.filter(username__icontains=user_filter)
    if search_query:
        error_logs = error_logs.filter(
            Q(error_message__icontains=search_query) |
            Q(context__icontains=search_query) |
            Q(error_type__icontains=search_query)
        )
    
    # Error statistics
    total_errors = error_logs.count()
    unresolved_errors = error_logs.filter(resolved=False).count()
    critical_errors = error_logs.filter(severity='critical').count()
    high_errors = error_logs.filter(severity='high').count()
    
    # Group errors by date
    errors_by_date = {}
    for error in error_logs.order_by('-timestamp')[:100]:  # Limit to 100 recent errors
        date_key = error.timestamp.date()
        if date_key not in errors_by_date:
            errors_by_date[date_key] = []
        errors_by_date[date_key].append(error)
    
    # === SYSTEM LOGS ===
    system_logs = SystemLog.objects.filter(timestamp__gte=start_date)
    
    if log_level_filter:
        system_logs = system_logs.filter(level=log_level_filter)
    if log_category_filter:
        system_logs = system_logs.filter(category=log_category_filter)
    if user_filter:
        system_logs = system_logs.filter(username__icontains=user_filter)
    if search_query:
        system_logs = system_logs.filter(
            Q(message__icontains=search_query) |
            Q(context__icontains=search_query)
        )
    
    # System log statistics
    total_system_logs = system_logs.count()
    info_logs = system_logs.filter(level='info').count()
    warning_logs = system_logs.filter(level='warning').count()
    error_level_logs = system_logs.filter(level='error').count()
    
    # Get unique values for filters
    unique_models = AuditLog.objects.filter(timestamp__gte=start_date).values_list('model_name', flat=True).distinct().order_by('model_name')
    unique_users = AuditLog.objects.filter(timestamp__gte=start_date).values_list('changed_by', flat=True).distinct().order_by('changed_by')
    unique_error_types = ErrorLog.objects.filter(timestamp__gte=start_date).values_list('error_type', flat=True).distinct().order_by('error_type')
    
    # === DATA INTEGRITY CHECKS ===
    orphaned_page = int(request.GET.get('orphaned_page', 1))
    zero_amount_page = int(request.GET.get('zero_amount_page', 1))
    mismatch_page = int(request.GET.get('mismatch_page', 1))
    merged_page = int(request.GET.get('merged_page', 1))
    data_integrity = get_data_integrity_issues(
        orphaned_page=orphaned_page,
        zero_amount_page=zero_amount_page,
        mismatch_page=mismatch_page,
        merged_page=merged_page
    )
    
    # Pagination - use a limited queryset to prevent memory issues
    page = request.GET.get('page', 1)
    # Limit the total entries for pagination to prevent OOM
    paginator = Paginator(audit_logs.order_by('-timestamp')[:2000], 50)
    page_obj = paginator.get_page(page)
    
    context = {
        'title': 'Administration Dashboard',
        
        # Audit Log Data
        'logs_by_date': dict(sorted(logs_by_date.items(), reverse=True)),
        'page_obj': page_obj,
        'total_logs': total_audit_logs,
        'logs_display_limit': MAX_LOGS_DISPLAY,
        'creates_count': creates_count,
        'updates_count': updates_count,
        'deletes_count': deletes_count,
        
        # Error Log Data
        'errors_by_date': dict(sorted(errors_by_date.items(), reverse=True)),
        'total_errors': total_errors,
        'unresolved_errors': unresolved_errors,
        'critical_errors': critical_errors,
        'high_errors': high_errors,
        'recent_errors': error_logs.order_by('-timestamp')[:20],  # 20 most recent
        
        # System Log Data
        'total_system_logs': total_system_logs,
        'info_logs': info_logs,
        'warning_logs': warning_logs,
        'error_level_logs': error_level_logs,
        'recent_system_logs': system_logs.order_by('-timestamp')[:50],  # 50 most recent
        
        # Data Integrity
        'data_integrity': data_integrity,
        
        # Filter options
        'unique_models': unique_models,
        'unique_users': unique_users,
        'unique_error_types': unique_error_types,
        
        # Current filter values
        'days': days,
        'action_filter': action_filter,
        'model_filter': model_filter,
        'user_filter': user_filter,
        'search_query': search_query,
        'severity_filter': severity_filter,
        'source_filter': source_filter,
        'error_type_filter': error_type_filter,
        'resolved_filter': resolved_filter,
        'log_level_filter': log_level_filter,
        'log_category_filter': log_category_filter,
        'start_date': start_date,
    }
    
    return render(request, 'database_activity.html', context)

