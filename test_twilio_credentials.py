#!/usr/bin/env python3
"""
Test script to verify Twilio credentials are valid
Run this after updating your .env file to ensure credentials work before restarting the service
"""

from dotenv import load_dotenv
import os
import sys

# Load .env file
load_dotenv()

def test_credentials():
    """Test if Twilio credentials are valid"""
    
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    
    if not account_sid or not auth_token:
        print("❌ ERROR: Twilio credentials not found in environment variables")
        print("   Make sure your .env file contains:")
        print("   - TWILIO_ACCOUNT_SID")
        print("   - TWILIO_AUTH_TOKEN")
        return False
    
    print(f"✓ Account SID loaded: {account_sid[:10]}...")
    print(f"✓ Auth Token loaded: {'*' * 10}")
    print(f"  Account SID length: {len(account_sid)}")
    print(f"  Auth Token length: {len(auth_token)}")
    
    # Validate format
    if not account_sid.startswith("AC") or len(account_sid) != 34:
        print(f"❌ ERROR: Invalid Account SID format")
        print(f"   Expected: AC followed by 32 characters (total 34)")
        print(f"   Got: {account_sid[:10]}... (length: {len(account_sid)})")
        return False
    
    if len(auth_token) != 32:
        print(f"❌ ERROR: Invalid Auth Token length")
        print(f"   Expected: 32 characters")
        print(f"   Got: {len(auth_token)} characters")
        return False
    
    print("\n✓ Credential format is valid")
    print("\nTesting authentication with Twilio API...")
    
    # Test Twilio authentication
    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        
        # Try to fetch account info
        account = client.api.accounts(account_sid).fetch()
        
        print(f"\n✅ SUCCESS! Twilio authentication works!")
        print(f"   Account Name: {account.friendly_name}")
        print(f"   Account Status: {account.status}")
        print(f"\nYou can now restart the service with: ./start.sh")
        return True
        
    except Exception as e:
        print(f"\n❌ AUTHENTICATION FAILED: {e}")
        print("\nPossible issues:")
        print("1. The credentials are invalid or expired")
        print("2. The credentials don't belong to the same Twilio account")
        print("3. Your Twilio account is suspended or has restrictions")
        print("\nPlease:")
        print("1. Log in to https://console.twilio.com/")
        print("2. Go to Account → API keys & tokens")
        print("3. Get fresh credentials")
        print("4. Update your .env file")
        print("5. Run this test again")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Twilio Credentials Test")
    print("=" * 60)
    print()
    
    success = test_credentials()
    
    print()
    print("=" * 60)
    
    sys.exit(0 if success else 1)

