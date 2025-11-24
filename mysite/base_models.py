"""
Base model classes with built-in error handling and Telegram logging
"""
from django.db import models
from django.core.exceptions import ValidationError
from mysite.telegram_logger import log_error
import traceback
import logging

logger = logging.getLogger(__name__)


class BaseModelWithErrorHandling(models.Model):
    """
    Abstract base model that provides automatic error handling and Telegram logging
    for all save operations and validation errors.
    
    All your models should inherit from this class to get automatic error reporting.
    """
    
    class Meta:
        abstract = True
    
    def save(self, *args, **kwargs):
        """
        Override save to wrap it with error handling and Telegram logging
        """
        model_name = self.__class__.__name__
        is_creating = self.pk is None
        action = "Creating" if is_creating else "Updating"
        
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


class PaymentBaseModel(BaseModelWithErrorHandling):
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


class BookingBaseModel(BaseModelWithErrorHandling):
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


class CleaningBaseModel(BaseModelWithErrorHandling):
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

