# Payment Sync V2 - Testing Guide

## Overview
Payment Sync V2 is a completely rebuilt payment matching system with improved algorithms, better UI/UX, and more features.

## Key Features

### 1. Intelligent Matching Algorithm
- **Multi-factor scoring system** - Combines amount, date, keywords, apartment, and notes matching
- **Confidence levels** - Automatically categorizes matches as High, Medium, or Low confidence
- **Weighted scoring** - More important factors (amount, date, keywords) contribute more to the match score


### 3. Features
- **Quick merge** - Merge individual payments directly from the list
- **Search and filter** - Find payments by amount, date, notes, apartment

## Testing Commands

### 1. Access the New System
```bash
# Visit the new payment sync page
http://localhost:8000/payments-sync-v2/
```

### 2. Upload Test CSV
```bash
# Prepare a CSV file with columns: Date, Description, Amount, Running Bal.
# Example format:
# 01/15/2025,Rent Payment John Doe,1500.00,5000.00
# 01/16/2025,Check 283,-450.00,4550.00
```

### 3. Test Different Scenarios

#### Scenario A: High Confidence Match
- Upload CSV with payment that exactly matches DB payment (same amount, close date)
- Should show in "High Confidence" tab with green badge
- Use "Quick Merge" or "Auto-merge high confidence"

#### Scenario B: Medium Confidence Match
- Upload CSV with payment that partially matches (amount matches but date is off by 3-4 days)
- Should show in "Medium Confidence" tab with yellow badge
- Review and manually merge if appropriate

#### Scenario C: No Match
- Upload CSV with completely new payment
- Should show in "Low/No Match" tab with red badge
- Click "Create New Payment" to add to database

#### Scenario D: Multiple Matches
- Upload CSV where one file payment matches multiple DB payments
- Expand "Show X more potential matches" to review all options
- Select the correct match and merge

### 4. Test Search and Filter
```bash
# Search by amount
Search: 1500

# Search by date
Search: 01/15

# Search by apartment
Search: Apartment A

# Search by notes
Search: rent payment
```

### 5. Test Batch Operations
1. Review multiple payments
2. Use "Quick Merge" on several payments
3. Review the "Payments Ready to Save" section
4. Click "Save All X Payments to Database"
5. Verify payments are saved with correct data

## Key Improvements Over V1

### Algorithm
- ✅ Comprehensive keyword matching (tenant name, email, phone)
- ✅ Scoring system for match quality
- ✅ Better handling of merged payments
- ✅ Apartment keyword matching

### UI/UX
- ✅ Clean, modern gradient design
- ✅ Tabbed interface for easy navigation
- ✅ Visual match quality indicators
- ✅ Expandable match lists
- ✅ Real-time statistics

### Features
- ✅ Auto-merge high confidence matches
- ✅ Quick merge buttons
- ✅ Advanced search/filter
- ✅ Batch save with review
- ✅ Better error handling

### Performance
- ✅ Optimized database queries with select_related
- ✅ Client-side filtering for better responsiveness
- ✅ Reduced redundant calculations

## Common Issues and Solutions

### Issue 1: No matches found
**Solution**: Adjust amount_delta and date_delta parameters. Try increasing both values.

### Issue 2: Too many low confidence matches
**Solution**: Ensure payment keywords are properly set in the database for apartments, tenants, and payment types.

### Issue 3: CSV not recognized
**Solution**: Verify CSV has header row with: Date, Description, Amount, Running Bal.

### Issue 4: Dates not parsing
**Solution**: Ensure dates are in MM/DD/YYYY format in CSV file.

## Testing Checklist

- [ ] Upload valid CSV file
- [ ] View all payments in "All Payments" tab
- [ ] Switch between tabs (High, Medium, Low confidence)
- [ ] Search for specific payment
- [ ] Quick merge a high confidence match
- [ ] Open edit modal and review merged data
- [ ] Add payment to save list
- [ ] Remove payment from save list
- [ ] Save all payments to database
- [ ] Verify payments saved correctly
- [ ] Test auto-merge high confidence
- [ ] Test create new payment
- [ ] Test with multiple matches
- [ ] Test apartment dropdown filtering for bookings
- [ ] Test with confirmed/merged payments included
- [ ] Test with different amount/date deltas

## Performance Benchmarks

Expected performance:
- Upload & parse 100 payments: < 2 seconds
- Match 100 file payments against 500 DB payments: < 5 seconds
- Render UI with all matches: < 1 second
- Save batch of 50 payments: < 3 seconds

## Future Enhancements (Optional)

1. AI-powered matching using machine learning
2. Export unmatched payments to CSV
3. Undo/redo functionality
4. Payment conflict resolution
5. Bulk edit capabilities
6. Match history and audit trail
7. Scheduled auto-sync
8. Email notifications for low confidence matches

## Support

For issues or questions:
1. Check the Django logs for detailed error messages
2. Review browser console for JavaScript errors
3. Verify database permissions
4. Ensure all required models are properly migrated

