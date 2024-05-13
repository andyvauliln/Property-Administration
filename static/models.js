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
            "building_n",
            "street",
            "apartment_n",
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
            "notes",
            'other_tenants'
        ],
        "options": {
            "status": [
                "Confirmed",
                "Waiting Contract",
                "Waiting Payment",
            ],
            "animals": [
                "Cat",
                "Dog",
                "Other"
            ],
            "visit_purpose": [
                "Tourism",
                "Work",
                "Between Houses",
                "Snow Bird",
                "Medical",
                "Other"
            ],

        },
        "relatedModels": {
            "apartment": "apartments",
            "tenant": "users"
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
    "paymenttypes": {
        "fields": [
            "name",
            "type",
        ],
        "options": {
            "type": [
                "In",
                "Out"
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
                "Completed",
                "Merged",
            ],
        },
        "relatedModels": {
            "payment_method": "paymentmethods",
            "payment_type": "paymenttypes",
            "bank": "paymentmethods",
            "booking": "bookings",
            "apartment": "apartments",
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
            ]
        },
        "relatedModels": {
            "booking": "bookings",
            "cleaning": "cleanings",
            "payment": "payments"
        }
    }
}
