"""
Unified Logging System
=====================
Single source of truth for all logging in the application.

Replaces:
- mysite/error_logger.py
- mysite/telegram_logger.py  
- All separate loggers throughout the codebase

Features:
- Logs errors AND general events
- Stores in database (ErrorLog and SystemLog models)
- Sends Telegram notifications for errors
- Automatic context capture (user, request, etc.)
- Proper severity levels
- Clean, simple API

Usage:
    from mysite.unified_logger import log_error, log_info, log_warning, logger
    
    # For errors
    try:
        operation()
    except Exception as e:
        log_error(e, "Context description", severity='high', source='web')
    
    # For general logging
    log_info("Payment processed", category='payment', details={'amount': 100})
    log_warning("Low balance", category='system')
"""
import logging
import traceback
import hashlib
import os
from datetime import datetime
from typing import Optional, Dict, Any
from django.utils import timezone

# Standard Python logger
logger = logging.getLogger('mysite.common')


def _get_user_context():
    """Get current user context from request context"""
    try:
        from mysite.request_context import get_current_user
        user = get_current_user()
        if user:
            return {
                'user_id': str(user.id) if hasattr(user, 'id') else None,
                'username': str(user.username) if hasattr(user, 'username') else str(user),
                'user_role': str(user.role) if hasattr(user, 'role') else None,
                'user_email': str(user.email) if hasattr(user, 'email') else None,
            }
    except Exception:
        pass
    return {'user_id': None, 'username': None, 'user_role': None, 'user_email': None}


def _get_request_context():
    """Get current request context"""
    try:
        from mysite.request_context import _request_context
        request = getattr(_request_context, 'request', None)
        if request:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ip = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR')
            
            return {
                'request_method': request.method,
                'request_path': request.path,
                'request_ip': ip,
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:500],
            }
    except Exception:
        pass
    return {'request_method': None, 'request_path': None, 'request_ip': None, 'user_agent': None}


def _generate_error_hash(error_type: str, context: str, message: str) -> str:
    """Generate hash for grouping similar errors"""
    # Create hash from error type, context, and first 100 chars of message
    hash_string = f"{error_type}:{context}:{message[:100]}"
    return hashlib.sha256(hash_string.encode()).hexdigest()


def _send_telegram(message: str) -> bool:
    """Send message to Telegram"""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ERROR_CHAT_ID", "288566859")
    
    if not token:
        return False
    
    try:
        import requests
        if len(message) > 4000:
            message = message[:3900] + "\n\n... (truncated)"
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram: {e}")
        return False


def _format_telegram_error(error_log) -> str:
    """Format error log for Telegram"""
    msg = f"🚨 <b>ERROR ALERT</b> 🚨\n\n"
    msg += f"⏰ <b>Time:</b> {error_log.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
    msg += f"🔥 <b>Severity:</b> {error_log.severity.upper()}\n"
    msg += f"📍 <b>Context:</b> {error_log.context}\n"
    msg += f"❌ <b>Error:</b> {error_log.error_type}\n"
    msg += f"💬 <b>Message:</b> {error_log.error_message[:200]}\n"
    
    if error_log.username:
        msg += f"\n👤 <b>User:</b> {error_log.username}"
        if error_log.user_role:
            msg += f" ({error_log.user_role})"
        msg += "\n"
    
    if error_log.request_path:
        msg += f"🌐 <b>Request:</b> {error_log.request_method} {error_log.request_path}\n"
    
    if error_log.traceback:
        msg += f"\n📋 <b>Traceback:</b>\n<pre>{error_log.traceback[:800]}</pre>"
    
    return msg


def log_error(
    error: Exception,
    context: str,
    severity: str = 'medium',
    source: str = 'other',
    additional_info: Optional[Dict[str, Any]] = None,
    send_telegram: bool = True,
    re_raise: bool = False
) -> Optional[int]:
    """
    Log an error to database, Telegram, and standard logger
    
    Args:
        error: The exception that occurred
        context: Description of where/what happened (e.g., "Payment Processing - Charge Card")
        severity: 'low', 'medium', 'high', 'critical'
        source: 'web', 'api', 'command', 'model', 'task', 'webhook', 'other'
        additional_info: Extra context as dictionary
        send_telegram: Whether to send Telegram notification (default: True)
        re_raise: Whether to re-raise the exception after logging
    
    Returns:
        ErrorLog ID if saved, None otherwise
    """
    try:
        # Avoid circular import
        from mysite.models import ErrorLog
        
        error_type = type(error).__name__
        error_message = str(error)
        tb = traceback.format_exc()
        
        # Get context
        user_ctx = _get_user_context()
        request_ctx = _get_request_context()
        
        # Generate error hash for grouping
        error_hash = _generate_error_hash(error_type, context, error_message)
        
        # Check if similar error exists recently (last hour)
        from django.utils import timezone as tz
        from datetime import timedelta
        recent_time = tz.now() - timedelta(hours=1)
        
        existing_error = ErrorLog.objects.filter(
            error_hash=error_hash,
            last_occurrence__gte=recent_time
        ).first()
        
        if existing_error:
            # Update existing error
            existing_error.occurrences += 1
            existing_error.last_occurrence = tz.now()
            existing_error.save(update_fields=['occurrences', 'last_occurrence'])
            error_log_id = existing_error.id
            # Don't send telegram for duplicates
            telegram_sent = False
        else:
            # Create new error log
            error_log = ErrorLog.objects.create(
                error_type=error_type,
                error_message=error_message,
                context=context,
                source=source,
                severity=severity,
                traceback=tb if tb != "NoneType: None\n" else None,
                additional_info=additional_info,
                error_hash=error_hash,
                **user_ctx,
                **request_ctx
            )
            error_log_id = error_log.id
            
            # Send Telegram notification
            telegram_sent = False
            if send_telegram and severity in ['high', 'critical']:
                telegram_message = _format_telegram_error(error_log)
                telegram_sent = _send_telegram(telegram_message)
                
                if telegram_sent:
                    error_log.telegram_sent = True
                    error_log.telegram_sent_at = tz.now()
                    error_log.save(update_fields=['telegram_sent', 'telegram_sent_at'])
        
        # Also log to standard logger
        logger.error(
            f"[{severity.upper()}] {context}: {error_message}",
            exc_info=True,
            extra={'error_log_id': error_log_id}
        )
        
        if re_raise:
            raise
        
        return error_log_id
        
    except Exception as log_err:
        # Fallback - log to standard logger if database fails
        logger.error(f"Failed to log error to database: {log_err}", exc_info=True)
        logger.error(f"Original error: {context}: {error}", exc_info=True)
        return None


def log_info(
    message: str,
    category: str = 'other',
    context: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """
    Log an informational message
    
    Args:
        message: The log message
        category: 'auth', 'sms', 'payment', 'booking', 'contract', 'notification', 'sync', 'cleanup', 'system', 'other'
        context: Optional context/location
        details: Additional structured data
    
    Returns:
        SystemLog ID if saved, None otherwise
    """
    return _log_system('info', message, category, context, details)


def log_warning(
    message: str,
    category: str = 'other',
    context: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Log a warning message"""
    return _log_system('warning', message, category, context, details)


def log_debug(
    message: str,
    category: str = 'other',
    context: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Log a debug message"""
    return _log_system('debug', message, category, context, details)


def _log_system(
    level: str,
    message: str,
    category: str,
    context: Optional[str],
    details: Optional[Dict[str, Any]]
) -> Optional[int]:
    """Internal function to log to SystemLog"""
    try:
        from mysite.models import SystemLog
        
        user_ctx = _get_user_context()
        
        system_log = SystemLog.objects.create(
            level=level,
            category=category,
            message=message,
            context=context,
            details=details,
            user_id=user_ctx['user_id'],
            username=user_ctx['username']
        )
        
        # Also log to standard logger
        log_func = getattr(logger, level, logger.info)
        log_func(f"[{category}] {message}", extra={'system_log_id': system_log.id})
        
        return system_log.id
        
    except Exception as e:
        logger.error(f"Failed to log to database: {e}", exc_info=True)
        return None


# Convenience function for backward compatibility
def log_exception(error: Exception, context: str, additional_info: Optional[Dict] = None):
    """Backward compatible with old error_logger"""
    return log_error(error, context, additional_info=additional_info)

