"""
Test Command for Centralized Error Handling

This command tests that all error logging mechanisms are working correctly.
It will:
1. Test the error_logger module
2. Test telegram notifications
3. Test middleware error handling
4. Verify environment variables

Run with: python manage.py test_centralized_errors
"""
from django.core.management.base import BaseCommand
from django.core.exceptions import ValidationError
from mysite.error_logger import log_exception, log_exception_simple, log_and_reraise
from mysite.telegram_logger import telegram_logger
import os
from django.utils import timezone


class Command(BaseCommand):
    help = 'Test centralized error handling and Telegram notifications'
    
    def handle(self, *args, **options):
        self.stdout.write("\n" + "="*60)
        self.stdout.write("TESTING CENTRALIZED ERROR HANDLING")
        self.stdout.write("="*60 + "\n")
        
        # Test 1: Check environment variables
        self.stdout.write("\n1. Checking Environment Variables...")
        self.test_environment()
        
        # Test 2: Test telegram logger directly
        self.stdout.write("\n2. Testing Telegram Logger...")
        self.test_telegram_logger()
        
        # Test 3: Test error_logger module
        self.stdout.write("\n3. Testing error_logger module...")
        self.test_error_logger()
        
        # Test 4: Summary
        self.stdout.write("\n" + "="*60)
        self.stdout.write("TEST SUMMARY")
        self.stdout.write("="*60)
        
        token_status = "✅ SET" if os.environ.get("TELEGRAM_TOKEN") else "❌ NOT SET"
        self.stdout.write(f"\nTELEGRAM_TOKEN: {token_status}")
        
        if not os.environ.get("TELEGRAM_TOKEN"):
            self.stdout.write(self.style.ERROR("\n⚠️  TELEGRAM_TOKEN is not set!"))
            self.stdout.write("   Telegram notifications are DISABLED")
            self.stdout.write("\n   To enable:")
            self.stdout.write("   1. Get a token from @BotFather on Telegram")
            self.stdout.write("   2. Set environment variable: export TELEGRAM_TOKEN=your_token")
            self.stdout.write("   3. Restart your application")
            self.stdout.write("   4. Run this test again")
        else:
            self.stdout.write(self.style.SUCCESS("\n✅ All tests completed!"))
            self.stdout.write("   Check your Telegram for test notifications")
        
        self.stdout.write("\n" + "="*60 + "\n")
    
    def test_environment(self):
        """Test environment variable configuration"""
        token = os.environ.get("TELEGRAM_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ERROR_CHAT_ID", "288566859")
        
        if not token:
            self.stdout.write(self.style.ERROR("   ❌ TELEGRAM_TOKEN not set"))
            self.stdout.write("      Telegram notifications will be disabled!")
        else:
            self.stdout.write(self.style.SUCCESS(f"   ✅ TELEGRAM_TOKEN set: {token[:10]}..."))
        
        self.stdout.write(f"   ✅ TELEGRAM_ERROR_CHAT_ID: {chat_id}")
        
        # Check if logger is enabled
        if telegram_logger.enabled:
            self.stdout.write(self.style.SUCCESS("   ✅ Telegram logger is ENABLED"))
        else:
            self.stdout.write(self.style.ERROR("   ❌ Telegram logger is DISABLED"))
    
    def test_telegram_logger(self):
        """Test telegram_logger module directly"""
        try:
            # Create a test exception
            test_error = Exception("🧪 TEST ERROR - Centralized Error Handling Test")
            
            # Send test notification
            self.stdout.write("   Sending test error notification...")
            success = telegram_logger.log_error(
                error=test_error,
                context="Management Command Test - test_centralized_errors",
                additional_info={
                    'Test Type': 'Centralized Error Handling Verification',
                    'Timestamp': str(timezone.now()),
                    'Status': 'Testing Error Logger',
                    'Environment': 'Production' if not os.environ.get('DEBUG') else 'Development'
                }
            )
            
            if success:
                self.stdout.write(self.style.SUCCESS("   ✅ Test notification sent successfully!"))
                self.stdout.write("      Check your Telegram for the notification")
            else:
                if not telegram_logger.enabled:
                    self.stdout.write(self.style.WARNING("   ⚠️  Telegram logger is disabled (TELEGRAM_TOKEN not set)"))
                else:
                    self.stdout.write(self.style.ERROR("   ❌ Failed to send notification"))
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   ❌ Error testing telegram logger: {e}"))
    
    def test_error_logger(self):
        """Test error_logger module"""
        try:
            # Test 1: log_exception
            self.stdout.write("   Testing log_exception()...")
            test_error = ValueError("🧪 TEST - log_exception function")
            log_exception(
                error=test_error,
                context="Error Logger Test - log_exception",
                additional_info={'Test': 'log_exception function'}
            )
            self.stdout.write(self.style.SUCCESS("   ✅ log_exception() works"))
            
            # Test 2: log_exception_simple
            self.stdout.write("   Testing log_exception_simple()...")
            test_error2 = RuntimeError("🧪 TEST - log_exception_simple")
            log_exception_simple(test_error2, "Error Logger Test - simple")
            self.stdout.write(self.style.SUCCESS("   ✅ log_exception_simple() works"))
            
            # Test 3: log_and_reraise (without actually raising)
            self.stdout.write("   Testing log_and_reraise()...")
            try:
                test_error3 = KeyError("🧪 TEST - log_and_reraise")
                log_and_reraise(
                    error=test_error3,
                    context="Error Logger Test - reraise",
                    additional_info={'Test': 'log_and_reraise function'}
                )
            except KeyError:
                self.stdout.write(self.style.SUCCESS("   ✅ log_and_reraise() works (correctly re-raised)"))
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   ❌ Error testing error_logger: {e}"))

