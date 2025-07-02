#!/usr/bin/env python3
"""
Test script for Apartment Pricing API endpoints
"""

import requests
import json
from datetime import date, timedelta
import os

# Try to load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded configuration from .env file")
except ImportError:
    print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")
    print("   Using environment variables or defaults")

# Configuration
BASE_URL = os.getenv('BASE_URL', 'http://localhost:8000')
AUTH_TOKEN = os.getenv('API_AUTH_TOKEN', 'your_test_token_here')

def make_request(method, endpoint, data=None, params=None):
    """Helper function to make API requests"""
    url = f"{BASE_URL}{endpoint}"
    headers = {'Content-Type': 'application/json'}
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, params=params, headers=headers)
        elif method.upper() == 'POST':
            response = requests.post(url, json=data, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        print(f"\n{'='*60}")
        print(f"REQUEST: {method.upper()} {url}")
        if params:
            print(f"PARAMS: {params}")
        if data:
            print(f"DATA: {json.dumps(data, indent=2)}")
        print(f"{'='*60}")
        
        print(f"STATUS CODE: {response.status_code}")
        
        try:
            response_data = response.json()
            print(f"RESPONSE: {json.dumps(response_data, indent=2)}")
            return response_data
        except json.JSONDecodeError:
            print(f"RESPONSE TEXT: {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"ERROR: {e}")
        return None

def test_get_apartment_booking_dates():
    """Test the GET apartment booking dates endpoint"""
    print("\n🔍 TESTING: GET Apartment Booking Dates")
    
    # Test 1: Get all apartments (should return empty due to no apartment_ids)
    print("\n--- Test 1: Get all apartments (empty response expected) ---")
    params = {'auth_token': AUTH_TOKEN}
    make_request('GET', '/api/apartment-booking-dates/', params=params)
    
    # Test 2: Get specific apartments
    print("\n--- Test 2: Get specific apartments ---")
    params = {
        'auth_token': AUTH_TOKEN,
        'apartment_ids': '1,2,3'  # Adjust these IDs based on your data
    }
    make_request('GET', '/api/apartment-booking-dates/', params=params)
    
    # Test 3: Test without auth token (should fail)
    print("\n--- Test 3: Without auth token (should fail) ---")
    params = {'apartment_ids': '1,2'}
    make_request('GET', '/api/apartment-booking-dates/', params=params)

def test_update_apartment_price_by_rooms():
    """Test the POST update apartment price by rooms endpoint"""
    print("\n🔄 TESTING: POST Update Apartment Price by Rooms")
    
    today = date.today()
    tomorrow = today + timedelta(days=1)
    
    # Test 1: Valid update request
    print("\n--- Test 1: Valid update request ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'number_of_rooms': 2,
        'new_price': 1450.00,
        'effective_date': tomorrow.strftime('%Y-%m-%d'),
        'notes': 'Test API price update for 2-bedroom apartments'
    }
    make_request('POST', '/api/update-apartment-price-by-rooms/', data=data)
    
    # Test 2: Missing required parameters
    print("\n--- Test 2: Missing effective_date (should fail) ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'number_of_rooms': 2,
        'new_price': 1450.00
        # Missing effective_date
    }
    make_request('POST', '/api/update-apartment-price-by-rooms/', data=data)
    
    # Test 3: Invalid date format
    print("\n--- Test 3: Invalid date format (should fail) ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'number_of_rooms': 2,
        'new_price': 1450.00,
        'effective_date': 'invalid-date'
    }
    make_request('POST', '/api/update-apartment-price-by-rooms/', data=data)
    
    # Test 4: Without auth token
    print("\n--- Test 4: Without auth token (should fail) ---")
    data = {
        'number_of_rooms': 2,
        'new_price': 1450.00,
        'effective_date': tomorrow.strftime('%Y-%m-%d')
    }
    make_request('POST', '/api/update-apartment-price-by-rooms/', data=data)

def test_update_single_apartment_price():
    """Test the POST update single apartment price endpoint"""
    print("\n🎯 TESTING: POST Update Single Apartment Price")
    
    today = date.today()
    future_date = today + timedelta(days=30)
    
    # Test 1: Valid single apartment update
    print("\n--- Test 1: Valid single apartment update ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'apartment_id': 1,  # Adjust this ID based on your data
        'new_price': 1650.00,
        'effective_date': future_date.strftime('%Y-%m-%d'),
        'notes': 'Test API single apartment price update'
    }
    make_request('POST', '/api/update-single-apartment-price/', data=data)
    
    # Test 2: Update existing price (same date)
    print("\n--- Test 2: Update existing price (same date) ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'apartment_id': 1,
        'new_price': 1700.00,
        'effective_date': future_date.strftime('%Y-%m-%d'),
        'notes': 'Updated test price - same date'
    }
    make_request('POST', '/api/update-single-apartment-price/', data=data)
    
    # Test 3: Non-existent apartment
    print("\n--- Test 3: Non-existent apartment (should fail) ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'apartment_id': 99999,  # Likely non-existent
        'new_price': 1500.00,
        'effective_date': future_date.strftime('%Y-%m-%d')
    }
    make_request('POST', '/api/update-single-apartment-price/', data=data)
    
    # Test 4: Missing apartment_id
    print("\n--- Test 4: Missing apartment_id (should fail) ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'new_price': 1500.00,
        'effective_date': future_date.strftime('%Y-%m-%d')
    }
    make_request('POST', '/api/update-single-apartment-price/', data=data)

def test_complete_workflow():
    """Test a complete workflow: update prices then retrieve them"""
    print("\n🔄 TESTING: Complete Workflow")
    
    today = date.today()
    test_date = today + timedelta(days=7)
    
    # Step 1: Update a single apartment price
    print("\n--- Step 1: Create a new price record ---")
    data = {
        'auth_token': AUTH_TOKEN,
        'apartment_id': 1,
        'new_price': 1800.00,
        'effective_date': test_date.strftime('%Y-%m-%d'),
        'notes': 'Workflow test price'
    }
    make_request('POST', '/api/update-single-apartment-price/', data=data)
    
    # Step 2: Retrieve apartment data to see the new price
    print("\n--- Step 2: Retrieve apartment data with new price ---")
    params = {
        'auth_token': AUTH_TOKEN,
        'apartment_ids': '1'
    }
    make_request('GET', '/api/apartment-booking-dates/', params=params)

def run_all_tests():
    """Run all API tests"""
    print("🚀 Starting Apartment Pricing API Tests")
    print(f"Base URL: {BASE_URL}")
    print(f"Auth Token: {'SET' if AUTH_TOKEN != 'your_test_token_here' else 'NOT SET (using placeholder)'}")
    
    try:
        test_get_apartment_booking_dates()
        test_update_apartment_price_by_rooms()
        test_update_single_apartment_price()
        test_complete_workflow()
        
        print("\n✅ All tests completed!")
        print("\nNOTE: Some tests are expected to fail (e.g., missing auth token, invalid parameters)")
        print("This demonstrates proper error handling in the API.")
        
    except KeyboardInterrupt:
        print("\n❌ Tests interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error during testing: {e}")

if __name__ == "__main__":
    run_all_tests() 