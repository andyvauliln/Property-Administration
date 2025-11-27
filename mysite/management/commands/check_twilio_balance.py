#!/usr/bin/env python3
"""
Check Twilio account balance and send notification to Telegram
This script is designed to be run as a cron job
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# Add the project directory to Python path
project_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(project_dir))

from dotenv import load_dotenv

# Load environment variables
env_path = project_dir / '.env'
load_dotenv(env_path)

def format_currency(amount):
    """Format currency amount"""
    return f"${amount:.2f}"

def get_twilio_balance():
    """Get Twilio account balance"""
    try:
        from twilio.rest import Client
        
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        
        if not account_sid or not auth_token:
            return None, "Twilio credentials not found in environment variables"
        
        client = Client(account_sid, auth_token)
        
        # Fetch account balance
        balance = client.api.v2010.accounts(account_sid).balance.fetch()
        
        return {
            'balance': float(balance.balance),
            'currency': balance.currency,
            'account_sid': account_sid[:10] + '...'
        }, None
        
    except Exception as e:
        return None, f"Error fetching Twilio balance: {str(e)}"

def send_telegram_message(message):
    """Send message to Telegram"""
    try:
        import requests
        
        token = os.environ.get("TELEGRAM_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ERROR_CHAT_ID")
        
        if not token:
            return False, "TELEGRAM_TOKEN not found in environment variables"
        
        if not chat_id:
            return False, "TELEGRAM_ERROR_CHAT_ID not found in environment variables"
        
        # If multiple chat IDs, use the first one
        if ',' in chat_id:
            chat_id = chat_id.split(',')[0].strip()
        
        # Debug: Log chat_id (first few chars only for security)
        print(f"DEBUG: Using chat_id: {str(chat_id)[:5]}...")
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        
        return True, "Message sent successfully"
        
    except requests.exceptions.HTTPError as e:
        # Get detailed error message from Telegram
        error_detail = ""
        try:
            error_detail = f" - {response.json()}"
        except:
            pass
        return False, f"Telegram API error: {str(e)}{error_detail}"
    except Exception as e:
        return False, f"Error sending Telegram message: {str(e)}"

def main():
    """Main function"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[{timestamp}] Checking Twilio balance...")
    
    # Get balance
    balance_data, error = get_twilio_balance()
    
    if error:
        print(f"[{timestamp}] ERROR: {error}")
        
        # Try to send error notification
        error_message = f"🚨 <b>Twilio Balance Check Failed</b>\n\n"
        error_message += f"⏰ Time: {timestamp}\n"
        error_message += f"❌ Error: {error}"
        
        send_telegram_message(error_message)
        return 1
    
    # Format message
    balance_amount = balance_data['balance']
    currency = balance_data['currency']
    account = balance_data['account_sid']
    
    # Determine emoji based on balance
    if balance_amount > 20:
        emoji = "✅"
        status = "Good"
    elif balance_amount > 10:
        emoji = "⚠️"
        status = "Warning"
    else:
        emoji = "🚨"
        status = "Critical"
    
    message = f"{emoji} <b>Twilio Balance Report</b>\n\n"
    message += f"⏰ <b>Time:</b> {timestamp}\n"
    message += f"💰 <b>Balance:</b> {format_currency(balance_amount)} {currency}\n"
    message += f"📊 <b>Status:</b> {status}\n"
    message += f"🔑 <b>Account:</b> {account}\n"
    
    # Add warning if balance is low
    if balance_amount <= 10:
        message += f"\n⚠️ <b>Warning:</b> Balance is low! Please top up your account."
    
    # Send to Telegram
    success, result = send_telegram_message(message)
    
    if success:
        print(f"[{timestamp}] Balance: {format_currency(balance_amount)} {currency}")
        print(f"[{timestamp}] Telegram notification sent successfully")
        return 0
    else:
        print(f"[{timestamp}] Balance: {format_currency(balance_amount)} {currency}")
        print(f"[{timestamp}] Failed to send Telegram notification: {result}")
        return 1

if __name__ == "__main__":
    sys.exit(main())




