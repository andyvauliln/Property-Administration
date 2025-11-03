#!/usr/bin/env python3
"""
Test script to generate apartment report for last 3 months and analyze data
"""
import os
import sys
import django
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

# Setup Django
sys.path.insert(0, '/home/superuser/site')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
django.setup()

from mysite.models import Booking, Payment, Apartment, ApartmentPrice
from mysite.views.apartments_report import prepare_apartment_rows
from django.db.models import Q
import json

# Calculate date range (last 3 months)
end_date = date.today()
start_date = end_date - relativedelta(months=3)

print(f"=" * 80)
print(f"APARTMENT REPORT TEST - Last 3 Months")
print(f"=" * 80)
print(f"Period: {start_date} to {end_date}")
print(f"")

# Get bookings for the period
print("Fetching bookings...")
bookings = Booking.objects.filter(
    start_date__lte=end_date, 
    end_date__gte=start_date
)

booking_count = bookings.count()
print(f"Found {booking_count} bookings overlapping this period")
print(f"")

if booking_count == 0:
    print("❌ No bookings found for this period!")
    sys.exit(0)

# Generate report data
print("Generating report data...")
print("-" * 80)
rows = prepare_apartment_rows(bookings, start_date, end_date)
print("-" * 80)
print(f"")

# Analyze the data
print(f"ANALYSIS RESULTS")
print(f"=" * 80)
print(f"")

# Basic statistics
total_rows = len(rows)
rows_with_payments = sum(1 for r in rows if r['payment_found'])
rows_without_payments = sum(1 for r in rows if not r['payment_found'])
total_days = sum(r['days'] for r in rows)
total_payment_amount = sum(r['total_payment'] for r in rows)

print(f"📊 SUMMARY STATISTICS:")
print(f"   Total bookings processed: {total_rows}")
print(f"   ✓ With payments (Rent/Hold Deposit): {rows_with_payments}")
print(f"   ❌ Missing payments: {rows_without_payments}")
print(f"   Total days: {total_days}")
print(f"   Total payment amount: ${total_payment_amount:,.2f}")
print(f"")

# Average daily rates
if total_days > 0:
    avg_adr_payments = sum(r['adr_payments'] * r['days'] for r in rows) / total_days
    avg_adr_table = sum(r['adr_price_table'] * r['days'] for r in rows) / total_days
    
    print(f"💰 AVERAGE DAILY RATES:")
    print(f"   From actual payments: ${avg_adr_payments:.2f}/day")
    print(f"   From price table: ${avg_adr_table:.2f}/day")
    print(f"   Difference: ${avg_adr_payments - avg_adr_table:.2f}/day")
    print(f"")

# Price variance analysis
positive_diff = [r for r in rows if r['price_difference'] > 10]
negative_diff = [r for r in rows if r['price_difference'] < -10]

print(f"📈 PRICE VARIANCE:")
print(f"   Bookings above expected price (>$10/day): {len(positive_diff)}")
print(f"   Bookings below expected price (<-$10/day): {len(negative_diff)}")
print(f"")

# Apartment breakdown
apartment_stats = {}
for row in rows:
    apt_name = row['apartment_name']
    if apt_name not in apartment_stats:
        apartment_stats[apt_name] = {
            'bookings': 0,
            'days': 0,
            'payment': 0,
            'missing_payments': 0
        }
    apartment_stats[apt_name]['bookings'] += 1
    apartment_stats[apt_name]['days'] += row['days']
    apartment_stats[apt_name]['payment'] += row['total_payment']
    if not row['payment_found']:
        apartment_stats[apt_name]['missing_payments'] += 1

print(f"🏢 TOP 10 APARTMENTS BY REVENUE:")
sorted_apts = sorted(apartment_stats.items(), key=lambda x: x[1]['payment'], reverse=True)[:10]
for i, (apt_name, stats) in enumerate(sorted_apts, 1):
    missing_text = f" (⚠️ {stats['missing_payments']} missing payments)" if stats['missing_payments'] > 0 else ""
    print(f"   {i:2d}. {apt_name[:40]:40s} ${stats['payment']:>10,.2f} ({stats['bookings']} bookings, {stats['days']} days){missing_text}")
print(f"")

# Show problematic bookings
if rows_without_payments > 0:
    print(f"⚠️  BOOKINGS WITH MISSING PAYMENTS:")
    missing_bookings = [r for r in rows if not r['payment_found']][:10]
    for r in missing_bookings:
        print(f"   • ID {r['booking_id']}: {r['apartment_name']}")
        print(f"     {r['start_date']} to {r['end_date']} ({r['days']} days)")
        print(f"     Renter: {r['renter']}")
        print(f"     Expected price: ${r['adr_price_table']:.2f}/day")
        print(f"")

# Data quality checks
print(f"✅ DATA QUALITY CHECKS:")
print(f"")

# Check 1: All rows have valid dates
invalid_dates = [r for r in rows if not r['start_date'] or not r['end_date']]
if invalid_dates:
    print(f"   ❌ Found {len(invalid_dates)} rows with invalid dates")
else:
    print(f"   ✓ All rows have valid dates")

# Check 2: Days calculation is positive
negative_days = [r for r in rows if r['days'] < 0]
if negative_days:
    print(f"   ❌ Found {len(negative_days)} rows with negative days")
else:
    print(f"   ✓ All rows have positive days")

# Check 3: ADR calculations are reasonable
extreme_adr = [r for r in rows if r['days'] > 0 and (r['adr_payments'] > 1000 or r['adr_price_table'] > 1000)]
if extreme_adr:
    print(f"   ⚠️  Found {len(extreme_adr)} rows with ADR > $1000/day (may be legitimate luxury apartments)")
else:
    print(f"   ✓ All ADR values are reasonable (<$1000/day)")

# Check 4: Price table data availability
no_price_data = [r for r in rows if r['days'] > 0 and r['adr_price_table'] == 0]
if no_price_data:
    print(f"   ⚠️  Found {len(no_price_data)} rows with no price table data")
else:
    print(f"   ✓ All bookings have price table data")

# Check 5: Zero-day bookings
zero_days = [r for r in rows if r['days'] == 0]
if zero_days:
    print(f"   ⚠️  Found {len(zero_days)} rows with 0 days (may be outside period)")
else:
    print(f"   ✓ No zero-day bookings")

print(f"")

# Sample data for verification
print(f"📋 SAMPLE BOOKINGS (First 5):")
for i, r in enumerate(rows[:5], 1):
    payment_status = "✓ Has payment" if r['payment_found'] else "❌ NO PAYMENT"
    print(f"\n{i}. Booking ID: {r['booking_id']}")
    print(f"   Apartment: {r['apartment_name']}")
    print(f"   Period: {r['start_date']} to {r['end_date']} ({r['days']} days)")
    print(f"   Renter: {r['renter']}")
    print(f"   Payment status: {payment_status}")
    print(f"   Total payment: ${r['total_payment']:.2f}")
    print(f"   ADR (Payments): ${r['adr_payments']:.2f}/day")
    print(f"   ADR (Price Table): ${r['adr_price_table']:.2f}/day")
    print(f"   Difference: ${r['price_difference']:.2f}/day")

print(f"")
print(f"=" * 80)

# Save to file
output_file = '/tmp/apartment_report_3months.json'
report_data = {
    'period_start': start_date.isoformat(),
    'period_end': end_date.isoformat(),
    'total_bookings': total_rows,
    'bookings_with_missing_payments': rows_without_payments,
    'total_payment_amount': float(total_payment_amount),
    'summary': {
        'total_days': total_days,
        'avg_adr_payments': round(avg_adr_payments, 2) if total_days > 0 else 0,
        'avg_adr_table': round(avg_adr_table, 2) if total_days > 0 else 0,
    },
    'apartments': apartment_stats,
    'rows': rows
}

with open(output_file, 'w') as f:
    json.dump(report_data, f, indent=2)

print(f"✅ Full report data saved to: {output_file}")
print(f"")
print(f"To view full data:")
print(f"  cat {output_file} | python3 -m json.tool | less")
print(f"")
print(f"=" * 80)


