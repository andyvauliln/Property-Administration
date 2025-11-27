"""
Universal Error Logger - Use this in ALL exception handlers

This module provides a standardized way to log errors throughout the application.
It ensures that all errors (both handled and unhandled) are sent to Telegram
and logged locally.

Usage:
    from mysite.error_logger import log_exception
    
    try:
        risky_operation()
    except Exception as e:
        log_exception(e, "View Name - Operation", {'key': 'value'})
        return error_response
"""
from mysite.telegram_logger import log_error
from mysite.request_context import get_current_user
import logging

logger = logging.getLogger(__name__)


def log_exception(
    error: Exception,
    context: str,
    additional_info: dict = None,
    re_raise: bool = False,
    include_user: bool = True
):
    """
    Universal error logger - use this in ALL exception handlers
    
    This function:
    1. Sends error to Telegram (if TELEGRAM_TOKEN is set)
    2. Logs to Django logger
    3. Automatically includes current user context
    4. Optionally re-raises the exception
    
    Args:
        error: The exception that occurred
        context: Where it happened (e.g., "Chat View - Send Message")
        additional_info: Dictionary of extra context to include
        re_raise: Whether to re-raise the exception after logging (default: False)
        include_user: Whether to automatically include current user info (default: True)
    
    Example:
        try:
            result = dangerous_operation()
        except Exception as e:
            log_exception(
                error=e,
                context="Payment Processing - Charge Card",
                additional_info={
                    'payment_id': payment.id,
                    'amount': payment.amount,
                }
            )
            return JsonResponse({'error': str(e)}, status=500)
    """
    # Prepare additional info
    if additional_info is None:
        additional_info = {}
    
    # Auto-include current user if requested
    if include_user:
        try:
            user = get_current_user()
            if user:
                additional_info['User'] = str(user.username) if hasattr(user, 'username') else str(user)
                additional_info['User ID'] = str(user.id) if hasattr(user, 'id') else 'N/A'
                if hasattr(user, 'full_name') and user.full_name:
                    additional_info['Full Name'] = user.full_name
                if hasattr(user, 'role') and user.role:
                    additional_info['Role'] = user.role
        except Exception:
            # Don't fail if we can't get user info
            pass
    
    # Send to Telegram (will be disabled if TELEGRAM_TOKEN not set)
    log_error(error, context, additional_info)
    
    # Also log locally
    logger.error(f"Error in {context}: {str(error)}", exc_info=True, extra=additional_info)
    
    # Re-raise if requested
    if re_raise:
        raise


def log_exception_simple(error: Exception, context: str):
    """
    Simplified version for quick logging without extra info
    
    Usage:
        except Exception as e:
            log_exception_simple(e, "Function Name")
    """
    log_exception(error, context, None, False, True)


def log_and_reraise(error: Exception, context: str, additional_info: dict = None):
    """
    Log the error and then re-raise it
    
    Useful when you want to log but still let the error propagate
    
    Usage:
        except Exception as e:
            log_and_reraise(e, "Critical Operation", {'data': 'value'})
    """
    log_exception(error, context, additional_info, re_raise=True, include_user=True)

