#!/bin/bash

# Payment Sync V2 Testing Commands

echo "🚀 Payment Sync V2 Testing Commands"
echo "===================================="
echo ""

echo "1️⃣  Start Django development server:"
echo "   python manage.py runserver 0.0.0.0:8000"
echo ""

echo "2️⃣  Access the new Payment Sync V2:"
echo "   http://68.183.124.79/payments-sync-v2/"
echo "   or"
echo "   http://localhost:8000/payments-sync-v2/"
echo ""

echo "3️⃣  Test with sample CSV:"
echo "   Create a test CSV file with format:"
echo "   Date,Description,Amount,Running Bal."
echo "   01/15/2025,Rent Payment John Doe,1500.00,5000.00"
echo "   01/16/2025,Check 283 Apartment A,-450.00,4550.00"
echo ""

echo "4️⃣  Check Django logs for errors:"
echo "   tail -f /var/log/django/error.log"
echo "   (or check your terminal if running development server)"
echo ""

echo "5️⃣  Test database connection:"
echo "   python manage.py shell"
echo "   >>> from mysite.models import Payment, PaymentMethod, Apartment, PaymenType"
echo "   >>> Payment.objects.count()"
echo ""

echo "6️⃣  Verify URL routing:"
echo "   python manage.py show_urls | grep sync"
echo ""

echo "✅ Quick Test Checklist:"
echo "   [ ] Can access /payments-sync-v2/ without errors"
echo "   [ ] Upload form is visible"
echo "   [ ] Can upload CSV file"
echo "   [ ] Payments are displayed in tabs"
echo "   [ ] Can switch between tabs"
echo "   [ ] Search functionality works"
echo "   [ ] Can click on payments to open modal"
echo "   [ ] Modal displays correctly"
echo "   [ ] Can add payment to save list"
echo "   [ ] Can save all payments"
echo ""

echo "🐛 Common Issues:"
echo "   - Template not found: Check _base.html exists"
echo "   - Import error: Check all models are imported correctly"
echo "   - No matches found: Adjust amount_delta and date_delta"
echo "   - JavaScript error: Check browser console (F12)"
echo ""

echo "📝 For detailed testing guide, see:"
echo "   PAYMENT_SYNC_V2_TESTING.md"
echo ""

