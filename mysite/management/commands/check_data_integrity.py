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
        print("Checking payment apartment mismatches...")
        
        # Get payments that have both an apartment and a booking
        payments_with_booking = Payment.objects.filter(
            booking__isnull=False,
            apartment__isnull=False
        ).select_related('booking', 'apartment', 'booking__apartment')
        
        mismatches = []
        for payment in payments_with_booking:
            if payment.booking.apartment and payment.apartment.id != payment.booking.apartment.id:
                mismatches.append(payment)
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
        
        print(f"Found {len(mismatches)} payment apartment mismatches")
        return len(mismatches)
    
    def check_orphaned_payments(self):
        """
        Check for payments without a booking or apartment reference
        """
        print("Checking orphaned payments...")
        
        orphaned = Payment.objects.filter(
            booking__isnull=True,
            apartment__isnull=True
        ).exclude(payment_status='Completed')
        
        for payment in orphaned:
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
        print(f"Found {count} orphaned payments")
        return count
    
    def check_booking_date_overlaps(self):
        """
        Check for overlapping bookings in the same apartment
        """
        print("Checking booking date overlaps...")
        
        overlaps = []
        bookings = Booking.objects.exclude(status='Blocked').select_related('apartment', 'tenant')
        
        for booking in bookings:
            if not booking.apartment:
                continue
                
            # Find overlapping bookings for the same apartment
            overlapping = Booking.objects.filter(
                apartment=booking.apartment
            ).exclude(
                id=booking.id
            ).exclude(
                status='Blocked'
            ).filter(
                Q(start_date__lte=booking.end_date, end_date__gte=booking.start_date)
            )
            
            for overlap in overlapping:
                # Avoid duplicate reporting
                if booking.id < overlap.id:
                    overlaps.append((booking, overlap))
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
        
        print(f"Found {len(overlaps)} booking overlaps")
        return len(overlaps)
    
    def check_cleaning_without_booking(self):
        """
        Check for cleanings that don't have a valid booking reference
        """
        print("Checking cleanings without bookings...")
        
        cleanings = Cleaning.objects.filter(
            booking__isnull=True
        ).select_related('cleaner', 'apartment')
        
        for cleaning in cleanings:
            # This might be intentional for some cleanings, mark as low severity
            self.add_issue(
                category="Cleaning Without Booking",
                severity="low",
                description=f"Cleaning #{cleaning.id} has no booking reference",
                details={
                    'Cleaning ID': cleaning.id,
                    'Date': str(cleaning.date),
                    'Status': cleaning.status if hasattr(cleaning, 'status') else 'N/A',
                    'Cleaner': cleaning.cleaner.full_name if cleaning.cleaner else 'N/A',
                    'Apartment': cleaning.apartment.name if cleaning.apartment else 'N/A'
                }
            )
        
        count = cleanings.count()
        print(f"Found {count} cleanings without bookings")
        return count
    
    def check_users_without_contact(self):
        """
        Check for users without email or phone
        """
        print("Checking users without contact information...")
        
        users = User.objects.filter(
            Q(email__isnull=True) | Q(email='') | Q(phone__isnull=True) | Q(phone='')
        ).filter(is_active=True)
        
        for user in users:
            missing_fields = []
            if not user.email:
                missing_fields.append('email')
            if not user.phone:
                missing_fields.append('phone')
                
            self.add_issue(
                category="User Missing Contact Info",
                severity="medium",
                description=f"User #{user.id} ({user.full_name}) missing {', '.join(missing_fields)}",
                details={
                    'User ID': user.id,
                    'Username': user.username if hasattr(user, 'username') else 'N/A',
                    'Full Name': user.full_name,
                    'Role': user.role if hasattr(user, 'role') else 'N/A',
                    'Email': user.email or 'MISSING',
                    'Phone': user.phone or 'MISSING',
                    'Active': 'Yes' if user.is_active else 'No'
                }
            )
        
        count = users.count()
        print(f"Found {count} users without contact information")
        return count
    
    def check_pending_payments_old(self):
        """
        Check for pending payments older than 60 days
        """
        print("Checking old pending payments...")
        
        from datetime import date, timedelta
        sixty_days_ago = date.today() - timedelta(days=60)
        
        old_pending = Payment.objects.filter(
            payment_status='Pending',
            payment_date__lt=sixty_days_ago
        ).select_related('booking', 'apartment')
        
        for payment in old_pending:
            self.add_issue(
                category="Old Pending Payment",
                severity="high",
                description=f"Payment #{payment.id} pending for over 60 days",
                details={
                    'Payment ID': payment.id,
                    'Amount': f"${payment.amount}",
                    'Payment Date': str(payment.payment_date),
                    'Days Pending': (date.today() - payment.payment_date).days,
                    'Apartment': payment.apartment.name if payment.apartment else (
                        payment.booking.apartment.name if payment.booking and payment.booking.apartment else 'N/A'
                    ),
                    'Tenant': payment.booking.tenant.full_name if payment.booking and payment.booking.tenant else 'N/A'
                }
            )
        
        count = old_pending.count()
        print(f"Found {count} old pending payments")
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
        self.check_cleaning_without_booking()
        self.check_users_without_contact()
        self.check_pending_payments_old()
        
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



