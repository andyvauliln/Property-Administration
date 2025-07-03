# API Pricing System Updates

## Overview

The apartment pricing system has been updated from a single `price` field to a comprehensive pricing history system using the `ApartmentPrice` model. This update affects all API endpoints that deal with apartment pricing.

## Updated API Endpoints

### 1. Apartment Booking Dates API

**Endpoint:** `GET /api/apartment-booking-dates/`

**Changes:**
- Removed: `price` field (old single price)
- Added: `current_price` field (price effective today or before)
- Added: `pricing_history` array (all prices since request date)

**Response Format:**
```json
{
  "apartments": [
    {
      "apartment_id": 1,
      "current_price": 1350.00,
      "apartment_name": "630-521",
      "pricing_history": [
        {
          "price": 1350.00,
          "effective_date": "2025-01-15",
          "notes": "Current market rate"
        },
        {
          "price": 1500.00,
          "effective_date": "2025-08-01",
          "notes": "Summer rate increase"
        }
      ],
      "bookings": [
        {
          "start_date": "2025-02-01",
          "end_date": "2025-02-28",
          "status": "Confirmed"
        }
      ]
    }
  ]
}
```

### 2. Update Apartment Price by Rooms API

**Endpoint:** `POST /api/update-apartment-price-by-rooms/`

**Changes:**
- Added: `effective_date` parameter (required) - format: YYYY-MM-DD
- Added: `notes` parameter (optional) - notes about the price change
- Enhanced: Now creates/updates `ApartmentPrice` records instead of direct apartment price field

**Request Parameters:**
```json
{
  "auth_token": "your_auth_token",
  "number_of_rooms": 2,
  "new_price": 1400.00,
  "effective_date": "2025-01-15",
  "notes": "Market adjustment for 2-bedroom units"
}
```

**Response Format:**
```json
{
  "message": "Successfully processed price updates for 3 apartments with 2 rooms",
  "updated_count": 3,
  "number_of_rooms": 2,
  "new_price": 1400.00,
  "effective_date": "2025-01-15",
  "notes": "Market adjustment for 2-bedroom units",
  "price_records": [
    {
      "apartment_id": 1,
      "apartment_name": "Apartment A",
      "price": 1400.00,
      "effective_date": "2025-01-15",
      "notes": "Market adjustment for 2-bedroom units",
      "action": "created"
    },
    {
      "apartment_id": 2,
      "apartment_name": "Apartment B",
      "price": 1400.00,
      "effective_date": "2025-01-15",
      "notes": "Market adjustment for 2-bedroom units",
      "action": "updated"
    }
  ]
}
```

### 3. NEW: Update Single Apartment Price API

**Endpoint:** `POST /api/update-single-apartment-price/`

**Description:** Update or create a price record for a specific apartment on a specific date.

**Request Parameters:**
```json
{
  "auth_token": "your_auth_token",
  "apartment_id": 1,
  "new_price": 1500.00,
  "effective_date": "2025-01-15",
  "notes": "Price increase due to renovations"
}
```

**Response Format:**
```json
{
  "message": "Successfully created price for apartment 630-521",
  "apartment_id": 1,
  "apartment_name": "630-521",
  "current_price": 1350.00,
  "new_price_record": {
    "price": 1500.00,
    "effective_date": "2025-01-15",
    "notes": "Price increase due to renovations",
    "action": "created"
  },
  "pricing_history": [
    {
      "price": 1500.00,
      "effective_date": "2025-01-15",
      "notes": "Price increase due to renovations"
    },
    {
      "price": 1600.00,
      "effective_date": "2025-08-01",
      "notes": "Summer rate"
    }
  ]
}
```

## Key Features

### 1. Date-Based Pricing
- All price updates now require an `effective_date`
- Historical pricing is preserved
- Future price changes can be scheduled in advance

### 2. Price History
- Complete pricing history tracking
- All APIs return pricing data since the request date
- No more data loss when prices change

### 3. Smart Price Management
- Duplicate prevention: Only one price per apartment per date
- Update existing: If a price exists for a date, it gets updated
- Create new: If no price exists for a date, a new record is created

### 4. Enhanced Responses
- APIs now return comprehensive pricing information
- Both current price and future pricing changes
- Action indicators (created/updated) for transparency

## Migration Notes

### Breaking Changes
- **Required Parameter:** `effective_date` is now required for all price update APIs
- **Response Format:** Apartment data now includes `current_price` and `pricing_history` instead of simple `price`

### Backward Compatibility
- Old `price` field references have been replaced with `current_price` logic
- Existing apartment data maintains pricing through the new `ApartmentPrice` records
- All apartment links now use `?apartment=ID` format for filtering

## Usage Examples

### Get apartment pricing data:
```bash
curl "https://yourapi.com/api/apartment-booking-dates/?auth_token=YOUR_TOKEN&apartment_ids=1,2,3"
```

### Update prices for all 2-bedroom apartments:
```bash
curl -X POST "https://yourapi.com/api/update-apartment-price-by-rooms/" \
  -H "Content-Type: application/json" \
  -d '{
    "auth_token": "YOUR_TOKEN",
    "number_of_rooms": 2,
    "new_price": 1400.00,
    "effective_date": "2025-02-01",
    "notes": "Q1 2025 rate adjustment"
  }'
```

### Update price for a specific apartment:
```bash
curl -X POST "https://yourapi.com/api/update-single-apartment-price/" \
  -H "Content-Type: application/json" \
  -d '{
    "auth_token": "YOUR_TOKEN",
    "apartment_id": 1,
    "new_price": 1500.00,
    "effective_date": "2025-02-01",
    "notes": "Premium location adjustment"
  }'
```

## UI Integration

### Apartment Filtering
- Apartment prices page now includes dropdown filtering by apartment
- URL format: `/apartmentprice/?apartment=ID`
- Links from apartment listings direct to filtered pricing pages

### Price Display
- Current price column shows in apartment tables
- Pricing links show: "Pricing: Current: $1350.00 (1 future change) (3 total)"
- Direct navigation to apartment-specific pricing management 