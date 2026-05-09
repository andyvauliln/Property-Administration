import json
import os
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from mysite.models import Apartment, Booking, Payment, PaymentMethod, PaymenType
from mysite.views.booking_api import RENTAL_GURU_SOURCE


SMOKE_BOOKING_SOURCE_ID = 'rental-guru-api-smoke-booking'
SMOKE_PAYMENT_SOURCE_ID = 'rental-guru-api-smoke-payment'


class Command(BaseCommand):
    help = (
        'Real DB: delete smoke booking/payment (fixed source_ids), then create/update via Rental Guru API. '
        'Requires API_AUTH_TOKEN. Default apartment 19.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apartment-id',
            type=int,
            default=19,
            help='Apartment pk (default 19)',
        )

    def handle(self, *args, **options):
        token = os.environ.get('API_AUTH_TOKEN', '').strip()
        if not token:
            raise CommandError('Set API_AUTH_TOKEN in the environment (same token the API expects).')

        apt_id = options['apartment_id']
        try:
            apt = Apartment.objects.get(pk=apt_id)
        except Apartment.DoesNotExist:
            raise CommandError(f'Apartment id={apt_id} does not exist.')
        if apt.status == 'Unavailable':
            raise CommandError(f'Apartment id={apt_id} is Unavailable.')

        pt = PaymenType.objects.first()
        if not pt:
            raise CommandError('No PaymenType in database.')
        pm = PaymentMethod.objects.filter(type='Payment Method').first()
        bank = PaymentMethod.objects.filter(type='Bank').first()
        if not pm or not bank:
            raise CommandError('Need PaymentMethod rows (Payment Method + Bank).')

        n_booking, _ = Booking.objects.filter(
            source=RENTAL_GURU_SOURCE,
            source_id=SMOKE_BOOKING_SOURCE_ID,
        ).delete()
        n_pay, _ = Payment.objects.filter(
            source=RENTAL_GURU_SOURCE,
            source_id=SMOKE_PAYMENT_SOURCE_ID,
        ).delete()
        self.stdout.write(
            self.style.WARNING(
                f'Removed prior smoke rows: bookings={n_booking}, standalone payments={n_pay}.'
            )
        )

        start = date.today() + timedelta(days=400)
        end = start + timedelta(days=60)
        start2 = start + timedelta(days=1)
        end2 = end + timedelta(days=1)

        client = APIClient()

        def post_json(name, url, body):
            r = client.post(url, data=body, format='json')
            if r.status_code >= 400:
                raise CommandError(f'{name} POST {r.status_code}: {r.data}')
            return r

        def patch_json(name, url, body):
            r = client.patch(url, data=body, format='json')
            if r.status_code >= 400:
                raise CommandError(f'{name} PATCH {r.status_code}: {r.data}')
            return r

        create_body = {
            'auth_token': token,
            'apartment': str(apt_id),
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'status': 'Confirmed',
            'notes': 'smoke create',
            'keywords': 'rg-smoke',
            'tenant_email': 'rg-smoke-tenant@example.com',
            'tenant_full_name': 'RG Smoke Tenant',
            'tenant_phone': '+12025551234',
            'source_id': SMOKE_BOOKING_SOURCE_ID,
            'payments': [
                {
                    'payment_date': start.isoformat(),
                    'amount': 100,
                    'payment_type': str(pt.pk),
                    'notes': 'smoke embedded payment',
                    'payment_status': 'Pending',
                    'payment_id': '',
                    'number_of_months': 1,
                },
            ],
        }
        r0 = post_json(
            'create_booking',
            reverse('api_rental_guru_create_booking'),
            create_body,
        )
        if r0.status_code != status.HTTP_201_CREATED:
            raise CommandError(f'Expected 201, got {r0.status_code}')
        booking = r0.data['booking']
        booking_id = booking['id']
        pay_embedded_id = booking['payments'][0]['id'] if booking.get('payments') else None

        patch_booking_body = {
            'auth_token': token,
            'notes': 'smoke after patch',
            'start_date': start2.isoformat(),
            'end_date': end2.isoformat(),
        }
        r1 = patch_json(
            'update_booking',
            reverse('api_rental_guru_update_booking', kwargs={'pk': booking_id}),
            patch_booking_body,
        )
        if r1.status_code != status.HTTP_200_OK:
            raise CommandError(f'Expected 200, got {r1.status_code}')

        pay_body = {
            'auth_token': token,
            'payment_date': start.isoformat(),
            'amount': '250.00',
            'number_of_months': 0,
            'payment_status': 'Pending',
            'payment_method': str(pm.pk),
            'payment_type': str(pt.pk),
            'bank': str(bank.pk),
            'booking': str(booking_id),
            'notes': 'smoke standalone payment',
            'source_id': SMOKE_PAYMENT_SOURCE_ID,
        }
        r2 = post_json(
            'create_payment',
            reverse('api_rental_guru_create_payment'),
            pay_body,
        )
        if r2.status_code != status.HTTP_201_CREATED:
            raise CommandError(f'Expected 201, got {r2.status_code}')
        pay_id = r2.data['payment']['id']

        patch_pay_body = {
            'auth_token': token,
            'amount': '275.00',
            'payment_status': 'Completed',
            'notes': 'smoke payment patched',
        }
        r3 = patch_json(
            'update_payment',
            reverse('api_rental_guru_update_payment', kwargs={'pk': pay_id}),
            patch_pay_body,
        )
        if r3.status_code != status.HTTP_200_OK:
            raise CommandError(f'Expected 200, got {r3.status_code}')

        out = {
            'booking_id': booking_id,
            'booking_source_id': SMOKE_BOOKING_SOURCE_ID,
            'embedded_payment_id': pay_embedded_id,
            'standalone_payment_id': pay_id,
            'standalone_payment_source_id': SMOKE_PAYMENT_SOURCE_ID,
            'booking_after_patch': r1.data.get('booking', {}),
            'payment_after_patch': r3.data.get('payment', {}),
        }
        self.stdout.write(self.style.SUCCESS(json.dumps(out, indent=2, default=str)))
