#!/usr/bin/env python
"""
Test script to verify phone number validation
Tests the problematic phone numbers identified in the audit
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
django.setup()

from mysite.models import validate_and_format_phone

def test_phone_validation():
    """Test various phone number formats"""
    
    test_cases = [
        # (input, expected_output, description)
        
        # Valid US numbers
        ("+12025551234", "+12025551234", "Valid US E.164"),
        ("2025551234", "+12025551234", "US 10 digits"),
        ("12025551234", "+12025551234", "US 11 digits with 1"),
        ("+1 202 555 1234", "+12025551234", "US with spaces"),
        ("+1-202-555-1234", "+12025551234", "US with dashes"),
        ("(202) 555-1234", "+12025551234", "US formatted"),
        
        # International numbers - from actual problem cases
        ("+9179525848", "+9179525848", "India number (10 digits after +91)"),
        ("+447738195342", "+447738195342", "UK number (12 digits total)"),
        
        # More international examples
        ("+442071234567", "+442071234567", "UK London number"),
        ("+33612345678", "+33612345678", "France mobile"),
        ("+861234567890", "+861234567890", "China number"),
        ("+5511987654321", "+5511987654321", "Brazil mobile"),
        
        # Invalid cases
        ("", None, "Empty string"),
        (None, None, "None value"),
        ("abc", None, "No digits"),
        ("+", None, "Just plus sign"),
        ("123", None, "Too short (3 digits)"),
        ("123456", None, "Too short (6 digits)"),
        ("+0123456789", None, "Invalid country code (starts with 0)"),
        ("++12025551234", "+12025551234", "Double plus (will clean)"),
        
        # Edge cases
        ("02025551234", "+12025551234", "US starting with 0 (11 digits total, 10 after strip)"),
        ("01234567890", "+11234567890", "US starting with 0 (11 digits)"),
        ("0201234567", None, "US starting with 0 but too short (only 9 after strip)"),
        ("  +12025551234  ", "+12025551234", "With whitespace"),
    ]
    
    print("="*80)
    print("PHONE VALIDATION TEST RESULTS")
    print("="*80)
    print()
    
    passed = 0
    failed = 0
    
    for input_phone, expected, description in test_cases:
        result = validate_and_format_phone(input_phone)
        status = "✅ PASS" if result == expected else "❌ FAIL"
        
        if result == expected:
            passed += 1
        else:
            failed += 1
        
        print(f"{status} | {description}")
        print(f"     Input:    {repr(input_phone)}")
        print(f"     Expected: {expected}")
        print(f"     Got:      {result}")
        
        if result != expected:
            print(f"     ⚠️  MISMATCH!")
        print()
    
    print("="*80)
    print(f"SUMMARY: {passed} passed, {failed} failed out of {len(test_cases)} tests")
    print("="*80)
    print()
    
    # Test with actual User model
    print("="*80)
    print("TESTING WITH USER MODEL")
    print("="*80)
    print()
    
    from mysite.models import User
    
    # Create test user with various phone formats
    test_user_cases = [
        ("+9179525848", "+9179525848", "India number"),
        ("+447738195342", "+447738195342", "UK number"),
        ("2025551234", "+12025551234", "US 10 digits"),
        ("invalid", None, "Invalid phone"),
    ]
    
    for input_phone, expected, description in test_user_cases:
        # Create a test user (we'll delete it after)
        try:
            user = User(
                email=f"test_{input_phone.replace('+', '')}@example.com",
                full_name=f"Test User {description}",
                phone=input_phone,
                role='Tenant'
            )
            user.save()
            
            status = "✅ PASS" if user.phone == expected else "❌ FAIL"
            print(f"{status} | {description}")
            print(f"     Input:    {repr(input_phone)}")
            print(f"     Expected: {expected}")
            print(f"     Saved as: {user.phone}")
            
            # Clean up
            user.delete()
            print(f"     ✓ Test user deleted")
            print()
            
        except Exception as e:
            print(f"❌ ERROR | {description}")
            print(f"     Input: {repr(input_phone)}")
            print(f"     Error: {e}")
            print()
    
    print("="*80)
    print("TEST COMPLETE")
    print("="*80)

if __name__ == "__main__":
    test_phone_validation()

