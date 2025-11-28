"""
Database Activity Monitoring View
Provides a comprehensive interface to monitor all database changes, errors, and system logs.
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.utils import timezone
from datetime import datetime, timedelta
from mysite.models import AuditLog, ErrorLog, SystemLog
from mysite.unified_logger import log_info
import json


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
    
    # Get statistics grouped by date and model
    logs_by_date = {}
    for log in audit_logs.order_by('-timestamp'):
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
    
    # Pagination
    page = request.GET.get('page', 1)
    paginator = Paginator(audit_logs.order_by('-timestamp'), 50)
    page_obj = paginator.get_page(page)
    
    context = {
        'title': 'Administration Dashboard',
        
        # Audit Log Data
        'logs_by_date': dict(sorted(logs_by_date.items(), reverse=True)),
        'page_obj': page_obj,
        'total_logs': total_audit_logs,
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

