#!/bin/bash

# Test script for apartment report optimization

echo "================================"
echo "Apartment Report Test Script"
echo "================================"
echo ""

# Check if required files exist
echo "1. Checking files..."
if [ -f "mysite/views/apartments_report.py" ]; then
    echo "   ✓ apartments_report.py found"
else
    echo "   ✗ apartments_report.py NOT found"
    exit 1
fi

# Check if common.log exists or can be created
echo ""
echo "2. Checking log file..."
if [ -f "common.log" ]; then
    echo "   ✓ common.log exists"
    echo "   Last 3 log entries:"
    tail -n 3 common.log
else
    echo "   ⚠ common.log doesn't exist yet (will be created on first run)"
fi

# Check if we can write to /tmp
echo ""
echo "3. Checking debug output location..."
if [ -w "/tmp" ]; then
    echo "   ✓ /tmp is writable (debug JSON will be saved here)"
else
    echo "   ✗ /tmp is not writable"
fi

# Check database connection
echo ""
echo "4. Testing database connection..."
python3 manage.py shell -c "
from mysite.models import Booking, Apartment, ApartmentPrice
booking_count = Booking.objects.count()
apartment_count = Apartment.objects.count()
price_count = ApartmentPrice.objects.count()
print(f'   ✓ Database connected')
print(f'   - Bookings: {booking_count}')
print(f'   - Apartments: {apartment_count}')
print(f'   - Price records: {price_count}')
" 2>/dev/null || echo "   ✗ Database connection failed"

# Check nginx configuration
echo ""
echo "5. Checking nginx timeout settings..."
nginx_timeout=$(sudo grep -E "proxy_read_timeout|proxy_connect_timeout" /etc/nginx/nginx.conf 2>/dev/null | head -n 1)
if [ -n "$nginx_timeout" ]; then
    echo "   ✓ Timeout configured: $nginx_timeout"
else
    echo "   ⚠ No timeout settings found - may need to increase for large reports"
    echo "   Recommended: Add to /etc/nginx/nginx.conf:"
    echo "     proxy_connect_timeout 300;"
    echo "     proxy_read_timeout 300;"
fi

echo ""
echo "================================"
echo "Test Complete!"
echo "================================"
echo ""
echo "To monitor report generation:"
echo "  tail -f common.log"
echo ""
echo "To view debug data after running report:"
echo "  cat /tmp/apartment_report_debug.json | python3 -m json.tool | less"
echo ""


