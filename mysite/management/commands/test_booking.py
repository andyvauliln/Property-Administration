#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
django.setup()

from mysite.models import Booking

print("=== Testing Booking 1470 ===")
print(f"Total bookings: {Booking.objects.count()}")
print(f"Booking 1470 exists: {Booking.objects.filter(id=1470).exists()}")

print("\n=== Bookings near ID 1470 ===")
nearby = Booking.objects.filter(id__gte=1460, id__lte=1480).values_list('id', 'status', 'contract_id')
for b in nearby:
    print(f"ID: {b[0]}, Status: {b[1]}, Contract ID: {b[2]}")

print("\n=== Search by contract_id 4342709 ===")
b = Booking.objects.filter(contract_id='4342709').first()
if b:
    print(f"Found booking! ID: {b.id}, Status: {b.status}, Apartment: {b.apartment}")
else:
    print("No booking found with contract_id='4342709'")

print("\n=== Latest bookings ===")
latest = Booking.objects.order_by('-id')[:5].values_list('id', 'status', 'created_at')
for b in latest:
    print(f"ID: {b[0]}, Status: {b[1]}, Created: {b[2]}")


