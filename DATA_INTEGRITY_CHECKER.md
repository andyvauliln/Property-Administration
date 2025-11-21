# Data Integrity Checker

Automated command that scans your database for corrupted or inconsistent data and reports issues to Telegram.

## Overview

The `check_data_integrity` command performs comprehensive checks on your database to identify:
- Payment mismatches
- Orphaned records
- Booking conflicts
- Missing data
- Outdated pending items

All issues are categorized by severity and reported to Telegram chat ID **288566859**.

## Built-in Checks

### 🚨 Critical Severity

#### 1. Booking Date Overlaps
- **What:** Two active bookings for the same apartment with overlapping dates
- **Why Critical:** Double bookings cause major operational issues
- **Reports:** Both booking IDs, dates, tenants, and apartment

### ⚠️ High Severity

#### 2. Payment Apartment Mismatch
- **What:** Payment has an apartment field that doesn't match the booking's apartment
- **Why Important:** Data inconsistency that can cause reporting errors
- **Reports:** Payment ID, amount, both apartment names, booking details

#### 3. Old Pending Payments
- **What:** Payments in "Pending" status for more than 60 days
- **Why Important:** Likely forgotten or needs follow-up
- **Reports:** Payment amount, date, days pending, tenant info

### 📌 Medium Severity

#### 4. Orphaned Payments
- **What:** Payments without a booking OR apartment reference
- **Why Important:** Hard to track and reconcile
- **Reports:** Payment ID, amount, date, status, notes

#### 5. Users Without Contact Info
- **What:** Active users missing email or phone
- **Why Important:** Cannot communicate with them
- **Reports:** User ID, name, role, missing fields

### ℹ️ Low Severity

#### 6. Cleanings Without Booking
- **What:** Cleaning records not linked to a booking
- **Why Low:** May be intentional for some cleaning types
- **Reports:** Cleaning ID, date, cleaner, apartment

## Usage

### Run Manually

```bash
# Run all checks and send report to Telegram
python manage.py check_data_integrity

# Run but don't send to Telegram (testing)
python manage.py check_data_integrity --no-send

# Send to a different chat ID
python manage.py check_data_integrity --chat-id=123456789
```

### Scheduled Execution

The command is automatically scheduled to run **daily at 9 PM** via cron job.

## Report Format

### When Issues Found

You'll receive a Telegram message like this:

```
🔍 DATA INTEGRITY REPORT

⏰ Time: 2025-11-21 21:00:00
📊 Total Issues: 15

🚨 CRITICAL (2):
• Bookings #123 and #456 overlap in Apartment 4B
  └ Apartment: Apartment 4B
  └ Booking 1 Dates: 2025-11-25 to 2025-11-30
  └ Booking 1 Tenant: John Doe
  └ Booking 2 Dates: 2025-11-28 to 2025-12-05
  └ Booking 2 Tenant: Jane Smith

⚠️ HIGH (5):
• Payment #789 has apartment mismatch
  └ Payment Amount: $1500
  └ Payment Apartment: Apartment 3A
  └ Booking Apartment: Apartment 3B
  └ Tenant: Bob Johnson
  
... and 3 more high priority issues

📌 MEDIUM (6):
• Payment #234 has no booking or apartment reference
  └ Amount: $500
  └ Status: Pending
  
... and 5 more medium priority issues

ℹ️ LOW (2):
• Cleaning #567 has no booking reference
  └ Date: 2025-11-20
  
... and 1 more low priority issue
```

### When No Issues

```
✅ No data integrity issues found!
```

## Adding New Checks

The checker is **fully extendable**. To add a new check:

### 1. Add a new method to the `DataIntegrityChecker` class:

```python
def check_my_custom_issue(self):
    """
    Check for my custom data integrity issue
    """
    print("Checking my custom issue...")
    
    # Your query logic here
    problematic_items = MyModel.objects.filter(
        # your conditions
    )
    
    for item in problematic_items:
        self.add_issue(
            category="My Custom Issue Category",
            severity="high",  # or 'critical', 'medium', 'low'
            description=f"Brief description of the issue",
            details={
                'Field 1': item.field1,
                'Field 2': item.field2,
                'Additional Info': 'whatever you need'
            }
        )
    
    count = problematic_items.count()
    print(f"Found {count} of my custom issues")
    return count
```

### 2. Add it to the `run_all_checks()` method:

```python
def run_all_checks(self):
    # ... existing checks ...
    self.check_payment_apartment_mismatch()
    self.check_orphaned_payments()
    # Add your new check
    self.check_my_custom_issue()
    
    return len(self.issues)
```

That's it! Your new check will automatically be included in the daily reports.

## Example Custom Checks

### Check for Bookings Without Payments

```python
def check_bookings_without_payments(self):
    """Check for bookings that don't have any associated payments"""
    from datetime import date
    
    bookings = Booking.objects.filter(
        start_date__lte=date.today()
    ).exclude(status='Blocked')
    
    for booking in bookings:
        if not hasattr(booking, 'payments') or not booking.payments.exists():
            self.add_issue(
                category="Booking Without Payment",
                severity="high",
                description=f"Booking #{booking.id} has no payments",
                details={
                    'Booking ID': booking.id,
                    'Dates': f"{booking.start_date} to {booking.end_date}",
                    'Apartment': booking.apartment.name if booking.apartment else 'N/A',
                    'Tenant': booking.tenant.full_name if booking.tenant else 'N/A'
                }
            )
```

### Check for Apartments Without Managers

```python
def check_apartments_without_managers(self):
    """Check for apartments that don't have an assigned manager"""
    apartments = Apartment.objects.filter(
        Q(manager__isnull=True) | Q(manager__is_active=False)
    )
    
    for apartment in apartments:
        self.add_issue(
            category="Apartment Without Manager",
            severity="medium",
            description=f"Apartment {apartment.name} has no active manager",
            details={
                'Apartment ID': apartment.id,
                'Name': apartment.name,
                'Address': apartment.address if hasattr(apartment, 'address') else 'N/A'
            }
        )
```

### Check for Expired Contracts

```python
def check_expired_contracts(self):
    """Check for bookings with expired contracts that need renewal"""
    from datetime import date, timedelta
    
    soon = date.today() + timedelta(days=7)
    
    bookings = Booking.objects.filter(
        end_date__gte=date.today(),
        end_date__lte=soon,
        contract_signed=False  # or whatever field you use
    )
    
    for booking in bookings:
        self.add_issue(
            category="Unsigned Contract Expiring Soon",
            severity="high",
            description=f"Booking #{booking.id} ends in <7 days without signed contract",
            details={
                'Booking ID': booking.id,
                'End Date': str(booking.end_date),
                'Days Until End': (booking.end_date - date.today()).days,
                'Tenant': booking.tenant.full_name if booking.tenant else 'N/A'
            }
        )
```

## Cron Schedule

In `cron.js`, the checker runs daily:

```javascript
// Schedule data integrity check to run daily at 9 PM
cron.schedule('0 21 * * *', function () {
    executeCronCommand(
        'Data Integrity Check',
        '/usr/bin/python3 /home/superuser/site/manage.py check_data_integrity',
        '/home/superuser/site/'
    );
});
```

### Change Schedule

To run at a different time or frequency:

```javascript
// Every day at 8 AM
cron.schedule('0 8 * * *', function () { ... });

// Every 6 hours
cron.schedule('0 */6 * * *', function () { ... });

// Every Monday at 9 AM
cron.schedule('0 9 * * 1', function () { ... });

// Twice daily: 9 AM and 9 PM
cron.schedule('0 9,21 * * *', function () { ... });
```

## Benefits

1. **Proactive Issue Detection** - Find problems before they cause issues
2. **Automated Monitoring** - No manual checks needed
3. **Instant Notifications** - Know immediately when data issues appear
4. **Categorized by Priority** - Focus on critical issues first
5. **Detailed Context** - All info needed to fix the issue
6. **Extendable** - Easy to add new checks as needed
7. **Error Handling** - Any errors in the checker itself are reported to Telegram

## Troubleshooting

### No Report Received

1. Check the cron logs: `cron_logs.json`
2. Run manually with: `python manage.py check_data_integrity`
3. Verify Telegram token and chat ID in `.env`

### Too Many Notifications

Adjust severity levels or add filters:

```python
# Only report critical and high
if issue['severity'] in ['critical', 'high']:
    # send to telegram
```

### False Positives

Refine the query logic in your check method or adjust severity levels.

## Files

- **Command:** `mysite/management/commands/check_data_integrity.py`
- **Cron:** `cron.js` (line ~128)
- **Base Class:** `mysite/management/commands/base_command.py`
- **Logger:** `mysite/telegram_logger.py`

## Integration with Error Logging

This command uses the same error logging system, so:
- Any errors in the checker itself are sent to Telegram
- Uses the same chat ID (288566859)
- Same error format and handling
- Automatic retry and error recovery

---

**The data integrity checker is now running daily at 9 PM!** Add custom checks as needed to monitor your specific data requirements.

