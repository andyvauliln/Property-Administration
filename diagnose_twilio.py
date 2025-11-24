#!/usr/bin/env python3
"""
Comprehensive Twilio credentials diagnostic tool
"""

import requests
from base64 import b64encode
import sys

def test_credentials(account_sid, auth_token):
    """Test Twilio credentials with detailed error reporting"""
    
    print("=" * 70)
    print("TWILIO CREDENTIALS DIAGNOSTIC")
    print("=" * 70)
    print()
    
    # Validate format
    print("📋 Step 1: Validating Credential Format")
    print("-" * 70)
    
    if not account_sid or not auth_token:
        print("❌ FATAL: Credentials are empty")
        return False
    
    print(f"Account SID: {account_sid}")
    print(f"Auth Token:  {auth_token[:5]}...{auth_token[-5:]}")
    print()
    
    if not account_sid.startswith("AC"):
        print(f"❌ ERROR: Account SID must start with 'AC'")
        print(f"   Your Account SID starts with: '{account_sid[:2]}'")
        return False
    
    if len(account_sid) != 34:
        print(f"❌ ERROR: Account SID must be 34 characters")
        print(f"   Your Account SID is: {len(account_sid)} characters")
        return False
    
    if len(auth_token) != 32:
        print(f"⚠️  WARNING: Auth Token is usually 32 characters")
        print(f"   Your Auth Token is: {len(auth_token)} characters")
        print(f"   This might be okay if it's an API Key Secret")
    
    print("✅ Format validation passed")
    print()
    
    # Test authentication
    print("🔐 Step 2: Testing Authentication")
    print("-" * 70)
    
    auth_string = f"{account_sid}:{auth_token}"
    auth_encoded = b64encode(auth_string.encode()).decode()
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json"
    headers = {
        "Authorization": f"Basic {auth_encoded}"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        print(f"Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print()
            print("✅ ✅ ✅ SUCCESS! Authentication works! ✅ ✅ ✅")
            print()
            print(f"Account Details:")
            print(f"  - Friendly Name: {data.get('friendly_name')}")
            print(f"  - Status: {data.get('status')}")
            print(f"  - Type: {data.get('type')}")
            print(f"  - Date Created: {data.get('date_created')}")
            return True
            
        elif response.status_code == 401:
            error_data = response.json()
            print(f"❌ Authentication Failed!")
            print(f"   Error Code: {error_data.get('code')}")
            print(f"   Message: {error_data.get('message')}")
            print(f"   More Info: {error_data.get('more_info')}")
            print()
            print("🔧 TROUBLESHOOTING:")
            print()
            print("1. CHECK AUTH TOKEN:")
            print("   - Go to: https://console.twilio.com/")
            print("   - Navigate to: Account > API keys & tokens")
            print("   - Look for 'Auth token (primary)'")
            print("   - Click 'Show' to reveal the token")
            print("   - Make sure it matches what you have")
            print()
            print("2. AUTH TOKEN MAY HAVE BEEN REGENERATED:")
            print("   - If you clicked 'Request a secondary token' or regenerated")
            print("   - The old token becomes invalid immediately")
            print("   - You need to copy the NEW active token")
            print()
            print("3. ACCOUNT STATUS:")
            print("   - Check if your account is active (not suspended)")
            print("   - Check if you have any billing issues")
            print("   - Check if your account needs verification")
            print()
            print("4. USING WRONG CREDENTIAL TYPE:")
            print("   - Make sure you're using 'Auth Token' not 'API Key'")
            print("   - Auth Token = 32 characters")
            print("   - API Key SID starts with 'SK'")
            return False
            
        else:
            print(f"❌ Unexpected error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False

def main():
    # Test with credentials from env file
    print("Testing credentials from .env file...")
    print()
    
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    
    if not account_sid or not auth_token:
        print("❌ Could not load credentials from .env file")
        print()
        print("Please provide credentials manually:")
        account_sid = input("Account SID: ").strip()
        auth_token = input("Auth Token: ").strip()
    
    success = test_credentials(account_sid, auth_token)
    
    print()
    print("=" * 70)
    
    if success:
        print("✅ Your credentials are working! You can now:")
        print("   1. Make sure they're in your .env file")
        print("   2. Restart the service: ./start.sh")
    else:
        print("❌ Please fix the issues above and try again")
    
    print("=" * 70)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()


