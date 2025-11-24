#!/usr/bin/env python3
"""
Comprehensive Twilio Account Checker
Helps diagnose why valid credentials are being rejected
"""

import requests
from base64 import b64encode
from dotenv import load_dotenv
import os

load_dotenv()

def check_account():
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    
    print("=" * 80)
    print("TWILIO ACCOUNT DIAGNOSTIC - DEEP CHECK")
    print("=" * 80)
    print()
    
    # Try different API endpoints to see which one works (if any)
    
    test_endpoints = [
        ("Account Info", f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json"),
        ("Balance", f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Balance.json"),
        ("Incoming Phone Numbers", f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json"),
        ("Conversations", f"https://conversations.twilio.com/v1/Conversations"),
    ]
    
    auth_string = f"{account_sid}:{auth_token}"
    auth_encoded = b64encode(auth_string.encode()).decode()
    headers = {"Authorization": f"Basic {auth_encoded}"}
    
    print("Testing different Twilio API endpoints...")
    print("-" * 80)
    print()
    
    any_success = False
    
    for name, url in test_endpoints:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            status = response.status_code
            
            if status == 200:
                print(f"✅ {name}: SUCCESS (200)")
                any_success = True
                try:
                    data = response.json()
                    if name == "Account Info" and "friendly_name" in data:
                        print(f"   Account Name: {data['friendly_name']}")
                        print(f"   Account Type: {data.get('type', 'N/A')}")
                        print(f"   Account Status: {data.get('status', 'N/A')}")
                except:
                    pass
            elif status == 401:
                print(f"❌ {name}: AUTHENTICATION FAILED (401)")
                try:
                    error = response.json()
                    print(f"   Error: {error.get('message', 'Unknown')}")
                except:
                    pass
            elif status == 403:
                print(f"⚠️  {name}: FORBIDDEN (403) - No permission")
                try:
                    error = response.json()
                    print(f"   Error: {error.get('message', 'Unknown')}")
                except:
                    pass
            else:
                print(f"❓ {name}: Unexpected status ({status})")
                
        except Exception as e:
            print(f"❌ {name}: Connection error - {e}")
        
        print()
    
    print("=" * 80)
    
    if not any_success:
        print()
        print("❌ CRITICAL: ALL API ENDPOINTS FAILED")
        print()
        print("This means one of the following:")
        print()
        print("1. ACCOUNT IS SUSPENDED OR RESTRICTED")
        print("   → Check your Twilio console for suspension/billing warnings")
        print("   → Go to: Console → Account → General Settings")
        print()
        print("2. CREDENTIALS ARE FROM DIFFERENT ACCOUNTS")
        print("   → Account SID and Auth Token must be from SAME account")
        print("   → Double-check both are from the same Twilio console")
        print()
        print("3. ACCOUNT IS A SUB-ACCOUNT")
        print("   → Sub-accounts need parent account credentials")
        print("   → Or sub-account specific credentials")
        print()
        print("4. ACCOUNT WAS RECENTLY CREATED")
        print("   → New accounts might have a propagation delay")
        print("   → Wait 5-10 minutes and try again")
        print()
        print("5. TRIAL ACCOUNT RESTRICTIONS")
        print("   → Trial accounts have API limitations")
        print("   → You may need to upgrade to a paid account")
        print()
        print("=" * 80)
        print()
        print("🔍 NEXT STEPS:")
        print()
        print("1. Take a screenshot of your Twilio Console main page")
        print("   (blur out sensitive info, but show account status)")
        print()
        print("2. Check Account Settings:")
        print("   Console → Settings → General")
        print("   What does it say for 'Account Status'?")
        print()
        print("3. Try creating a NEW Twilio account:")
        print("   - Sometimes old accounts have issues")
        print("   - Create fresh account at https://www.twilio.com/try-twilio")
        print("   - Use new credentials")
        print()
    else:
        print()
        print("✅ SOME ENDPOINTS WORKED!")
        print()
        print("Your credentials are partially valid.")
        print("Check which endpoint failed - that's where the issue is.")
    
    print("=" * 80)

if __name__ == "__main__":
    check_account()


