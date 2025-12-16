"""
Data Integrity Checker
Scans the database for corrupted or inconsistent data and reports to Telegram
"""
from mysite.management.commands.base_command import BaseCommandWithErrorHandling
from mysite.telegram_logger import telegram_logger
from mysite.models import Payment, Booking, Cleaning, Apartment, User
from django.db.models import Q
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class DataIntegrityChecker:
    """
    Extendable class for checking data integrity issues
    Add new check methods following the pattern: check_<name>()
    """
    
    def __init__(self):
        self.issues = []
        self.chat_id = "288566859"  # Default error chat ID
        
    def add_issue(self, category, severity, description, details=None):
        """Add an integrity issue to the list"""
        self.issues.append({
            'category': category,
            'severity': severity,  # 'critical', 'high', 'medium', 'low'
            'description': description,
            'details': details or {},
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    
    def check_payment_apartment_mismatch(self):
        """
        Check for payments where the apartment field doesn't match the booking's apartment
        """
        print("\n" + "=" * 60)
        print("🏠 PAYMENT APARTMENT MISMATCH CHECK")
        print("=" * 60)
        print("Problem: Payment has 'apartment' field set to a different")
        print("         apartment than the booking it's linked to.")
        print("Why it matters: This causes incorrect financial reports and")
        print("               revenue attribution to wrong properties.")
        print("-" * 60)
        
        # Get payments that have both an apartment and a booking
        payments_with_booking = Payment.objects.filter(
            booking__isnull=False,
            apartment__isnull=False
        ).select_related('booking', 'apartment', 'booking__apartment')
        
        mismatches = []
        example_shown = False
        for payment in payments_with_booking:
            if payment.booking.apartment and payment.apartment.id != payment.booking.apartment.id:
                mismatches.append(payment)
                
                # Show first example
                if not example_shown:
                    print("📋 EXAMPLE:")
                    print(f"   Payment ID: {payment.id}")
                    print(f"   Amount: ${payment.amount}")
                    print(f"   Payment Date: {payment.payment_date}")
                    print(f"   Payment's Apartment: {payment.apartment.name} (ID: {payment.apartment.id})")
                    print(f"   Booking ID: {payment.booking.id}")
                    print(f"   Booking's Apartment: {payment.booking.apartment.name} (ID: {payment.booking.apartment.id})")
                    print(f"   Tenant: {payment.booking.tenant.full_name if payment.booking.tenant else 'N/A'}")
                    print(f"   ⚠️  Payment says '{payment.apartment.name}' but booking says '{payment.booking.apartment.name}'")
                    example_shown = True
                
                self.add_issue(
                    category="Payment Apartment Mismatch",
                    severity="high",
                    description=f"Payment #{payment.id} has apartment mismatch",
                    details={
                        'Payment ID': payment.id,
                        'Payment Amount': f"${payment.amount}",
                        'Payment Date': str(payment.payment_date),
                        'Payment Apartment': payment.apartment.name,
                        'Booking ID': payment.booking.id,
                        'Booking Apartment': payment.booking.apartment.name,
                        'Tenant': payment.booking.tenant.full_name if payment.booking.tenant else 'N/A'
                    }
                )
        
        print(f"\n✅ Found {len(mismatches)} payment apartment mismatches")
        return len(mismatches)
    
    def check_orphaned_payments(self):
        """
        Check for payments without a booking or apartment reference
        """
        print("\n" + "=" * 60)
        print("💸 ORPHANED PAYMENTS CHECK")
        print("=" * 60)
        print("Problem: Payment exists without any link to a booking OR apartment.")
        print("Why it matters: These payments cannot be attributed to any property")
        print("               or tenant, making financial tracking impossible.")
        print("               Money received but we don't know what it's for!")
        print("-" * 60)
        
        orphaned = Payment.objects.filter(
            booking__isnull=True,
            apartment__isnull=True
        ).exclude(payment_status='Completed').select_related('payment_type')
        
        example_shown = False
        for payment in orphaned:
            # Show first example
            if not example_shown:
                print("📋 EXAMPLE:")
                print(f"   Payment ID: {payment.id}")
                print(f"   Amount: ${payment.amount}")
                print(f"   Payment Date: {payment.payment_date}")
                print(f"   Status: {payment.payment_status}")
                print(f"   Type: {payment.payment_type.name if payment.payment_type else 'N/A'}")
                print(f"   Notes: {payment.notes[:100] if payment.notes else 'N/A'}")
                print(f"   Booking: None ❌")
                print(f"   Apartment: None ❌")
                print(f"   ⚠️  This payment has no connection to any booking or apartment!")
                example_shown = True
            
            self.add_issue(
                category="Orphaned Payment",
                severity="medium",
                description=f"Payment #{payment.id} has no booking or apartment reference",
                details={
                    'Payment ID': payment.id,
                    'Amount': f"${payment.amount}",
                    'Payment Date': str(payment.payment_date),
                    'Status': payment.payment_status,
                    'Type': payment.payment_type.name if payment.payment_type else 'N/A',
                    'Notes': payment.notes[:100] if payment.notes else 'N/A'
                }
            )
        
        count = orphaned.count()
        print(f"\n✅ Found {count} orphaned payments")
        return count
    
    def check_booking_date_overlaps(self):
        """
        Check for overlapping bookings in the same apartment
        """
        print("\n" + "=" * 60)
        print("📅 BOOKING DATE OVERLAPS CHECK")
        print("=" * 60)
        print("Problem: Two or more bookings for the SAME apartment have")
        print("         overlapping dates (both claim the apartment at same time).")
        print("Note: Same-day turnovers are OK (booking1 ends Aug 27, booking2")
        print("      starts Aug 27) - only TRUE overlaps are flagged.")
        print("Why it matters: Double-booking! Two tenants might show up for")
        print("               the same apartment. This is a critical issue!")
        print("-" * 60)
        
        overlaps = []
        bookings = Booking.objects.exclude(status='Blocked').select_related('apartment', 'tenant')
        
        example_shown = False
        for booking in bookings:
            if not booking.apartment:
                continue
                
            # Find overlapping bookings for the same apartment
            # Note: We use strict < and > to exclude same-day turnovers
            # (e.g., booking1 ends Aug 27, booking2 starts Aug 27 is NOT an overlap)
            overlapping = Booking.objects.filter(
                apartment=booking.apartment
            ).exclude(
                id=booking.id
            ).exclude(
                status='Blocked'
            ).filter(
                Q(start_date__lt=booking.end_date, end_date__gt=booking.start_date)
            ).select_related('tenant')
            
            for overlap in overlapping:
                # Avoid duplicate reporting
                if booking.id < overlap.id:
                    overlaps.append((booking, overlap))
                    
                    # Show first example
                    if not example_shown:
                        print("📋 EXAMPLE:")
                        print(f"   Apartment: {booking.apartment.name}")
                        print(f"   --- Booking 1 ---")
                        print(f"   ID: {booking.id}")
                        print(f"   Dates: {booking.start_date} to {booking.end_date}")
                        print(f"   Status: {booking.status}")
                        print(f"   Tenant: {booking.tenant.full_name if booking.tenant else 'N/A'}")
                        print(f"   --- Booking 2 ---")
                        print(f"   ID: {overlap.id}")
                        print(f"   Dates: {overlap.start_date} to {overlap.end_date}")
                        print(f"   Status: {overlap.status}")
                        print(f"   Tenant: {overlap.tenant.full_name if overlap.tenant else 'N/A'}")
                        print(f"   ⚠️  Both bookings claim '{booking.apartment.name}' during overlapping dates!")
                        example_shown = True
                    
                    self.add_issue(
                        category="Booking Date Overlap",
                        severity="critical",
                        description=f"Bookings #{booking.id} and #{overlap.id} overlap in {booking.apartment.name}",
                        details={
                            'Apartment': booking.apartment.name,
                            'Booking 1 ID': booking.id,
                            'Booking 1 Dates': f"{booking.start_date} to {booking.end_date}",
                            'Booking 1 Tenant': booking.tenant.full_name if booking.tenant else 'N/A',
                            'Booking 2 ID': overlap.id,
                            'Booking 2 Dates': f"{overlap.start_date} to {overlap.end_date}",
                            'Booking 2 Tenant': overlap.tenant.full_name if overlap.tenant else 'N/A'
                        }
                    )
        
        print(f"\n✅ Found {len(overlaps)} booking overlaps")
        return len(overlaps)
    
    def check_invalid_phone_numbers(self):
        """
        Check for users with invalid or missing phone numbers
        """
        print("\n" + "=" * 60)
        print("📱 USER PHONE NUMBERS CHECK")
        print("=" * 60)
        print("Problem: Tenants without phone numbers or with invalid phone format.")
        print("Why it matters: Cannot send SMS notifications, reminders, or")
        print("               contact tenants in case of emergency.")
        print("-" * 60)
        
        from mysite.models import validate_and_format_phone
        
        invalid_count = 0
        missing_count = 0
        missing_example_shown = False
        invalid_example_shown = False
        
        # Check all users with phone numbers
        users = User.objects.all()
        
        for user in users:
            if not user.phone:
                # Phone is missing
                if user.role == 'Tenant':
                    # Missing phone for tenant is more critical
                    missing_count += 1
                    
                    # Show first example
                    if not missing_example_shown:
                        print("📋 EXAMPLE (Missing Phone):")
                        print(f"   User ID: {user.id}")
                        print(f"   Name: {user.full_name}")
                        print(f"   Email: {user.email}")
                        print(f"   Role: {user.role}")
                        print(f"   Phone: None ❌")
                        print(f"   Created: {user.created_at.date()}")
                        print(f"   ⚠️  Tenant has no phone - cannot receive SMS notifications!")
                        missing_example_shown = True
                    
                    self.add_issue(
                        category="Missing Phone Number",
                        severity="medium",
                        description=f"Tenant {user.full_name} has no phone number",
                        details={
                            'User ID': user.id,
                            'Name': user.full_name,
                            'Email': user.email,
                            'Role': user.role,
                            'Created': str(user.created_at.date())
                        }
                    )
                continue
            
            # Validate phone format
            validated = validate_and_format_phone(user.phone)
            
            if validated is None:
                # Phone is invalid
                invalid_count += 1
                
                # Show first example
                if not invalid_example_shown:
                    print("📋 EXAMPLE (Invalid Phone):")
                    print(f"   User ID: {user.id}")
                    print(f"   Name: {user.full_name}")
                    print(f"   Email: {user.email}")
                    print(f"   Role: {user.role}")
                    print(f"   Phone: {user.phone} ❌")
                    print(f"   ⚠️  Phone format is invalid - SMS will fail!")
                    invalid_example_shown = True
                
                self.add_issue(
                    category="Invalid Phone Number",
                    severity="high",
                    description=f"User {user.full_name} has invalid phone: {user.phone}",
                    details={
                        'User ID': user.id,
                        'Name': user.full_name,
                        'Email': user.email,
                        'Role': user.role,
                        'Phone': user.phone,
                        'Issue': 'Does not meet E.164 standard'
                    }
                )
            elif validated != user.phone:
                # Phone needs reformatting
                self.add_issue(
                    category="Phone Needs Reformatting",
                    severity="low",
                    description=f"User {user.full_name} phone needs reformatting",
                    details={
                        'User ID': user.id,
                        'Name': user.full_name,
                        'Email': user.email,
                        'Current Phone': user.phone,
                        'Should Be': validated,
                        'Action': 'Run fix_phone_numbers command'
                    }
                )
        
        print(f"\n✅ Found {invalid_count} invalid phones, {missing_count} missing phones")
        return invalid_count + missing_count
    
    def check_merged_payments_without_key(self):
        """
        Check for payments with status 'Merged' but without a merged_payment_key
        """
        print("\n" + "=" * 60)
        print("🔗 MERGED PAYMENTS WITHOUT KEY CHECK")
        print("=" * 60)
        print("Problem: Payment has status='Merged' but no 'merged_payment_key'.")
        print("Why it matters: Merged payments should link to a parent payment.")
        print("               Without the key, we can't track which payments were")
        print("               combined, causing accounting inconsistencies.")
        print("-" * 60)
        
        # Find payments with status Merged but no merged_payment_key
        invalid_merged = Payment.objects.filter(
            payment_status='Merged'
        ).filter(
            Q(merged_payment_key__isnull=True) | Q(merged_payment_key='')
        ).select_related('booking', 'apartment', 'payment_type')
        
        example_shown = False
        for payment in invalid_merged:
            # Show first example
            if not example_shown:
                apt_name = 'N/A'
                if payment.booking and payment.booking.apartment:
                    apt_name = payment.booking.apartment.name
                elif payment.apartment:
                    apt_name = payment.apartment.name
                    
                print("📋 EXAMPLE:")
                print(f"   Payment ID: {payment.id}")
                print(f"   Amount: ${payment.amount}")
                print(f"   Payment Date: {payment.payment_date}")
                print(f"   Status: {payment.payment_status}")
                print(f"   Payment Type: {payment.payment_type.name if payment.payment_type else 'N/A'}")
                print(f"   Merged Payment Key: None ❌")
                print(f"   Booking ID: {payment.booking.id if payment.booking else 'N/A'}")
                print(f"   Apartment: {apt_name}")
                print(f"   ⚠️  Status is 'Merged' but missing merged_payment_key!")
                example_shown = True
            
            self.add_issue(
                category="Merged Payment Without Key",
                severity="high",
                description=f"Payment #{payment.id} has status 'Merged' but no merged_payment_key",
                details={
                    'Payment ID': payment.id,
                    'Amount': f"${payment.amount}",
                    'Payment Date': str(payment.payment_date),
                    'Payment Type': payment.payment_type.name if payment.payment_type else 'N/A',
                    'Booking ID': payment.booking.id if payment.booking else 'N/A',
                    'Apartment': payment.booking.apartment.name if payment.booking and payment.booking.apartment else (payment.apartment.name if payment.apartment else 'N/A'),
                    'Tenant': payment.booking.tenant.full_name if payment.booking and payment.booking.tenant else 'N/A',
                    'Issue': 'Merged payment must have merged_payment_key'
                }
            )
        
        count = invalid_merged.count()
        print(f"\n✅ Found {count} merged payments without keys")
        return count

    def check_bookings_without_apartment(self):
        """
        Check for bookings that have no apartment assigned
        """
        print("\n" + "=" * 60)
        print("🏚️  BOOKINGS WITHOUT APARTMENT CHECK")
        print("=" * 60)
        print("Problem: Booking exists but has no apartment assigned (apartment=NULL).")
        print("Why it matters: A booking without an apartment is meaningless!")
        print("               Tenant has a reservation but we don't know WHERE.")
        print("               This breaks dashboard display and reporting.")
        print("-" * 60)
        
        bookings_no_apartment = Booking.objects.filter(
            apartment__isnull=True
        ).select_related('tenant')
        
        example_shown = False
        for booking in bookings_no_apartment:
            # Show first example
            if not example_shown:
                print("📋 EXAMPLE:")
                print(f"   Booking ID: {booking.id}")
                print(f"   Start Date: {booking.start_date}")
                print(f"   End Date: {booking.end_date}")
                print(f"   Status: {booking.status}")
                print(f"   Apartment: None ❌")
                print(f"   Tenant: {booking.tenant.full_name if booking.tenant else 'N/A'}")
                print(f"   Tenant Email: {booking.tenant.email if booking.tenant else 'N/A'}")
                print(f"   Tenant Phone: {booking.tenant.phone if booking.tenant else 'N/A'}")
                print(f"   Created At: {booking.created_at}")
                print(f"   Notes: {booking.notes[:100] if booking.notes else 'N/A'}")
                print(f"   ⚠️  Booking has no apartment - where should the tenant go?!")
                example_shown = True
            
            self.add_issue(
                category="Booking Without Apartment",
                severity="critical",
                description=f"Booking #{booking.id} has no apartment assigned",
                details={
                    'Booking ID': booking.id,
                    'Start Date': str(booking.start_date),
                    'End Date': str(booking.end_date),
                    'Status': booking.status,
                    'Tenant': booking.tenant.full_name if booking.tenant else 'N/A',
                    'Tenant Email': booking.tenant.email if booking.tenant else 'N/A',
                    'Created At': str(booking.created_at),
                    'Issue': 'Every booking must have an apartment'
                }
            )
        
        count = bookings_no_apartment.count()
        print(f"\n✅ Found {count} bookings without apartment")
        return count

    
    def run_all_checks(self):
        """
        Run all integrity checks
        Returns the number of issues found
        """
        print("=" * 50)
        print("Starting Data Integrity Checks")
        print("=" * 50)
        
        # Run all check methods
        self.check_payment_apartment_mismatch()
        self.check_orphaned_payments()
        self.check_booking_date_overlaps()
        self.check_invalid_phone_numbers()
        self.check_merged_payments_without_key()
        self.check_bookings_without_apartment()
        
        print("=" * 50)
        print(f"Total issues found: {len(self.issues)}")
        print("=" * 50)
        
        return len(self.issues)
    
    def format_report(self):
        """
        Format issues into a readable report
        """
        if not self.issues:
            return "✅ No data integrity issues found!"
        
        # Group issues by severity
        critical = [i for i in self.issues if i['severity'] == 'critical']
        high = [i for i in self.issues if i['severity'] == 'high']
        medium = [i for i in self.issues if i['severity'] == 'medium']
        low = [i for i in self.issues if i['severity'] == 'low']
        
        report = f"🔍 <b>DATA INTEGRITY REPORT</b>\n\n"
        report += f"⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        report += f"📊 <b>Total Issues:</b> {len(self.issues)}\n\n"
        
        if critical:
            report += f"🚨 <b>CRITICAL ({len(critical)}):</b>\n"
            for issue in critical[:5]:  # Limit to 5 per severity
                report += self._format_issue(issue)
            if len(critical) > 5:
                report += f"   ... and {len(critical) - 5} more critical issues\n"
            report += "\n"
        
        if high:
            report += f"⚠️ <b>HIGH ({len(high)}):</b>\n"
            for issue in high[:5]:
                report += self._format_issue(issue)
            if len(high) > 5:
                report += f"   ... and {len(high) - 5} more high priority issues\n"
            report += "\n"
        
        if medium:
            report += f"📌 <b>MEDIUM ({len(medium)}):</b>\n"
            for issue in medium[:3]:
                report += self._format_issue(issue)
            if len(medium) > 3:
                report += f"   ... and {len(medium) - 3} more medium priority issues\n"
            report += "\n"
        
        if low:
            report += f"ℹ️ <b>LOW ({len(low)}):</b>\n"
            for issue in low[:2]:
                report += self._format_issue(issue)
            if len(low) > 2:
                report += f"   ... and {len(low) - 2} more low priority issues\n"
        
        return report
    
    def _format_issue(self, issue):
        """Format a single issue"""
        text = f"• {issue['description']}\n"
        if issue['details']:
            for key, value in list(issue['details'].items())[:4]:  # Limit details
                text += f"  └ {key}: {value}\n"
        return text
    
    def send_report(self):
        """
        Send the report to Telegram
        """
        if not self.issues:
            print("No issues to report - skipping Telegram notification")
            return True
        
        report = self.format_report()
        
        # Split report if too long (Telegram has 4096 char limit)
        if len(report) > 3900:
            # Send summary first
            summary = f"🔍 <b>DATA INTEGRITY REPORT</b>\n\n"
            summary += f"⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            summary += f"📊 <b>Total Issues:</b> {len(self.issues)}\n\n"
            
            critical = [i for i in self.issues if i['severity'] == 'critical']
            high = [i for i in self.issues if i['severity'] == 'high']
            medium = [i for i in self.issues if i['severity'] == 'medium']
            low = [i for i in self.issues if i['severity'] == 'low']
            
            summary += f"🚨 Critical: {len(critical)}\n"
            summary += f"⚠️ High: {len(high)}\n"
            summary += f"📌 Medium: {len(medium)}\n"
            summary += f"ℹ️ Low: {len(low)}\n\n"
            summary += f"<i>Detailed reports sent in separate messages...</i>"
            
            telegram_logger.send_telegram_message(self.chat_id, summary)
            
            # Send critical and high issues in detail
            for issue in critical + high:
                detail = f"🔍 <b>{issue['category']}</b>\n\n"
                detail += f"Severity: {issue['severity'].upper()}\n"
                detail += f"{issue['description']}\n\n"
                if issue['details']:
                    detail += "<b>Details:</b>\n"
                    for key, value in issue['details'].items():
                        detail += f"• {key}: {value}\n"
                telegram_logger.send_telegram_message(self.chat_id, detail)
        else:
            telegram_logger.send_telegram_message(self.chat_id, report)
        
        print(f"Report sent to Telegram chat {self.chat_id}")
        return True


class Command(BaseCommandWithErrorHandling):
    help = 'Check database for data integrity issues and report to Telegram'

    def add_arguments(self, parser):
        parser.add_argument(
            '--chat-id',
            type=str,
            default='288566859',
            help='Telegram chat ID to send report to (default: 288566859)'
        )
        parser.add_argument(
            '--no-send',
            action='store_true',
            help='Run checks but do not send report to Telegram'
        )

    def execute_command(self, *args, **options):
        chat_id = options.get('chat_id', '288566859')
        no_send = options.get('no_send', False)
        
        # Create checker instance
        checker = DataIntegrityChecker()
        checker.chat_id = chat_id
        
        # Run all checks
        self.stdout.write('Running data integrity checks...')
        issues_count = checker.run_all_checks()
        
        if issues_count == 0:
            self.stdout.write(self.style.SUCCESS('✅ No integrity issues found!'))
            return
        
        # Display summary
        self.stdout.write(self.style.WARNING(f'\n⚠️  Found {issues_count} integrity issues:'))
        
        critical = len([i for i in checker.issues if i['severity'] == 'critical'])
        high = len([i for i in checker.issues if i['severity'] == 'high'])
        medium = len([i for i in checker.issues if i['severity'] == 'medium'])
        low = len([i for i in checker.issues if i['severity'] == 'low'])
        
        if critical:
            self.stdout.write(self.style.ERROR(f'  🚨 Critical: {critical}'))
        if high:
            self.stdout.write(self.style.WARNING(f'  ⚠️  High: {high}'))
        if medium:
            self.stdout.write(f'  📌 Medium: {medium}')
        if low:
            self.stdout.write(f'  ℹ️  Low: {low}')
        
        # Send report to Telegram
        if not no_send:
            self.stdout.write('\nSending report to Telegram...')
            checker.send_report()
            self.stdout.write(self.style.SUCCESS(f'Report sent to chat ID: {chat_id}'))
        else:
            self.stdout.write(self.style.WARNING('Skipping Telegram notification (--no-send flag)'))



