"""
Base Command with Error Handling
Provides a base class for all management commands with automatic error logging to Telegram
"""
from django.core.management.base import BaseCommand
from mysite.telegram_logger import log_error
import sys
import traceback


class BaseCommandWithErrorHandling(BaseCommand):
    """
    Base command class that automatically catches and logs errors to Telegram
    
    Usage:
        class Command(BaseCommandWithErrorHandling):
            help = 'Your command description'
            
            def execute_command(self, *args, **options):
                # Your command logic here
                pass
    """
    
    def handle(self, *args, **options):
        """
        Wrapper around the actual command execution that catches errors
        """
        command_name = self.__module__.split('.')[-1]
        
        try:
            # Call the actual command implementation
            self.stdout.write(f'Starting command: {command_name}')
            result = self.execute_command(*args, **options)
            self.stdout.write(self.style.SUCCESS(f'Successfully completed {command_name}'))
            return result
            
        except Exception as e:
            # Log the error
            self.stdout.write(self.style.ERROR(f'Error in {command_name}: {str(e)}'))
            
            # Send to Telegram
            log_error(
                error=e,
                context=f"Django Management Command: {command_name}",
                additional_info={
                    'Command': command_name,
                    'Arguments': str(args) if args else 'None',
                    'Options': str(options) if options else 'None'
                }
            )
            
            # Re-raise the exception so the command exits with error code
            raise
    
    def execute_command(self, *args, **options):
        """
        Override this method in your command class instead of handle()
        This is where your actual command logic should go
        """
        raise NotImplementedError(
            f'{self.__class__.__name__} must implement execute_command() method'
        )



