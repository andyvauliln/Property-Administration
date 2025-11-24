#!/usr/bin/env python
"""
Comprehensive phone validation test suite
Tests ALL edge cases and problematic scenarios
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
django.setup()

from mysite.models import validate_and_format_phone

def run_comprehensive_tests():
    """Test every possible edge case and problematic phone number"""
    
    test_cases = [
        # ===== CATEGORY 1: VALID US NUMBERS =====
        ("Valid US Numbers", [
            ("2025551234", "+12025551234", "10 digits"),
            ("12025551234", "+12025551234", "11 digits with 1"),
            ("+12025551234", "+12025551234", "Already E.164"),
            ("+1-202-555-1234", "+12025551234", "With dashes"),
            ("+1 202 555 1234", "+12025551234", "With spaces"),
            ("+1 (202) 555-1234", "+12025551234", "Full formatting"),
            ("(202) 555-1234", "+12025551234", "Parentheses format"),
            ("202-555-1234", "+12025551234", "Dashed format"),
            ("202.555.1234", "+12025551234", "Dotted format"),
            ("1-202-555-1234", "+12025551234", "1 with dashes"),
        ]),
        
        # ===== CATEGORY 2: VALID US WITH LEADING ZERO =====
        ("US Numbers with Leading 0", [
            ("02025551234", "+12025551234", "0 + 10 digits"),
            ("01234567890", "+11234567890", "0 + 10 digits (alt)"),
            ("0-202-555-1234", "+12025551234", "0 with dashes"),
            ("0 (202) 555-1234", "+12025551234", "0 with formatting"),
        ]),
        
        # ===== CATEGORY 3: VALID INTERNATIONAL NUMBERS =====
        ("Valid International Numbers", [
            ("+447738195342", "+447738195342", "UK mobile"),
            ("+442071234567", "+442071234567", "UK landline"),
            ("+9179525848", "+9179525848", "India (10 digits)"),
            ("+919876543210", "+919876543210", "India (10 digits alt)"),
            ("+33612345678", "+33612345678", "France"),
            ("+861234567890", "+861234567890", "China"),
            ("+5511987654321", "+5511987654321", "Brazil"),
            ("+81312345678", "+81312345678", "Japan"),
            ("+492012345678", "+492012345678", "Germany"),
            ("+34612345678", "+34612345678", "Spain"),
            ("+61412345678", "+61412345678", "Australia"),
            ("+27123456789", "+27123456789", "South Africa"),
            ("+971501234567", "+971501234567", "UAE"),
            ("+85212345678", "+85212345678", "Hong Kong"),
            ("+6512345678", "+6512345678", "Singapore"),
        ]),
        
        # ===== CATEGORY 4: BOUNDARY CASES (E.164 limits) =====
        ("Boundary Cases", [
            ("+2234567", "+2234567", "Minimum length (7 digits, country code +2)"),
            ("+223456789012345", "+223456789012345", "Maximum length (15 digits)"),
            ("2234567", "+2234567", "7 digits without + (international)"),
            ("223456789012345", "+223456789012345", "15 digits starting with 2 (international)"),
            ("12025551234", "+12025551234", "11 digits starting with 1 (US)"),
            ("+12025551234", "+12025551234", "Valid US format"),
        ]),
        
        # ===== CATEGORY 5: EMPTY/NULL INPUTS =====
        ("Empty/Null Inputs", [
            ("", None, "Empty string"),
            (None, None, "None value"),
            ("   ", None, "Only spaces"),
            ("\t\n", None, "Only whitespace chars"),
        ]),
        
        # ===== CATEGORY 6: INVALID - NO DIGITS =====
        ("Invalid - No Digits", [
            ("abc", None, "Only letters"),
            ("abcdefghij", None, "More letters"),
            ("!@#$%^&*()", None, "Only special chars"),
            ("+++", None, "Multiple plus signs"),
            ("---", None, "Only dashes"),
            ("...", None, "Only dots"),
            ("()", None, "Empty parentheses"),
        ]),
        
        # ===== CATEGORY 7: INVALID - TOO SHORT =====
        ("Invalid - Too Short", [
            ("1", None, "1 digit"),
            ("12", None, "2 digits"),
            ("123", None, "3 digits"),
            ("1234", None, "4 digits"),
            ("12345", None, "5 digits"),
            ("123456", None, "6 digits"),
            ("+1", None, "Just +1"),
            ("+12", None, "+1 + 1 digit"),
            ("+123", None, "+1 + 2 digits"),
            ("0201234567", None, "10 digits but 9 after strip 0"),
            ("0123456789", None, "10 digits but 9 after strip 0 (alt)"),
        ]),
        
        # ===== CATEGORY 8: INVALID - TOO LONG =====
        ("Invalid - Too Long", [
            ("1234567890123456", None, "16 digits"),
            ("12345678901234567", None, "17 digits"),
            ("12345678901234567890", None, "20 digits"),
            ("+1234567890123456", None, "+1 + 15 digits (16 total)"),
            ("99999999999999999999", None, "20 nines"),
        ]),
        
        # ===== CATEGORY 9: INVALID - COUNTRY CODE STARTS WITH 0 =====
        ("Invalid - Country Code with 0", [
            ("+0123456789", None, "Country code starts with 0"),
            ("+01234567890", None, "Country code 0 (11 digits)"),
            ("+012345678901", None, "Country code 0 (12 digits)"),
        ]),
        
        # ===== CATEGORY 10: MIXED LETTERS AND NUMBERS (Extract Digits) =====
        ("Mixed Letters/Numbers - Extract Digits", [
            ("123abc456", None, "Too few digits after extraction (6)"),
            ("abc1234567890", "+11234567890", "Letters then numbers (extracts 12 digits)"),
            ("1234567890abc", "+11234567890", "Numbers then letters (extracts 12 digits)"),
            ("12a34b56c78d90", "+11234567890", "Alternating (extracts 12 digits)"),
            ("+1-abc-def-ghij", None, "Only letters with +1"),
        ]),
        
        # ===== CATEGORY 11: EDGE CASES - MULTIPLE ZEROS =====
        ("Edge Cases - Zeros", [
            ("0000000000", None, "10 zeros (all zeros after strip)"),
            ("00000000000", None, "11 zeros (all zeros after strip)"),
            ("+0000000000", None, "+10 zeros (invalid country code)"),
            ("+1000000000", None, "All zeros after +1 (invalid)"),
            ("1000000000", "+11000000000", "1 + 9 zeros (valid)"),
            ("2000000000", "+12000000000", "2 + 9 zeros (valid)"),
        ]),
        
        # ===== CATEGORY 12: EDGE CASES - UNICODE & SPECIAL =====
        ("Edge Cases - Unicode/Special", [
            ("①②③④⑤⑥⑦⑧⑨⑩", None, "Unicode number chars (not ASCII digits)"),
            ("2️⃣0️⃣2️⃣5️⃣5️⃣5️⃣1️⃣2️⃣3️⃣4️⃣", "+12025551234", "Emoji numbers (extracts ASCII digits)"),
            ("二〇二五五五一二三四", None, "Chinese numbers (not ASCII digits)"),
            ("+1 📞 202-555-1234", "+12025551234", "With emoji (extracts digits)"),
        ]),
        
        # ===== CATEGORY 13: EDGE CASES - WHITESPACE VARIATIONS =====
        ("Edge Cases - Whitespace", [
            ("  2025551234  ", "+12025551234", "Leading/trailing spaces"),
            ("202  555  1234", "+12025551234", "Double spaces"),
            ("202\t555\t1234", "+12025551234", "Tabs"),
            ("202\n555\n1234", "+12025551234", "Newlines"),
        ]),
        
        # ===== CATEGORY 14: EDGE CASES - MULTIPLE PLUS SIGNS =====
        ("Edge Cases - Multiple Plus Signs", [
            ("++12025551234", "+12025551234", "Double plus"),
            ("+++12025551234", "+12025551234", "Triple plus"),
            ("+1+2025551234", "+12025551234", "Plus in middle"),
        ]),
        
        # ===== CATEGORY 15: REAL-WORLD PROBLEMATIC CASES =====
        ("Real-World Problematic Cases", [
            ("+2604105429", "+2604105429", "Short international (Zambia-like)"),
            ("+927383999", "+927383999", "Pakistan-like"),
            ("+1971568418887", None, "Too many digits for +1 (13 total, need 11)"),
            ("+12025551234", "+12025551234", "Valid US (11 digits)"),
            ("+5345676", "+5345676", "Short 7-digit"),
            ("+8157620357", "+8157620357", "10-digit international"),
        ]),
        
        # ===== CATEGORY 16: EDGE CASES - LEADING ZERO VARIATIONS =====
        ("Leading Zero Edge Cases", [
            ("00201234567", None, "Double zero (8 digits after strip all zeros)"),
            ("000201234567", None, "Triple zero (8 digits after strip all zeros)"),
            ("0012025551234", "+12025551234", "Double zero + valid (10 after strip)"),
            ("01", None, "0 + 1 digit (too short)"),
            ("012", None, "0 + 2 digits (too short)"),
            ("0123", None, "0 + 3 digits (too short)"),
            ("01234567", None, "0 + 7 digits (too short after strip)"),
            ("012345678", None, "0 + 8 digits (too short after strip)"),
            ("0123456789", None, "0 + 9 digits (too short after strip)"),
            ("01234567890", "+11234567890", "0 + 10 digits (valid after strip)"),
        ]),
        
        # ===== CATEGORY 17: SPECIAL FORMATTING =====
        ("Special Formatting", [
            ("202 555 1234", "+12025551234", "Space separated"),
            ("202-555-1234", "+12025551234", "Dash separated"),
            ("202.555.1234", "+12025551234", "Dot separated"),
            ("(202)555-1234", "+12025551234", "No space after area"),
            ("202/555/1234", "+12025551234", "Slash separated"),
            ("202_555_1234", "+12025551234", "Underscore separated"),
        ]),
    ]
    
    print("="*80)
    print("COMPREHENSIVE PHONE VALIDATION TEST")
    print("="*80)
    print()
    
    total_tests = 0
    total_passed = 0
    total_failed = 0
    failed_tests = []
    
    for category_name, category_tests in test_cases:
        print(f"\n{'='*80}")
        print(f"CATEGORY: {category_name}")
        print(f"{'='*80}\n")
        
        category_passed = 0
        category_failed = 0
        
        for input_phone, expected, description in category_tests:
            total_tests += 1
            result = validate_and_format_phone(input_phone)
            
            if result == expected:
                status = "✅ PASS"
                total_passed += 1
                category_passed += 1
            else:
                status = "❌ FAIL"
                total_failed += 1
                category_failed += 1
                failed_tests.append({
                    'category': category_name,
                    'description': description,
                    'input': input_phone,
                    'expected': expected,
                    'got': result
                })
            
            print(f"{status} | {description}")
            print(f"     Input:    {repr(input_phone)}")
            print(f"     Expected: {expected}")
            print(f"     Got:      {result}")
            if result != expected:
                print(f"     ⚠️  MISMATCH!")
            print()
        
        print(f"Category Summary: {category_passed} passed, {category_failed} failed out of {len(category_tests)} tests")
    
    # Final Summary
    print("\n" + "="*80)
    print("FINAL SUMMARY")
    print("="*80)
    print(f"Total Tests: {total_tests}")
    print(f"✅ Passed: {total_passed} ({100*total_passed/total_tests:.1f}%)")
    print(f"❌ Failed: {total_failed} ({100*total_failed/total_tests:.1f}%)")
    print("="*80)
    
    # Show failed tests details
    if failed_tests:
        print("\n" + "="*80)
        print("FAILED TESTS DETAILS")
        print("="*80)
        for idx, test in enumerate(failed_tests, 1):
            print(f"\n{idx}. {test['category']} - {test['description']}")
            print(f"   Input:    {repr(test['input'])}")
            print(f"   Expected: {test['expected']}")
            print(f"   Got:      {test['got']}")
    else:
        print("\n🎉 ALL TESTS PASSED! 🎉")
    
    print()
    return total_failed == 0

if __name__ == "__main__":
    success = run_comprehensive_tests()
    exit(0 if success else 1)

