#!/usr/bin/env python
import os
import sys
import django
from django.conf import settings

# Add the project directory to Python path
sys.path.append('/home/superuser/site')

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth import get_user_model
from mysite.views.booking_availability import booking_availability

def test_booking_availability():
    # Create a mock request
    factory = RequestFactory()
    request = factory.get('/booking-availability/?rooms=2&start_date=July%2001%202025&end_date=September%2030%202025')
    
    # Create or get a user with appropriate role
    User = get_user_model()
    try:
        user = User.objects.filter(role__in=['Admin', 'Manager']).first()
        if not user:
            print("No Admin or Manager user found. Creating a test admin user...")
            user = User.objects.create_user(
                username='test_admin',
                email='admin@test.com',
                password='testpass',
                role='Admin'
            )
    except Exception as e:
        print(f"Error getting user: {e}")
        # Create a mock user object
        class MockUser:
            role = 'Admin'
            id = 1
        user = MockUser()
    
    request.user = user
    
    print("Calling booking_availability view...")
    print("=" * 60)
    
    try:
        response = booking_availability(request)
        print("View executed successfully!")
        print(f"Response status: {response.status_code}")
    except Exception as e:
        print(f"Error executing view: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_booking_availability() 