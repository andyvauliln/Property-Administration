"""
Request context utilities for tracking who is making model changes.

This module provides a thread-local storage mechanism to track the current user
making requests throughout the application, making it easy to audit model changes.
"""

import threading
from typing import Optional
from django.contrib.auth import get_user_model

# Thread-local storage for the current user
_thread_locals = threading.local()


def set_current_user(user):
    """
    Set the current user in thread-local storage.
    This should be called by middleware for each request.
    
    Args:
        user: Django User instance or None
    """
    _thread_locals.user = user


def get_current_user():
    """
    Get the current user from thread-local storage.
    
    Returns:
        User instance if set, None otherwise
    """
    return getattr(_thread_locals, 'user', None)


def get_current_user_display():
    """
    Get a display-friendly representation of the current user.
    
    Returns:
        String representation of the user (full_name, email, or 'System')
    """
    user = get_current_user()
    if user is None:
        return 'System'
    
    # Try to get full_name first
    if hasattr(user, 'full_name') and user.full_name:
        return user.full_name
    
    # Fall back to email
    if hasattr(user, 'email') and user.email:
        return user.email
    
    # Fall back to username
    if hasattr(user, 'username') and user.username:
        return user.username
    
    # Last resort
    return f'User #{user.pk}'


def clear_current_user():
    """
    Clear the current user from thread-local storage.
    Useful for cleanup after request processing.
    """
    if hasattr(_thread_locals, 'user'):
        delattr(_thread_locals, 'user')


def apply_user_tracking(instance, updated_by=None):
    """
    Apply user tracking to a model instance before saving.
    Sets created_by (on creation) and last_updated_by (always).
    
    Args:
        instance: The model instance to track
        updated_by: Optional explicit user (for backwards compatibility)
    
    Usage in model save() method:
        def save(self, *args, **kwargs):
            updated_by = kwargs.pop('updated_by', None)
            apply_user_tracking(self, updated_by)
            super().save(*args, **kwargs)
    """
    # Determine the current user
    if updated_by:
        current_user = updated_by.full_name if hasattr(updated_by, 'full_name') else str(updated_by)
    else:
        current_user = get_current_user_display()
    
    # Set created_by on creation
    is_creating = instance.pk is None
    if is_creating and hasattr(instance, 'created_by') and not instance.created_by:
        instance.created_by = current_user
    
    # Always update last_updated_by
    if hasattr(instance, 'last_updated_by'):
        instance.last_updated_by = current_user

