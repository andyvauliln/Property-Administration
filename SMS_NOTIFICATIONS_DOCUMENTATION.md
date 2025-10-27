# SMS Notifications System Documentation

## Notification Types and Conditions

### 1. 🏠 **Move In Notification**
**When:** 1 day before booking start date  
**Timing:** Day before `start_date`

**Conditions:**
- Booking status is NOT in: `['Blocked', 'Pending', 'Problem Booking']`
- `start_date` equals tomorrow's date
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"Hey! How are you? What time are you planning to be here tomorrow?"
```
---

### 2. 📝 **Unsigned Contract (1 Day)**
**When:** 1 day after booking creation  
**Timing:** Exactly 1 day after `created_at`

**Conditions:**
- Booking status: `'Waiting Contract'`
- Booking was created exactly 1 day ago
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"Hi! Just wanted to check - did you receive the contract link? Please let me know if you need any help with signing it."
```
---

### 3. 📝 **Unsigned Contract (3 Days)**
**When:** 3 days after booking creation  
**Timing:** Exactly 3 days after `created_at`

**Conditions:**
- Booking status: `'Waiting Contract'`
- Booking was created exactly 3 days ago
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"Hi, Did you get a chance to sign the contract?"
```
---

### 4. 💰 **Deposit Reminder**
**When:** 2 days after booking creation  
**Timing:** Exactly 2 days after `created_at`

**Conditions:**
- Booking status: `'Waiting Payment'`
- Booking was created exactly 2 days ago
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"Hi, Did you get a chance to send a deposit yet?"
```
---

### 5. 💳 **Due Payment**
**When:** 1 day before rent payment due date  
**Timing:** Day before `payment_date`

**Conditions:**
- Payment due date is tomorrow
- Payment type is `'Rent'`
- Payment status is `'Pending'`
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"How are you? Gentle reminder that tomorrow is a due date for the payment. Please, let me know when you send it."
```
---

### 6. 🔄 **Extension Inquiry**
**When:** Based on booking duration  
**Timing:** Complex logic based on stay length

**Conditions:**
Two scenarios trigger this notification:

**Scenario A (Long stays > 25 days):**
- `end_date` > `start_date` + 25 days
- `end_date` equals 1 week from today
- Booking has already started (`start_date` ≤ today)

**Scenario B (Short stays ≤ 25 days):**
- `end_date` ≤ `start_date` + 25 days  
- `end_date` equals tomorrow
- Booking has already started (`start_date` ≤ today)

**Excluded statuses:** `['Blocked', 'Pending', 'Problem Booking']`

**Additional Requirements:**
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"How are you? Do you think you might need an extension for your stay?"
```
---

### 7. 🚪 **Move Out**
**When:** 1 day before booking end date  
**Timing:** Day before `end_date`

**Conditions:**
- Booking status is NOT in: `['Blocked', 'Pending', 'Problem Booking']`
- `end_date` equals tomorrow's date
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"Hey! What time do you think you will be leaving tomorrow? I need to arrange cleaners. My standard check out is 10am."
```
---

### 8. ✈️ **Safe Travel**
**When:** 1 day after booking end date  
**Timing:** Day after `end_date`

**Conditions:**
- Booking status is NOT in: `['Blocked', 'Pending', 'Problem Booking']`
- `end_date` was yesterday
- **CRITICAL: Existing Twilio conversation must be found for the booking/tenant**

**Message:**
```
"Thank you for staying with me. Save my number please if you need something here in the future. Safe travels."
```
---
