# API Testing Files

This directory contains comprehensive test files for the Apartment Pricing API endpoints.

## Files Overview

### 1. `test_api_calls.py` - Python Test Script
- **Purpose**: Comprehensive Python-based testing with detailed output
- **Dependencies**: `requests` library
- **Features**: 
  - Detailed test scenarios with success/failure cases
  - JSON response formatting
  - Error handling validation
  - Complete workflow testing

**Usage:**
```bash
# Install dependencies
pip install requests python-dotenv

# Create .env file with your configuration (see Configuration section below)

# Run tests
python test_api_calls.py
```

### 2. `test_api_curl.sh` - Bash/Curl Test Script
- **Purpose**: Command-line testing using curl
- **Dependencies**: `curl`, `jq` (optional for pretty JSON)
- **Features**:
  - Color-coded output
  - All test scenarios from Python script
  - Shell-based automation
  - Works on Linux/Mac

**Usage:**
```bash
# Make executable (if not already)
chmod +x test_api_curl.sh

# Create .env file with your configuration (see Configuration section below)
# Then run:
./test_api_curl.sh
```

### 3. `API_EXAMPLES.md` - Quick Reference
- **Purpose**: Simple copy-paste API examples
- **Features**:
  - Exact curl commands
  - Expected responses
  - Error examples
  - Quick test commands

## Configuration

### Create a .env file

Create a `.env` file in your project root with the following content:

```bash
# API Configuration
BASE_URL=http://localhost:8000
API_AUTH_TOKEN=your_actual_token_here

# Optional: Django settings
DEBUG=True
SECRET_KEY=your_secret_key_here
```

### Configuration Values

Before running any tests, update these values in your `.env` file:

1. **BASE_URL**: Change from `http://localhost:8000` to your server URL
2. **API_AUTH_TOKEN**: Replace `your_actual_token_here` with your actual authentication token
3. **APARTMENT_IDS**: Adjust apartment IDs (1, 2, 3) in the test scripts to match your actual data

### Alternative: Environment Variables

If you prefer not to use a `.env` file, you can set environment variables directly:

```bash
export BASE_URL="http://localhost:8000"
export API_AUTH_TOKEN="your_actual_token_here"
```

## Test Scenarios Covered

### GET Endpoint Tests
- ✅ Valid requests with specific apartment IDs
- ✅ Empty requests (should return empty response)
- ✅ Missing authentication (should fail)

### POST Bulk Update Tests
- ✅ Valid bulk price updates by room count
- ✅ Missing required parameters (should fail)
- ✅ Invalid date formats (should fail)
- ✅ Missing authentication (should fail)

### POST Single Update Tests
- ✅ Valid single apartment price updates
- ✅ Updating existing price records
- ✅ Non-existent apartment IDs (should fail)
- ✅ Missing required parameters (should fail)

### Workflow Tests
- ✅ Complete workflow: Create price → Retrieve data
- ✅ Verify pricing history is returned correctly

## Expected Results

Some tests are **designed to fail** to validate error handling:
- Requests without auth tokens
- Invalid date formats
- Missing required parameters
- Non-existent apartment IDs

This is normal and demonstrates proper API error handling.

## Troubleshooting

### Common Issues

1. **Connection refused**: Check if your Django server is running
2. **Authentication failed**: Verify your AUTH_TOKEN is correct
3. **Invalid JSON**: Install `jq` for better JSON formatting: `brew install jq` (Mac) or `apt-get install jq` (Linux)
4. **Permission denied**: Run `chmod +x test_api_curl.sh` to make the script executable

### Debug Steps

1. Check if Django server is running:
   ```bash
   curl -I http://localhost:8000/admin/
   ```

2. Test with a simple GET request:
   ```bash
   curl "http://localhost:8000/api/apartment-booking-dates/?auth_token=your_token&apartment_ids=1"
   ```

3. Check Django logs for error messages

## API Endpoints Tested

1. **GET** `/api/apartment-booking-dates/`
   - Returns apartment data with current prices and pricing history
   - Includes booking information

2. **POST** `/api/update-apartment-price-by-rooms/`
   - Updates prices for all apartments with specified bedroom count
   - Creates date-based pricing records

3. **POST** `/api/update-single-apartment-price/`
   - Updates price for a specific apartment
   - Returns comprehensive pricing history

## Next Steps

After running the tests:
1. Review the responses to understand the API structure
2. Adjust apartment IDs and room counts based on your actual data
3. Integrate these API calls into your application
4. Set up proper authentication for production use 