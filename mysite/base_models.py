"""
Base model classes with built-in error handling, Telegram logging, and user tracking
"""
from django.db import models
from django.core.exceptions import ValidationError
from mysite.telegram_logger import log_error
from mysite.request_context import get_current_user_display
import traceback
import logging

logger = logging.getLogger(__name__)


class BaseModelWithTracking(models.Model):
    """
    Abstract base model that provides:
    - Automatic user tracking (created_by, last_updated_by)
    - Automatic timestamps (created_at, updated_at)
    - Error handling and Telegram logging
    
    All models should inherit from this class for consistent tracking and error reporting.
    """
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True
    
    def save(self, *args, **kwargs):
        """
        Override save to add user tracking, error handling and Telegram logging
        """
        model_name = self.__class__.__name__
        is_creating = self.pk is None
        action = "Creating" if is_creating else "Updating"
        
        # Extract updated_by from kwargs if provided (for backwards compatibility)
        updated_by = kwargs.pop('updated_by', None)
        
        # Track who is making this change
        current_user = self._get_user_for_tracking(updated_by)
        
        if is_creating:
            # Set created_by on creation
            if not self.created_by:
                self.created_by = current_user
        
        # Always update last_updated_by
        self.last_updated_by = current_user
        
        try:
            # Call the actual save logic
            self._do_save(*args, **kwargs)
            
        except ValidationError as e:
            # Handle validation errors specifically
            self._handle_validation_error(e, model_name, action)
            raise  # Re-raise after logging
            
        except Exception as e:
            # Handle any other unexpected errors
            self._handle_unexpected_error(e, model_name, action)
            raise  # Re-raise after logging
    
    def _get_user_for_tracking(self, explicit_user=None):
        """
        Get the user to use for tracking purposes.
        
        Args:
            explicit_user: User explicitly passed to save() (for backwards compatibility)
        
        Returns:
            String representation of the user
        """
        if explicit_user:
            # Explicit user was provided (backwards compatibility)
            if hasattr(explicit_user, 'full_name'):
                return explicit_user.full_name
            elif isinstance(explicit_user, str):
                return explicit_user
        
        # Use the current user from request context
        return get_current_user_display()
    
    def _do_save(self, *args, **kwargs):
        """
        The actual save logic. Override this in your model if you need custom save behavior.
        If you override save() directly, the error handling will still work.
        """
        super().save(*args, **kwargs)
    
    def _handle_validation_error(self, error, model_name, action):
        """
        Handle and log validation errors to Telegram
        """
        error_details = self._get_model_details()
        error_details['Action'] = action
        error_details['Validation Errors'] = self._format_validation_error(error)
        
        context = f"{model_name}.save() - Validation Error"
        log_error(error, context=context, additional_info=error_details)
        
        logger.error(f"Validation error in {model_name}.save(): {error}", exc_info=True)
    
    def _handle_unexpected_error(self, error, model_name, action):
        """
        Handle and log unexpected errors to Telegram
        """
        error_details = self._get_model_details()
        error_details['Action'] = action
        error_details['Error Type'] = type(error).__name__
        
        # Add call stack info
        stack = traceback.extract_stack()
        if len(stack) > 3:
            caller_info = []
            for frame in stack[-4:-1]:
                path = frame.filename.split('/')[-2:] if '/' in frame.filename else [frame.filename]
                caller_info.append(f"{'/'.join(path)}:{frame.lineno} in {frame.name}")
            error_details['Call Stack'] = ' → '.join(caller_info)
        
        context = f"{model_name}.save() - Unexpected Error"
        log_error(error, context=context, additional_info=error_details)
        
        logger.error(f"Unexpected error in {model_name}.save(): {error}", exc_info=True)
    
    def _get_model_details(self):
        """
        Get relevant details about the model instance for error reporting.
        Override this in your models to add model-specific details.
        """
        details = {
            'Model': self.__class__.__name__,
            'ID': str(self.pk) if self.pk else 'New Instance',
        }
        
        # Add common fields if they exist
        if hasattr(self, 'created_at'):
            details['Created At'] = str(self.created_at)
        if hasattr(self, 'updated_at'):
            details['Updated At'] = str(self.updated_at)
        
        return details
    
    def _format_validation_error(self, error):
        """
        Format ValidationError for readable display
        """
        if hasattr(error, 'message_dict'):
            # Multiple field errors
            errors = []
            for field, messages in error.message_dict.items():
                for message in messages:
                    errors.append(f"{field}: {message}")
            return '; '.join(errors)
        elif hasattr(error, 'messages'):
            # List of error messages
            return '; '.join(error.messages)
        else:
            # Single error message
            return str(error)


class PaymentBaseModel(BaseModelWithTracking):
    """
    Base model for Payment with specific error details
    """
    
    class Meta:
        abstract = True
    
    def _get_model_details(self):
        """Add Payment-specific details to error reports"""
        details = super()._get_model_details()
        
        if hasattr(self, 'payment_date'):
            details['Payment Date'] = str(self.payment_date) if self.payment_date else 'Not set'
        if hasattr(self, 'amount'):
            details['Amount'] = str(self.amount) if self.amount else 'Not set'
        if hasattr(self, 'payment_type'):
            details['Payment Type'] = str(self.payment_type) if self.payment_type else 'Not set'
        if hasattr(self, 'payment_status'):
            details['Status'] = str(self.payment_status) if self.payment_status else 'Not set'
        if hasattr(self, 'booking') and self.booking:
            details['Booking ID'] = str(self.booking.id)
        if hasattr(self, 'apartment') and self.apartment:
            details['Apartment'] = str(self.apartment.name)
        
        return details


class BookingBaseModel(BaseModelWithTracking):
    """
    Base model for Booking with specific error details
    """
    
    class Meta:
        abstract = True
    
    def _get_model_details(self):
        """Add Booking-specific details to error reports"""
        details = super()._get_model_details()
        
        if hasattr(self, 'start_date'):
            details['Start Date'] = str(self.start_date) if self.start_date else 'Not set'
        if hasattr(self, 'end_date'):
            details['End Date'] = str(self.end_date) if self.end_date else 'Not set'
        if hasattr(self, 'status'):
            details['Status'] = str(self.status) if self.status else 'Not set'
        if hasattr(self, 'apartment') and self.apartment:
            details['Apartment'] = str(self.apartment.name)
        if hasattr(self, 'tenant') and self.tenant:
            details['Tenant'] = str(self.tenant.full_name)
        
        return details


class CleaningBaseModel(BaseModelWithTracking):
    """
    Base model for Cleaning with specific error details
    """
    
    class Meta:
        abstract = True
    
    def _get_model_details(self):
        """Add Cleaning-specific details to error reports"""
        details = super()._get_model_details()
        
        if hasattr(self, 'date'):
            details['Date'] = str(self.date) if self.date else 'Not set'
        if hasattr(self, 'status'):
            details['Status'] = str(self.status) if self.status else 'Not set'
        if hasattr(self, 'cleaner') and self.cleaner:
            details['Cleaner'] = str(self.cleaner.full_name)
        if hasattr(self, 'booking') and self.booking:
            details['Booking ID'] = str(self.booking.id)
        if hasattr(self, 'apartment') and self.apartment:
            details['Apartment'] = str(self.apartment.name)
        
        return details


# Keep the old name for backwards compatibility
BaseModelWithErrorHandling = BaseModelWithTracking

