import json
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date
from mysite.models import Payment

class Command(BaseCommand):
    help = 'Print all payments from date to date with additional data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from-date',
            type=str,
            help='Start date (YYYY-MM-DD format)',
            required=False
        )
        parser.add_argument(
            '--to-date', 
            type=str,
            help='End date (YYYY-MM-DD format)',
            required=False
        )
        parser.add_argument(
            '--json-output',
            type=str,
            help='Output JSON file path (optional)',
            required=False,
            default=None
        )
        parser.add_argument(
            '--from-last-merged',
            action='store_true',
            help='Automatically get one month of data from the last payment with status "merged" and save as JSON',
            required=False
        )

    def handle(self, *args, **kwargs):
        from_last_merged = kwargs.get('from_last_merged', False)
        
        if from_last_merged:
            # Find the last payment with status "merged"
            try:
                last_merged_payment = Payment.objects.filter(
                    payment_status='Merged'
                ).order_by('-payment_date').first()
                
                if not last_merged_payment:
                    self.stdout.write(
                        self.style.ERROR('No payments found with status "merged".')
                    )
                    return
                
                # Set start date to the last merged payment date
                from_date = last_merged_payment.payment_date
                # Set end date to one month after
                to_date = from_date + timedelta(days=30)
                
                # Auto-generate JSON filename if not provided
                json_output_path = kwargs.get('json_output')
                if not json_output_path:
                    json_output_path = f"payments_from_last_merged_{to_date.strftime('%Y%m%d')}.json"
                
                self.stdout.write(
                    self.style.SUCCESS(f"Using last merged payment date: {to_date}")
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Date range: {from_date} to {to_date}")
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Will save to: {json_output_path}")
                )
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error finding last merged payment: {str(e)}')
                )
                return
        else:
            # Use provided dates
            from_date_str = kwargs['from_date']
            to_date_str = kwargs['to_date']
            json_output_path = kwargs.get('json_output')
            
            if not from_date_str or not to_date_str:
                self.stdout.write(
                    self.style.ERROR('--from-date and --to-date are required when not using --from-last-merged')
                )
                return
            
            # Parse the dates
            try:
                from_date = parse_date(from_date_str)
                to_date = parse_date(to_date_str)
            except (ValueError, TypeError):
                self.stdout.write(
                    self.style.ERROR('Invalid date format. Please use YYYY-MM-DD format.')
                )
                return

            if not from_date or not to_date:
                self.stdout.write(
                    self.style.ERROR('Invalid date format. Please use YYYY-MM-DD format.')
                )
                return

            if from_date > to_date:
                self.stdout.write(
                    self.style.ERROR('From date cannot be later than to date.')
                )
                return

        # Query payments within the date range
        payments = Payment.objects.filter(
            payment_date__gte=from_date,
            payment_date__lte=to_date
        ).select_related(
            'payment_type',
            'apartment', 
            'booking',
            'booking__tenant',
            'booking__apartment'
        ).order_by('payment_date')

        # Print header
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS(f"Payments from {from_date} to {to_date}"))
        self.stdout.write("="*80)

        if not payments.exists():
            self.stdout.write(self.style.WARNING("No payments found in the specified date range."))
            if from_last_merged and json_output_path:
                # Still create empty JSON file
                try:
                    json_data = {"payments": []}
                    with open(json_output_path, 'w', encoding='utf-8') as json_file:
                        json.dump(json_data, json_file, indent=2, ensure_ascii=False)
                    self.stdout.write(f"\nEmpty JSON file saved to: {json_output_path}")
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error saving JSON file: {str(e)}"))
            return

        # Collect data for JSON output
        payments_data = []

        # Print each payment with additional data
        for payment in payments:
            # Create payment data dict for JSON
            payment_data = {
                "payment_id": payment.id,
                "date": str(payment.payment_date),
                "amount": float(payment.amount),
                "status": payment.payment_status,
                "key": payment.merged_payment_key
            }
            
            self.stdout.write(f"Payment ID: {payment.id}")
            self.stdout.write(f"Date: {payment.payment_date}")
            self.stdout.write(f"Amount: ${payment.amount}")
            self.stdout.write(f"Status: {payment.payment_status}")
            self.stdout.write(f"Key: {payment.merged_payment_key}")
            
            # Payment Type and Operation Type
            if payment.payment_type:
                payment_data["payment_type"] = payment.payment_type.name
                payment_data["operation_type"] = payment.payment_type.type
                self.stdout.write(f"Payment Type: {payment.payment_type.name}")
                self.stdout.write(f"Operation Type: {payment.payment_type.type}")
            
            # Apartment information
            apartment_name = ""
            apartment_keywords = ""
            if payment.apartment:
                apartment_name = payment.apartment.name
                apartment_keywords = payment.apartment.keywords or ""
            elif payment.booking and payment.booking.apartment:
                apartment_name = payment.booking.apartment.name
                apartment_keywords = payment.booking.apartment.keywords or ""
            
            payment_data["apartment_name"] = apartment_name
            payment_data["apartment_keywords"] = apartment_keywords
            
            if apartment_name:
                self.stdout.write(f"Apartment {apartment_name}")
            self.stdout.write(f'Apartment Keywords: "{apartment_keywords}"')
            
            # Payment Type Keywords
            payment_type_keywords = ""
            if payment.payment_type and payment.payment_type.keywords:
                payment_type_keywords = payment.payment_type.keywords
            payment_data["payment_type_keywords"] = payment_type_keywords
            self.stdout.write(f'Payment Type Keywords: "{payment_type_keywords}"')
            
            # Booking information
            if payment.booking:
                payment_data["booking"] = {
                    "booking_id": payment.booking.id,
                    "start_date": str(payment.booking.start_date),
                    "end_date": str(payment.booking.end_date),
                    "keywords": payment.booking.keywords or ""
                }
                
                self.stdout.write(f"Booking Period: {payment.booking.start_date} to {payment.booking.end_date}")
                
                # Booking keywords
                booking_keywords = payment.booking.keywords or ""
                self.stdout.write(f'Booking Keywords: "{booking_keywords}"')
                
                # Tenant information
                if payment.booking.tenant:
                    payment_data["tenant"] = {
                        "full_name": payment.booking.tenant.full_name,
                        "email": payment.booking.tenant.email,
                        "phone": payment.booking.tenant.phone or "",
                        "notes": getattr(payment.booking.tenant, 'notes', '') or ""
                    }
                    
                    self.stdout.write(f"Tenant Full Name: {payment.booking.tenant.full_name}")
                    self.stdout.write(f"Tenant Email: {payment.booking.tenant.email}")
                    tenant_phone = payment.booking.tenant.phone or ""
                    if tenant_phone:
                        self.stdout.write(f"Tenant Phone: {tenant_phone}")
                    if hasattr(payment.booking.tenant, 'notes') and payment.booking.tenant.notes:
                        self.stdout.write(f"Tenant Notes: {payment.booking.tenant.notes}")
            
            # Add payment data to list
            payments_data.append(payment_data)
            
            self.stdout.write("")  # Empty line separator

        # Print summary
        total_count = payments.count()
        total_amount = sum(payment.amount for payment in payments)
        
        self.stdout.write("\n" + "="*50)
        self.stdout.write(self.style.SUCCESS(f"Summary:"))
        self.stdout.write(f"Total Payments: {total_count}")
        self.stdout.write(f"Total Amount: ${total_amount}")
        self.stdout.write("="*50)

        # Save JSON file if requested or if using --from-last-merged
        if json_output_path or from_last_merged:
            if not json_output_path and from_last_merged:
                json_output_path = f"payments_from_last_merged_{to_date.strftime('%Y%m%d')}.json"
            
            try:
                json_data = {
                    # "date_range": {
                    #     "from_date": str(from_date),
                    #     "to_date": str(to_date)
                    # },
                    # "summary": {
                    #     "total_payments": total_count,
                    #     "total_amount": float(total_amount)
                    # },
                    "payments": payments_data
                }
                
                with open(json_output_path, 'w', encoding='utf-8') as json_file:
                    json.dump(json_data, json_file, indent=2, ensure_ascii=False)
                
                self.stdout.write(f"\nJSON file saved to: {json_output_path}")
                self.stdout.write(self.style.SUCCESS(f"Successfully exported {total_count} payments to JSON"))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error saving JSON file: {str(e)}"))

