# Booking API — input / output

All Endpoints Need `auth_token`

| Where | How |
|-------|-----|
| GET | query `?auth_token=…` |
| POST / PATCH | JSON body `"auth_token": "…"` |

**Errors**

| Status | Body |
|--------|------|
| 401 | `{"error": "Invalid or missing authentication token"}` |
| 404 | `{"error": "…"}` (e.g. booking/payment/apartment not found) |
| 400 | `{"field_name": ["…"]}` (validation) or `{"error": "…"}` (bad params) |

---

## GET `/api/apartment-booking-dates/`

**Query**

| Field | Required |
|-------|----------|
| `auth_token` | yes |
| `apartment_ids` | no — comma-separated apartment PKs; if omitted → empty list |

**Response**

```json
{
  "apartments": [
    {
      "apartment_id": 0,
      "default_price": 0,
      "apartment_name": "",
      "rating": null,
      "rating_surcharge_per_day": null,
      "pricing_history": [
        { "price": 0, "base_price": 0, "effective_date": "YYYY-MM-DD", "notes": "" }
      ],
      "bookings": []
    }
  ]
}
```

Each `bookings[]` item uses the same shape as **`booking`** in **POST `/api/bookings/`** (201).

---


## POST `/api/bookings/` (create)

Top-level keys (except `auth_token` and `payments`) are validated and saved like a new booking in the app. On save, `source` is always `Rental Guru`; a client-sent `source` is ignored.

**Body**

| Field | Required |
|-------|----------|
| `auth_token` | yes |
| `apartment` | yes (apartment PK, string or number) |
| `start_date` | yes |
| `end_date` | yes |
| `tenant_email` | yes (required unless status is Blocked / Pending / Problem Booking) |
| `tenant_phone` | yes if `create_chat` is true; omit `create_chat` or send falsey to skip |
| `payments` | no — array of objects (see below) |
| `status` | no |
| `tenant_full_name` | no |
| `source_id` | no (external id; must be unique if set) |
| `notes` | no |
| `keywords` | no |
| `other_tenants` | no |
| `tenants_n` | no |
| `animals` | no |
| `visit_purpose` | no |
| `is_rent_car` | no |
| `car_model` | no |
| `car_price` | no |
| `car_rent_days` | no |
| `parking_number` | no |
| `create_chat` | no |

**`payments[]` item** (when creating/updating rows with the booking)

| Field | Required | Default if omitted |
|-------|----------|-------------------|
| `payment_date` | yes (for a real row) | `""` (empty slot) |
| `amount` | yes (non-zero) | `0` |
| `payment_type` | yes (PaymenType id) | `""` (invalid) |
| `payment_status` | no | `Pending` |
| `payment_notes` / `notes` | no | `""` |
| `payment_id` | no | `""` (new row); set to existing payment PK to update |
| `number_of_months` | no | `1` |

**Response 201**

```json
{
  "booking": {
    "id": 0,
    "apartment_id": 0,
    "contract_url": "",
    "contract_id": "",
    "contract_send_status": "",
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "tenants_n": "",
    "status": "",
    "tenant_id": null,
    "tenant_email": "",
    "tenant_full_name": "",
    "tenant_phone": "",
    "source": "Rental Guru",
    "source_id": null,
    "payments": []
  }
}
```

Each `payments[]` item uses the same shape as **`payment`** in **POST `/api/payments/`** (201).

---

## PATCH `/api/bookings/<id>/`

Updates the booking with that `id`. Send **only the fields you want to change** (plus `auth_token`). Omitted scalars keep their **current stored values**. `source` is always saved as `Rental Guru`. Omit **`payments`** to leave existing payments unchanged; if you send **`payments`**, use the same object shape as **POST `/api/bookings/`** (see **`payments[]` item** above).

**Body**

| Field | Required | If you omit it in JSON |
|-------|----------|-------------------------|
| `auth_token` | yes | — |
| `apartment` | no | stays as it is now |
| `start_date` | no | stays as it is now |
| `end_date` | no | stays as it is now |
| `tenant_email` | no | stays as it is now |
| `tenant_full_name` | no | stays as it is now |
| `tenant_phone` | no | stays as it is now |
| `status` | no | stays as it is now |
| `source_id` | no | stays as it is now |
| `notes` | no | stays as it is now |
| `keywords` | no | stays as it is now |
| `other_tenants` | no | stays as it is now |
| `tenants_n` | no | stays as it is now |
| `animals` | no | stays as it is now |
| `visit_purpose` | no | stays as it is now |
| `is_rent_car` | no | stays as it is now |
| `car_model` | no | stays as it is now |
| `car_price` | no | stays as it is now |
| `car_rent_days` | no | stays as it is now |
| `parking_number` | no | stays as it is now |
| `create_chat` | no | stays as it is now |
| `payments` | no | existing payments are **not** changed |

**Response 200:** `{"booking": { … }}` — same structure as **POST `/api/bookings/`** (201).

---

## PATCH `/api/bookings/by-source-id/<source_id>/`

Same as **PATCH `/api/bookings/<id>/`**, but the booking is found by `source=Rental Guru` and `source_id` from the URL path (not by numeric id).

**Body:** same table as above.

**Response 200:** `{"booking": { … }}` — same structure as **POST `/api/bookings/`** (201).

---

## POST `/api/payments/` (create)

Server sets `source` to `Rental Guru`. Any client `source` in the body is overwritten.

**Body**

| Field | Required | Default if omitted |
|-------|----------|-------------------|
| `auth_token` | yes | — |
| `payment_date` | yes | — |
| `amount` | yes (non-zero; negatives are stored as positive) | — |
| `payment_type` | yes (PaymenType id) | — |
| `booking` | no | yes |
| `payment_method` | no | `null` (send PaymentMethod id in JSON if you want a method) |
| `bank` | no | `null` (send bank id in JSON if you want a bank) |
| `payment_status` | no | `Pending` |
| `number_of_months` | no | treated as **`0`** → one payment row (no multi-month split) |
| `notes` | no | `""` |
| `tenant_notes` | no | `""` |
| `keywords` | no | `""` |
| `invoice_url` | no | `""` |
| `source_id` | no | `null` (empty string stored as `null`) |


**Response 201**

```json
{
  "payment": {
    "id": 0,
    "booking_id": null,
    "apartment_id": null,
    "payment_date": "YYYY-MM-DD",
    "amount": "0.00",
    "payment_status": "Pending",
    "payment_type_id": 0,
    "payment_type": { "id": 0, "name": "", "type": "", "category": "", "balance_sheet_name": "", "keywords": "" },
    "payment_method_id": null,
    "payment_method": null,
    "bank_id": null,
    "bank": null,
    "invoice_url": "",
    "notes": "",
    "tenant_notes": "",
    "keywords": "",
    "merged_payment_key": "",
    "source": "Rental Guru",
    "source_id": null,
    "created_by": "",
    "last_updated_by": "",
    "created_at": "",
    "updated_at": ""
  }
}
```

---

## PATCH `/api/payments/<id>/`

Updates the payment with that `id`. You can send **only the fields you want to change** (plus `auth_token`). `source` is always stored as `Rental Guru`.

**Body**

| Field | Required | If you omit it in JSON |
|-------|----------|-------------------------|
| `auth_token` | yes | — |
| `payment_date` | no | stays as it is now in the database |
| `amount` | no | stays as it is now |
| `payment_type` | no | stays as it is now |
| `booking` | no | stays as it is now |
| `apartment` | no | stays as it is now |
| `payment_method` | no | stays as it is now |
| `bank` | no | stays as it is now |
| `payment_status` | no | stays as it is now |
| `number_of_months` | no | server uses **`0`** if omitted (single-row update; send a value only if you need multi-month split) |
| `notes` | no | stays as it is now |
| `tenant_notes` | no | stays as it is now |
| `keywords` | no | stays as it is now |
| `invoice_url` | no | stays as it is now |
| `source_id` | no | stays as it is now |

**Response 200:** `{"payment": { … }}` — same structure as **POST `/api/payments/`** (201).

---

