"""
Test Error Logging
This command intentionally raises an error to test the Telegram error notification system
"""
from mysite.management.commands.base_command import BaseCommandWithErrorHandling
from mysite.telegram_logger import log_critical


class Command(BaseCommandWithErrorHandling):
    help = 'Test the error logging system by intentionally raising an error'

    def add_arguments(self, parser):
        parser.add_argument(
            '--test-type',
            type=str,
            default='exception',
            choices=['exception', 'critical'],
            help='Type of test to run (exception or critical)'
        )

    def execute_command(self, *args, **options):
        test_type = options.get('test_type', 'exception')
        
        if test_type == 'exception':
            self.stdout.write('Testing exception error logging...')
            self.stdout.write('This will raise a ValueError and send it to Telegram')
            
            # This will be caught by the base class and sent to Telegram
            raise ValueError('This is a test error from test_error_logging command. If you see this in Telegram, the error logging system is working!')
            
        elif test_type == 'critical':
            self.stdout.write('Testing critical message logging...')
            
            # Send a critical message directly
            success = log_critical(
                message='This is a test critical alert. If you see this in Telegram, the critical logging system is working!',
                context='Test Error Logging Command',
                additional_info={
                    'test_type': 'critical',
                    'purpose': 'System verification'
                }
            )
            
            if success:
                self.stdout.write(self.style.SUCCESS('Critical message sent successfully!'))
            else:
                self.stdout.write(self.style.WARNING('Critical message may not have been sent (check Telegram token)'))




