var modelsData = {
    "users": {
        "fields": [
            "email",
            "full_name",
            "phone",
            "role",
            "notes"
        ],
        "options": {
            "role": [
                "Admin",
                "Cleaner",
                "Manager",
                "Tenant",
                "Owner"
            ]
        },
        "relatedModels": {}
    },
    "apartments": {
        "fields": [
            "name",
            "web_link",
            "house_number",
            "street",
            "room",
            "state",
            "city",
            "zip_index",
            "bedrooms",
            "bathrooms",
            "apartment_type",
            "status",
            "notes"
        ],
        "options": {
            "apartment_type": [
                "In Management",
                "In Ownership"
            ],
            "status": [
                "Available",
                "Unavailable"
            ]
        },
        "relatedModels": {
            "manager": "users",
            "owner": "users"
        }
    },
    "bookings": {
        "fields": [
            "start_date",
            "end_date",
            "status",
            "notes"
        ],
        "options": {
            "status": [
                "Confirmed",
                "Canceled",
                "Pending"
            ]
        },
        "relatedModels": {
            "apartment": "apartments",
            "tenant": "users"
        }
    },
    "contracts": {
        "fields": [
            "contract_id",
            "sign_date",
            "link",
            "status"
        ],
        "options": {
            "status": [
                "Signed",
                "Pending",
                "Canceled"
            ]
        },
        "relatedModels": {
            "booking": "bookings"
        }
    },
    "paymentmethods": {
        "fields": [
            "name",
            "type",
            "notes"
        ],
        "options": {
            "type": [
                "Payment Method",
                "Bank"
            ]
        },
        "relatedModels": {}
    },
    "payments": {
        "fields": [
            "payment_date",
            "amount",
            "payment_type",
            "payment_status",
            "notes"
        ],
        "options": {
            "payment_status": [
                "Pending",
                "Received",
                "Cancelled"
            ],
            "payment_type": [
                "Income",
                "Outcome",
                "Damage Deposit",
                "Hold Deposit",
                "Booking"
            ]
        },
        "relatedModels": {
            "payment_method": "paymentmethods",
            "bank": "paymentmethods",
            "booking": "bookings"
        }
    },
    "cleanings": {
        "fields": [
            "date",
            "status",
            "tasks",
            "notes"
        ],
        "options": {
            "status": [
                "Scheduled",
                "Completed",
                "Canceled"
            ]
        },
        "relatedModels": {
            "cleaner": "users",
            "booking": "bookings"
        }
    },
    "notifications": {
        "fields": [
            "date",
            "status",
            "send_in_telegram",
            "message"
        ],
        "options": {
            "status": [
                "Done",
                "Pending",
                "Canceled"
            ]
        },
        "relatedModels": {
            "booking": "bookings"
        }
    }
}
