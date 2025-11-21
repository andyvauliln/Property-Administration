"""
Centralized Telegram Error Logger
This module provides utilities for sending error notifications to Telegram
"""
import os
import traceback
import requests
from datetime import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramErrorLogger:
    """
    Centralized logger for sending error notifications to Telegram
    """
    
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_TOKEN")
        self.error_chat_id = os.environ.get("TELEGRAM_ERROR_CHAT_ID", "288566859")
        self.enabled = bool(self.token)
        
        if not self.enabled:
            logger.warning("TELEGRAM_TOKEN not found in environment variables. Telegram error notifications disabled.")
    
    def send_telegram_message(self, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to Telegram
        
        Args:
            chat_id: The Telegram chat ID to send to
            message: The message to send
            parse_mode: Message formatting (HTML or Markdown)
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.enabled:
            return False
            
        try:
            # Telegram message limit is 4096 characters
            if len(message) > 4000:
                message = message[:3900] + "\n\n... (message truncated)"
            
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {str(e)}")
            return False
    
    def format_error_message(
        self, 
        error: Exception, 
        context: Optional[str] = None,
        additional_info: Optional[dict] = None
    ) -> str:
        """
        Format an error message for Telegram
        
        Args:
            error: The exception that occurred
            context: Additional context about where the error occurred
            additional_info: Dictionary of additional information to include
            
        Returns:
            str: Formatted error message
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"🚨 <b>ERROR ALERT</b> 🚨\n\n"
        message += f"⏰ <b>Time:</b> {timestamp}\n"
        
        if context:
            message += f"📍 <b>Context:</b> {context}\n"
        
        # Highlight user information if available
        if additional_info:
            user_fields = ['Username', 'User ID', 'Full Name', 'Email', 'Role', 'Phone', 'Authenticated']
            user_info = {k: v for k, v in additional_info.items() if k in user_fields}
            
            if user_info:
                message += f"\n👤 <b>User Information:</b>\n"
                for key, value in user_info.items():
                    message += f"  • {key}: {value}\n"
        
        message += f"\n❌ <b>Error Type:</b> {type(error).__name__}\n"
        message += f"💬 <b>Message:</b> {str(error)}\n"
        
        # Add other additional info (excluding user fields already displayed)
        if additional_info:
            user_fields = ['Username', 'User ID', 'Full Name', 'Email', 'Role', 'Phone', 'Authenticated', 'Staff', 'Superuser']
            other_info = {k: v for k, v in additional_info.items() if k not in user_fields}
            
            if other_info:
                message += f"\n📊 <b>Request Details:</b>\n"
                for key, value in other_info.items():
                    message += f"  • {key}: {value}\n"
        
        # Add traceback
        tb = traceback.format_exc()
        if tb and tb != "NoneType: None\n":
            message += f"\n📋 <b>Traceback:</b>\n<pre>{tb[:1000]}</pre>"
        
        return message
    
    def log_error(
        self, 
        error: Exception, 
        context: Optional[str] = None,
        additional_info: Optional[dict] = None,
        chat_id: Optional[str] = None
    ) -> bool:
        """
        Log an error and send it to Telegram
        
        Args:
            error: The exception that occurred
            context: Additional context about where the error occurred
            additional_info: Dictionary of additional information to include
            chat_id: Override the default error chat ID
            
        Returns:
            bool: True if notification was sent successfully
        """
        # Log to Django logger
        logger.error(f"Error in {context or 'unknown context'}: {str(error)}", exc_info=True)
        
        if not self.enabled:
            return False
        
        # Format and send message
        message = self.format_error_message(error, context, additional_info)
        target_chat_id = chat_id or self.error_chat_id
        
        return self.send_telegram_message(target_chat_id, message)
    
    def log_critical(
        self, 
        message: str, 
        context: Optional[str] = None,
        additional_info: Optional[dict] = None,
        chat_id: Optional[str] = None
    ) -> bool:
        """
        Log a critical message (not necessarily an exception) to Telegram
        
        Args:
            message: The critical message to log
            context: Additional context
            additional_info: Dictionary of additional information to include
            chat_id: Override the default error chat ID
            
        Returns:
            bool: True if notification was sent successfully
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        telegram_message = f"⚠️ <b>CRITICAL ALERT</b> ⚠️\n\n"
        telegram_message += f"⏰ <b>Time:</b> {timestamp}\n"
        
        if context:
            telegram_message += f"📍 <b>Context:</b> {context}\n"
        
        telegram_message += f"\n💬 <b>Message:</b> {message}\n"
        
        if additional_info:
            telegram_message += f"\n📊 <b>Additional Info:</b>\n"
            for key, value in additional_info.items():
                telegram_message += f"  • {key}: {value}\n"
        
        # Log to Django logger
        logger.critical(f"Critical alert in {context or 'unknown context'}: {message}")
        
        if not self.enabled:
            return False
        
        target_chat_id = chat_id or self.error_chat_id
        return self.send_telegram_message(target_chat_id, telegram_message)


# Global instance
telegram_logger = TelegramErrorLogger()


# Convenience functions
def log_error(error: Exception, context: str = None, additional_info: dict = None, chat_id: str = None) -> bool:
    """Log an error to Telegram"""
    return telegram_logger.log_error(error, context, additional_info, chat_id)


def log_critical(message: str, context: str = None, additional_info: dict = None, chat_id: str = None) -> bool:
    """Log a critical message to Telegram"""
    return telegram_logger.log_critical(message, context, additional_info, chat_id)

