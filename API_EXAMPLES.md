# Apartment Pricing API Examples

This document provides simple examples for testing the apartment pricing API endpoints.

## Configuration

Create a `.env` file in your project root with:
```bash
BASE_URL=http://localhost:8000
API_AUTH_TOKEN=your_actual_token_here
```

Or set environment variables manually:
- **Base URL**: `http://localhost:8000` (adjust for your environment)
- **Auth Token**: Replace `your_auth_token_here` with your actual token

## 1. GET Apartment Booking Dates

### Endpoint
```
GET /api/apartment-booking-dates/
```

### Example 1: Get specific apartments
```bash
curl -X GET "http://localhost:8000/api/apartment-booking-dates/?auth_token=your_auth_token_here&apartment_ids=1,2,3" \
  -H "Content-Type: application/json"
```

### Expected Response
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
        }
      ],
      "bookings": [
        {
          "booking_id": 45,
          "start_date": "2025-02-01",
          "end_date": "2025-02-28",
          "tenant_name": "John Doe",
          "status": "Confirmed"
        }
      ]
    }
  ]
}
```

## 2. Update Apartment Price by Rooms

### Endpoint
```
POST /api/update-apartment-price-by-rooms/
```

### Example 1: Update all 2-bedroom apartments
```bash
curl -X POST "http://localhost:8000/api/update-apartment-price-by-rooms/" \
  -H "Content-Type: application/json" \
  -d '{
    "auth_token": "your_auth_token_here",
    "number_of_rooms": 2,
    "new_price": 1450.00,
    "effective_date": "2025-02-01",
    "notes": "Market adjustment for Q1 2025"
  }'
```

### Expected Response
```json
{
  "success": true,
  "message": "Successfully updated prices for 3 apartments with 2 bedrooms",
  "updated_apartments": [
    {
      "apartment_id": 1,
      "apartment_name": "630-521",
      "old_price": 1350.00,
      "new_price": 1450.00,
      "effective_date": "2025-02-01",
      "action": "created"
    },
    {
      "apartment_id": 3,
      "apartment_name": "630-523",
      "old_price": 1300.00,
      "new_price": 1450.00,
      "effective_date": "2025-02-01",
      "action": "created"
    }
  ]
}
```

## 3. Update Single Apartment Price

### Endpoint
```
POST /api/update-single-apartment-price/
```

### Example 1: Update specific apartment
```bash
curl -X POST "http://localhost:8000/api/update-single-apartment-price/" \
  -H "Content-Type: application/json" \
  -d '{
    "auth_token": "your_auth_token_here",
    "apartment_id": 1,
    "new_price": 1650.00,
    "effective_date": "2025-03-01",
    "notes": "Premium location adjustment"
  }'
```

### Expected Response
```json
{
  "success": true,
  "message": "Price updated successfully for apartment 630-521",
  "apartment": {
    "apartment_id": 1,
    "apartment_name": "630-521",
    "current_price": 1450.00,
    "new_price_record": {
      "price": 1650.00,
      "effective_date": "2025-03-01",
      "notes": "Premium location adjustment",
      "action": "created"
    },
    "pricing_history_since_effective_date": [
      {
        "price": 1650.00,
        "effective_date": "2025-03-01",
        "notes": "Premium location adjustment"
      },
      {
        "price": 1450.00,
        "effective_date": "2025-02-01",
        "notes": "Market adjustment for Q1 2025"
      }
    ]
  }
}
```

## Error Responses

### Missing Authentication
```json
{
  "error": "Authentication required",
  "success": false
}
```

### Missing Required Parameters
```json
{
  "error": "Missing required parameters: effective_date",
  "success": false
}
```

### Invalid Date Format
```json
{
  "error": "Invalid date format. Use YYYY-MM-DD",
  "success": false
}
```

### Apartment Not Found
```json
{
  "error": "Apartment with ID 99999 not found",
  "success": false
}
```

## Quick Test Commands

### Load environment from .env file
```bash
# If using bash/zsh
source <(cat .env | grep -v '^#' | sed 's/^/export /')

# Or manually set environment
export BASE_URL="http://localhost:8000"
export API_AUTH_TOKEN="your_actual_token_here"
```

### Test GET endpoint
```bash
curl -X GET "${BASE_URL}/api/apartment-booking-dates/?auth_token=${API_AUTH_TOKEN}&apartment_ids=1" \
  -H "Content-Type: application/json" | jq '.'
```

### Test bulk price update
```bash
curl -X POST "${BASE_URL}/api/update-apartment-price-by-rooms/" \
  -H "Content-Type: application/json" \
  -d "{
    \"auth_token\": \"${API_AUTH_TOKEN}\",
    \"number_of_rooms\": 1,
    \"new_price\": 1200.00,
    \"effective_date\": \"$(date -d '+1 day' +%Y-%m-%d)\",
    \"notes\": \"Test price update\"
  }" | jq '.'
```

### Test single apartment update
```bash
curl -X POST "${BASE_URL}/api/update-single-apartment-price/" \
  -H "Content-Type: application/json" \
  -d "{
    \"auth_token\": \"${API_AUTH_TOKEN}\",
    \"apartment_id\": 1,
    \"new_price\": 1500.00,
    \"effective_date\": \"$(date -d '+7 days' +%Y-%m-%d)\",
    \"notes\": \"Individual apartment adjustment\"
  }" | jq '.'
```

## Notes

1. **Date Format**: Always use `YYYY-MM-DD` format for dates
2. **Price Format**: Use decimal numbers (e.g., 1450.00)
3. **Authentication**: Include auth_token in all requests
4. **JSON Formatting**: Install `jq` for better JSON output formatting
5. **Error Handling**: The API returns proper HTTP status codes and error messages 