# Property Management MCP Server

An MCP (Model Context Protocol) server that lets Claude Desktop on any machine query and update live property management data via a URL connection.

---

## How It Works

```
Claude Desktop (remote machine)
        │
        │  HTTP / SSE  (http://68.183.124.79:8001/sse)
        ▼
  mcp_server.py  ← Python process managed by PM2
        │
        │  Django ORM (direct DB connection — no HTTP round-trip to the web app)
        ▼
  PostgreSQL database
```

**Key points:**
- The MCP server is a standalone Python process. It does not proxy to the Django web app — it queries PostgreSQL directly via Django ORM.
- On startup it calls `django.setup()`, which loads all models, settings, and the `.env` file.
- Transport is SSE (Server-Sent Events): a persistent HTTP connection that Claude Desktop opens and keeps alive.
- PM2 manages the process — restarts it automatically on crash and on server reboot.
- **All write operations are tagged** with `last_updated_by = "Claude MCP Agent"` in the database, so every action taken through this server is fully auditable.

---

## Apartment Name Lookup

Most tools accept `apartment_name` as a flexible string — you don't need to know the numeric ID.

The lookup tries these in order:
1. **Exact name match** — `name` field equals the query (case-insensitive)
2. **Building-apt notation** — `"780-110"` → `building_n` contains `780` AND `apartment_n` contains `110`
3. **Name substring** — `name` contains the query string
4. **Building number alone** — returns all units in that building

If multiple apartments match, the tool returns the list and asks you to be more specific. If you just want to explore, use `find_apartments("780")` to list all units in building 780.

---

## Files

| File | Purpose |
|------|---------|
| `mcp_server.py` | All 13 tools — read tools + one write tool (`complete_payment`) |
| `pm2.config.js` | PM2 process config (includes the `mcp-server` entry) |
| `requirements.txt` | `mcp[cli]>=1.0` dependency |
| `MCP_SERVER.md` | This file |

---

## Process Management

```bash
# Check status
pm2 status

# View live logs
pm2 logs mcp-server

# Restart after code changes
pm2 restart mcp-server

# Stop / start
pm2 stop mcp-server
pm2 start pm2.config.js --only mcp-server

# Save process list (run after any config change so it survives reboots)
pm2 save
```

The server runs on port **8001**. The Django web app uses port 8000 / a unix socket — they don't share a port.

---

## Connecting Claude Desktop

Edit (or create) on the remote machine:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "property-management": {
      "type": "sse",
      "url": "http://68.183.124.79:8001/sse"
    }
  }
}
```

Restart Claude Desktop. All 13 tools appear automatically.

---

## Tools Reference

### `find_apartments(query)`
Search for apartments by name, building-apt notation, building number, or name substring.
Returns a list of matching apartments with their IDs, addresses, and bedroom counts.
Use this to discover which apartments exist or to get an apartment's numeric ID.

### `get_apartment_status(apartment_name)`
One-stop status check for an apartment.
Returns: is it occupied right now, who is staying there (name/phone/email), check-in and check-out dates, nights remaining, and the next upcoming booking with its check-in date.

### `get_apartment_parking(apartment_name)`
Returns current and upcoming parking bookings linked to an apartment for the next 90 days.
Includes parking spot number, building, status (Booked/Unavailable/No Car), tenant name, and dates.

### `get_upcoming_payments(apartment_name, days_ahead)`
Returns pending and future payments for an apartment in the next N days (default 60).
Also includes the 5 most recent payments from the last 30 days for context.
Answers questions like "when is the next payment and how much?"

### `search_tenants(query)`
Search tenants by full name, email, or phone number (partial match).
Returns up to 20 matches, each with full contact info and their last 10 bookings.

### `get_availability(start_date, end_date, bedrooms, apartment_type)`
Find apartments with no overlapping confirmed bookings in the given date range.
Filters by bedroom count and/or apartment type ("In Management" / "In Ownership").
`start_date` and `end_date` are ISO strings (e.g. `"2026-06-15"`).

### `get_occupancy_stats(year, month, bedrooms)`
Occupancy rate, booked days, and available days for a month.
Returns an overall rate plus a breakdown by bedroom count.
If `bedrooms` is non-zero, only apartments with that many bedrooms are counted.

### `get_apartment_calendar(apartment_id, year, month)`
Full calendar for one apartment — all bookings, cleanings, and payments.
Month 0 returns the full year. Use `find_apartments()` first to get the ID.

### `get_payment_report(period)`
All payments for a full month with an income/expense/profit summary.
`period` is `"current"`, `"previous"`, or `"next"`.

### `get_booking_availability(year, month)`
3-month availability matrix for every active apartment.
Per-apartment, per-day status (Available / Confirmed / Waiting Contract / Blocked / etc.)
plus occupancy %, revenue, and max revenue per month.

### `get_parking_calendar(year, month)`
3-month day-by-day status for every parking spot.
Per-spot monthly occupancy % and tenant/apartment assignment per day.

### `get_booking_details(booking_id)`
Full record for one booking — tenant contact info, apartment address, all payments with totals, cleanings with cleaner assignment, parking bookings, contract status, and notes.

---

## Example Questions Claude Can Answer

### Occupancy & Current Status
1. Is 780-110 occupied right now?
2. Who is currently staying in 630-213?
3. When does the current guest in 780-408 check out?
4. How many nights does the guest in 780-502 have left?
5. Is apartment 630-222 available this weekend?
6. Which apartments are vacant right now?
7. Is building 630 fully booked this week?
8. What is the current status of 780-110 — waiting contract, confirmed, or something else?
9. Are there any Problem Booking statuses right now?
10. Which apartments have a Waiting Payment status?

### Check-in / Check-out
11. When is the next check-in at 780-502?
12. When is the next check-out at 780-408?
13. What check-ins are happening tomorrow?
14. What check-outs are happening this week?
15. Is there a gap between the current booking and the next one at 780-110?
16. When does the guest in 630-213 arrive?
17. What bookings are starting in the next 7 days?
18. Which apartments have a check-out today?

### Tenant Search
19. Find tenant John Smith.
20. Who has phone number +13051234567?
21. Show me all bookings for Anna Kowalski.
22. What apartment is Maria Garcia staying in?
23. Find the tenant with email john@example.com.
24. Has David Lee stayed with us before? Show his history.
25. Who is the tenant in building 780, apartment 110?
26. Find all tenants whose names start with "Al".
27. Search for tenant by partial phone number "305".

### Availability
28. Any one-bedroom apartments available starting June 15?
29. Which two-bedroom units are free from July 1 to July 14?
30. Show me all available In Management apartments next month.
31. Which apartments are free for the whole month of August?
32. Is there anything available this Friday for a 3-night stay?
33. Find a two-bedroom available from June 20 to July 5.
34. Which In Ownership apartments are free right now?

### Occupancy Statistics
35. What is the occupancy rate for May?
36. How occupied are one-bedroom apartments this month?
37. Compare occupancy between one-bedrooms and two-bedrooms in June.
38. What was the occupancy rate last month?
39. How many available days do we have across all apartments in July?
40. What is the occupancy breakdown by apartment size for Q2?

### Payments
41. When is the next payment for 630-210 and how much?
42. Show me all pending payments for apartment 780-408.
43. What is the total income for April?
44. What was the net profit last month?
45. Show me all outgoing payments this month.
46. What is the total pending income for this month?
47. Has the tenant in 780-408 paid their rent?
48. Show me the payment report for May.
49. Which apartments have pending payments overdue?
50. What payments are due in the next 30 days for 630-213?
52. How much has been paid for booking #142?
53. Show me all Non-Operating payments this month.

### Parking
54. What is the parking situation for 630-222?
55. Is parking spot #5 in building 630 free in July?
56. Which parking spots are available this week?
57. Who has parking in building 780 right now?
58. When does the parking booking for 780-110's current guest end?
59. Show the parking calendar for June.

### Apartment Calendar & History
60. Show me the full calendar for apartment 780-110 this month.
61. What bookings does 630-213 have in June?
62. What cleanings are scheduled for next week?
63. Show me all bookings in building 780 for July.
64. What is the revenue breakdown for 780-110 this year?
65. Show me full details for booking #142.
66. Has a contract been sent for booking #88?
67. What is the source of booking #200 — Airbnb, referral, or other?

---

## Adding New Tools

Add a decorated function to `mcp_server.py`:

```python
@mcp.tool()
def my_new_tool(param: str) -> str:
    """Docstring shown to Claude as the tool description."""
    # Use _resolve_apartment(param) to look up an apartment by name
    # Use _dumps({...}) to serialize the result
    return _dumps({"result": ...})
```

Then restart:

```bash
pm2 restart mcp-server
```

New tools appear in Claude Desktop automatically on next reconnect — no config change needed.
