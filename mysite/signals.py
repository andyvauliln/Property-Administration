"""
Django signals to automatically track database changes for audit logging.
This captures creates, updates, and deletes for all models.
"""
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from mysite.request_context import get_current_user_display
import json
import logging

logger = logging.getLogger(__name__)

# Models to exclude from audit logging
EXCLUDED_MODELS = ['auditlog', 'session', 'contenttype', 'permission', 'logentry']

def serialize_value(value):
    """Convert a value to a JSON-serializable format"""
    if value is None:
        return None
    
    # Handle datetime/date objects
    from datetime import datetime, date, time
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    
    # Handle Decimal
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    
    # Handle Django models (foreign keys)
    from django.db import models
    if isinstance(value, models.Model):
        return str(value)
    
    # Handle querysets
    if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
        try:
            return [serialize_value(item) for item in value]
        except:
            return str(value)
    
    return str(value)


def get_current_user_info():
    """Get current user name and role"""
    from mysite.request_context import get_current_user
    user = get_current_user()
    
    if user and hasattr(user, 'full_name') and hasattr(user, 'role'):
        return f"{user.full_name} ({user.role})"
    elif user and hasattr(user, 'full_name'):
        return user.full_name
    elif user and hasattr(user, 'email'):
        return user.email
    
    return 'System'


def get_model_fields(instance):
    """Get all field values for a model instance"""
    fields_data = {}
    
    for field in instance._meta.fields:
        field_name = field.name
        try:
            value = getattr(instance, field_name)
            fields_data[field_name] = serialize_value(value)
        except Exception as e:
            fields_data[field_name] = f"Error: {str(e)}"
    
    return fields_data


def should_track_model(instance):
    """Determine if we should track changes for this model"""
    model_name = instance.__class__.__name__.lower()
    
    # Don't track AuditLog itself (avoid recursion)
    if model_name in EXCLUDED_MODELS:
        return False
    
    return True


# Store the old state of objects before they're saved (for tracking updates)
_pre_save_instances = {}

@receiver(pre_save)
def capture_pre_save_state(sender, instance, **kwargs):
    """Capture the state of an object before it's saved"""
    if not should_track_model(instance):
        return
    
    # If updating (not creating), capture the old state
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            _pre_save_instances[f"{sender.__name__}_{instance.pk}"] = get_model_fields(old_instance)
        except sender.DoesNotExist:
            pass


@receiver(post_save)
def log_create_update(sender, instance, created, **kwargs):
    """Log object creation and updates"""
    if not should_track_model(instance):
        return
    
    try:
        from mysite.models import AuditLog
        
        model_name = sender.__name__
        object_id = str(instance.pk)
        changed_by = get_current_user_info()
        
        if created:
            # Object was created
            new_values = get_model_fields(instance)
            
            AuditLog.objects.create(
                model_name=model_name,
                object_id=object_id,
                object_repr=str(instance),
                action='create',
                changed_by=changed_by,
                new_values=new_values,
                changed_fields=list(new_values.keys())
            )
        else:
            # Object was updated
            key = f"{model_name}_{object_id}"
            old_values = _pre_save_instances.get(key, {})
            new_values = get_model_fields(instance)
            
            # Find what changed
            changed_fields = []
            for field_name in new_values.keys():
                old_val = old_values.get(field_name)
                new_val = new_values.get(field_name)
                if old_val != new_val:
                    changed_fields.append(field_name)
            
            # Only log if something actually changed
            if changed_fields:
                AuditLog.objects.create(
                    model_name=model_name,
                    object_id=object_id,
                    object_repr=str(instance),
                    action='update',
                    changed_by=changed_by,
                    old_values={k: old_values.get(k) for k in changed_fields},
                    new_values={k: new_values.get(k) for k in changed_fields},
                    changed_fields=changed_fields
                )
            
            # Clean up the stored state
            if key in _pre_save_instances:
                del _pre_save_instances[key]
    
    except Exception as e:
        logger.error(f"Error logging {sender.__name__} save: {e}")


@receiver(pre_delete)
def log_delete(sender, instance, **kwargs):
    """Log object deletion"""
    if not should_track_model(instance):
        return
    
    try:
        from mysite.models import AuditLog
        
        model_name = sender.__name__
        object_id = str(instance.pk)
        changed_by = get_current_user_info()
        old_values = get_model_fields(instance)
        
        AuditLog.objects.create(
            model_name=model_name,
            object_id=object_id,
            object_repr=str(instance),
            action='delete',
            changed_by=changed_by,
            old_values=old_values,
            changed_fields=list(old_values.keys())
        )
    
    except Exception as e:
        logger.error(f"Error logging {sender.__name__} delete: {e}")

