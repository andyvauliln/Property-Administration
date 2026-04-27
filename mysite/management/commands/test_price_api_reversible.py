import json
import os
from datetime import date, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
import requests

from mysite.models import Apartment, ApartmentPrice


class Command(BaseCommand):
    help = (
        "Apply smoke updates for apartment price APIs and inspect results. "
        "Run later with --reverse to restore original DB state from saved snapshot."
    )

    SNAPSHOT_FILE = Path("project_data/test_price_api_reversible_snapshot.json")

    def add_arguments(self, parser):
        parser.add_argument(
            "--apartment-id",
            type=int,
            default=None,
            help="Apartment ID for single update test (default: first apartment by id).",
        )
        parser.add_argument(
            "--rooms",
            type=int,
            default=None,
            help="Room count for by-rooms test (default: bedrooms of selected apartment).",
        )
        parser.add_argument(
            "--single-price",
            type=float,
            default=1234.56,
            help="Temporary price used for single update test.",
        )
        parser.add_argument(
            "--rooms-price",
            type=float,
            default=987.65,
            help="Temporary price used for by-rooms update test.",
        )
        parser.add_argument(
            "--single-days-ahead",
            type=int,
            default=7,
            help="Effective date offset (days) for single update test.",
        )
        parser.add_argument(
            "--rooms-days-ahead",
            type=int,
            default=8,
            help="Effective date offset (days) for by-rooms update test.",
        )
        parser.add_argument(
            "--reverse",
            action="store_true",
            help="Restore DB state from the latest saved snapshot (no API calls).",
        )
        parser.add_argument(
            "--base-url",
            type=str,
            default="http://68.183.124.79",
            help="Base URL for real API calls (default: http://68.183.124.79).",
        )

    @staticmethod
    def _snapshot_state(apartments, effective_date):
        snapshot = {}
        for apartment in apartments:
            row = ApartmentPrice.objects.filter(
                apartment=apartment,
                effective_date=effective_date,
            ).first()
            snapshot[apartment.id] = {
                "exists": bool(row),
                "price": float(row.price) if row else None,
                "notes": row.notes if row else None,
            }
        return snapshot

    @staticmethod
    def _restore_state(apartments, effective_date, snapshot):
        for apartment in apartments:
            before = snapshot[apartment.id]
            current = ApartmentPrice.objects.filter(
                apartment=apartment,
                effective_date=effective_date,
            ).first()
            if before["exists"]:
                if current is None:
                    ApartmentPrice.objects.create(
                        apartment=apartment,
                        effective_date=effective_date,
                        price=before["price"],
                        notes=before["notes"],
                    )
                else:
                    current.price = before["price"]
                    current.notes = before["notes"]
                    current.save()
            else:
                if current is not None:
                    current.delete()

    @staticmethod
    def _validate_restore(apartments, effective_date, snapshot):
        for apartment in apartments:
            before = snapshot[apartment.id]
            current = ApartmentPrice.objects.filter(
                apartment=apartment,
                effective_date=effective_date,
            ).first()
            if before["exists"]:
                if current is None:
                    return False
                if float(current.price) != before["price"] or current.notes != before["notes"]:
                    return False
            else:
                if current is not None:
                    return False
        return True

    @classmethod
    def _save_snapshot_file(cls, payload):
        cls.SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        cls.SNAPSHOT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def _load_snapshot_file(cls):
        if not cls.SNAPSHOT_FILE.exists():
            raise CommandError(
                f"No snapshot file found at {cls.SNAPSHOT_FILE}. "
                "Run command first without --reverse."
            )
        return json.loads(cls.SNAPSHOT_FILE.read_text(encoding="utf-8"))

    @classmethod
    def _delete_snapshot_file(cls):
        if cls.SNAPSHOT_FILE.exists():
            cls.SNAPSHOT_FILE.unlink()

    def handle(self, *args, **options):
        if options["reverse"]:
            return self._handle_reverse()
        return self._handle_apply(options)

    def _handle_apply(self, options):
        token = os.environ.get("API_AUTH_TOKEN", "").strip()
        if not token:
            raise CommandError("Set API_AUTH_TOKEN in environment before running this command.")
        base_url = options["base_url"].rstrip("/")

        apartment_id = options["apartment_id"]
        if apartment_id is None:
            apartment = Apartment.objects.order_by("id").first()
        else:
            apartment = Apartment.objects.filter(id=apartment_id).first()
        if not apartment:
            raise CommandError("No apartment found for single update test.")

        rooms = options["rooms"] if options["rooms"] is not None else apartment.bedrooms
        if rooms is None:
            raise CommandError("Selected apartment has no bedrooms value; pass --rooms explicitly.")

        single_date = date.today() + timedelta(days=options["single_days_ahead"])
        rooms_date = date.today() + timedelta(days=options["rooms_days_ahead"])
        single_price = options["single_price"]
        rooms_price = options["rooms_price"]

        single_note = "temp api test"
        rooms_note = "temp rooms api test"

        rooms_apartments = list(Apartment.objects.filter(bedrooms=rooms).order_by("id"))
        if not rooms_apartments:
            raise CommandError(f"No apartments found for rooms={rooms}.")

        single_snapshot = self._snapshot_state([apartment], single_date)
        rooms_snapshot = self._snapshot_state(rooms_apartments, rooms_date)

        result = {
            "mode": "apply",
            "single_update": {},
            "by_rooms_update": {},
            "snapshot_file": str(self.SNAPSHOT_FILE),
            "base_url": base_url,
        }

        single_payload = {
            "auth_token": token,
            "apartment_id": apartment.id,
            "new_price": single_price,
            "effective_date": single_date.isoformat(),
            "notes": single_note,
        }
        single_url = f"{base_url}{reverse('update_single_apartment_price')}"
        r_single = requests.post(single_url, json=single_payload, timeout=20)
        try:
            single_data = r_single.json()
        except ValueError:
            single_data = {"raw": r_single.text}
        if r_single.status_code >= 400:
            raise CommandError(f"Single update API failed: {r_single.status_code} {single_data}")

        single_after = ApartmentPrice.objects.filter(
            apartment=apartment, effective_date=single_date
        ).first()
        result["single_update"] = {
            "status_code": r_single.status_code,
            "url": single_url,
            "apartment_id": apartment.id,
            "effective_date": single_date.isoformat(),
            "record_exists_after_call": bool(single_after),
            "price_after_call": float(single_after.price) if single_after else None,
            "api_message": single_data.get("message") if isinstance(single_data, dict) else None,
        }

        rooms_payload = {
            "auth_token": token,
            "number_of_rooms": rooms,
            "new_price": rooms_price,
            "effective_date": rooms_date.isoformat(),
            "notes": rooms_note,
        }
        rooms_url = f"{base_url}{reverse('update_apartment_price_by_rooms')}"
        r_rooms = requests.post(rooms_url, json=rooms_payload, timeout=20)
        try:
            rooms_data = r_rooms.json()
        except ValueError:
            rooms_data = {"raw": r_rooms.text}
        if r_rooms.status_code >= 400:
            raise CommandError(f"By-rooms API failed: {r_rooms.status_code} {rooms_data}")

        sample_apartment = rooms_apartments[0]
        sample_after = ApartmentPrice.objects.filter(
            apartment=sample_apartment, effective_date=rooms_date
        ).first()
        result["by_rooms_update"] = {
            "status_code": r_rooms.status_code,
            "url": rooms_url,
            "rooms": rooms,
            "matched_apartments": len(rooms_apartments),
            "updated_count_response": rooms_data.get("updated_count")
            if isinstance(rooms_data, dict)
            else None,
            "sample_apartment_id": sample_apartment.id,
            "sample_price_after_call": float(sample_after.price) if sample_after else None,
            "api_message": rooms_data.get("message") if isinstance(rooms_data, dict) else None,
        }

        snapshot_payload = {
            "single": {
                "apartment_ids": [apartment.id],
                "effective_date": single_date.isoformat(),
                "snapshot": single_snapshot,
            },
            "rooms": {
                "apartment_ids": [a.id for a in rooms_apartments],
                "effective_date": rooms_date.isoformat(),
                "snapshot": rooms_snapshot,
            },
        }
        self._save_snapshot_file(snapshot_payload)

        self.stdout.write(self.style.SUCCESS(json.dumps(result, indent=2, default=str)))

    def _handle_reverse(self):
        snapshot_payload = self._load_snapshot_file()

        single_apartment_ids = snapshot_payload["single"]["apartment_ids"]
        single_date = date.fromisoformat(snapshot_payload["single"]["effective_date"])
        single_snapshot = snapshot_payload["single"]["snapshot"]
        single_apartments = list(Apartment.objects.filter(id__in=single_apartment_ids).order_by("id"))
        if len(single_apartments) != len(single_apartment_ids):
            raise CommandError("Cannot reverse: some single-test apartments no longer exist.")

        rooms_apartment_ids = snapshot_payload["rooms"]["apartment_ids"]
        rooms_date = date.fromisoformat(snapshot_payload["rooms"]["effective_date"])
        rooms_snapshot = snapshot_payload["rooms"]["snapshot"]
        rooms_apartments = list(Apartment.objects.filter(id__in=rooms_apartment_ids).order_by("id"))
        if len(rooms_apartments) != len(rooms_apartment_ids):
            raise CommandError("Cannot reverse: some rooms-test apartments no longer exist.")

        # JSON keys are strings; convert to int to match model IDs used by helpers.
        single_snapshot = {int(k): v for k, v in single_snapshot.items()}
        rooms_snapshot = {int(k): v for k, v in rooms_snapshot.items()}

        self._restore_state(single_apartments, single_date, single_snapshot)
        self._restore_state(rooms_apartments, rooms_date, rooms_snapshot)

        result = {
            "mode": "reverse",
            "snapshot_file": str(self.SNAPSHOT_FILE),
            "restore": {
                "single_reverted": self._validate_restore(single_apartments, single_date, single_snapshot),
                "rooms_reverted": self._validate_restore(rooms_apartments, rooms_date, rooms_snapshot),
            },
        }
        if not result["restore"]["single_reverted"] or not result["restore"]["rooms_reverted"]:
            raise CommandError(f"Reverse ran, but revert check failed: {json.dumps(result, default=str)}")

        self._delete_snapshot_file()
        self.stdout.write(self.style.SUCCESS(json.dumps(result, indent=2, default=str)))
