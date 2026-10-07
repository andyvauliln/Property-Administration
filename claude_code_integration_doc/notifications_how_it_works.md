# Automatic notifications to tenants: how it works

Written 2026-10-06. Two parts: how it works **in production today**, and how it works **in the new version** (built
and tested in the sandbox, not live yet). The examples and tests of the new version are in
`simple_telegram_alerts.md`, part 8a (cases H1–H6).

---

## 1. In short

- **Today (production):** every day at 08:00 Florida time a job sends template texts to tenants. It looks only at
  the CRM (dates, payment status). It does **not** read the chat. So a tenant who wrote "I already paid" still gets
  "reminder: your rent is due".
- **New version:** the same job, the same templates, the same times. But before anything is sent, the AI reads the
  tenant's chats and the payments and decides: **send**, or **hold and ask the team**.

---

## 2. Today in production

### 2.1 What happens

1. Every day at **08:00 ET** the job `sms_notifications` starts.
2. For each kind of notification (table below) it finds the bookings that match its rule.
3. It takes the text of that kind from the templates on the site (AI Management → SMS templates).
4. It sends the text to the tenant's chat as "Virtual Assistant". If the tenant has several chats, it goes to the
   chat where the tenant wrote last.
5. If the booking has no chat at all, nothing is sent and the manager chat gets a "message wasn't delivered" note.

Nobody approves anything, nothing appears in the AI group in Telegram. About 1 to 7 texts go out per day.

### 2.2 The notifications

| Notification | Sent when | On the site now | Text |
|---|---|---|---|
| Move-in tomorrow | the day before the booking starts | ON | Hey! How are you? What time are you planning to be here tomorrow? |
| Contract not signed, 1 day | booking created yesterday, status "Waiting Contract" | ON | Hi! Just wanted to check - did you receive the contract link? Please let me know if you need any help with signing it. |
| Contract not signed, 3 days | booking created 3 days ago, status "Waiting Contract" | ON | Hi, Did you get a chance to sign the contract? |
| Contract not signed, 7 days | booking created 7 days ago, status "Waiting Contract" | OFF | Hi, We are still waiting for you to sign the contract. Please let us know if you have any questions. |
| Deposit not received | booking created 2 days ago, status "Waiting Payment", has a Hold Deposit payment | ON | Hi, Did you get a chance to send a deposit yet? |
| Rent due tomorrow | a Rent payment is due tomorrow and is Pending | ON | How are you? Gentle reminder that tomorrow is a due date for the payment. Please, let me know when you send it. |
| Rent 3 days late | a Rent payment was due 3 days ago and is still Pending | OFF | Hi, We are still waiting for your rent payment. Please let us know when you send it. |
| Stay ends soon – extension? | 1 week before the end (stays longer than 25 days), or 1 day before the end (shorter stays) | ON | How are you? Do you think you might need an extension for your stay? |
| Move-out tomorrow | the day before the booking ends | ON | Hey! What time do you think you will be leaving tomorrow? I need to arrange cleaners. My standard check out is 10am. |
| After checkout | the day after the booking ended | OFF | Thank you for staying with us. Save our number please if you need something here in the future. Safe travels. Team of http://www.RentalGuru.co/ |

Bookings with status Blocked, Pending, Problem Booking or Cancelled get no move-in, extension, move-out or
after-checkout text.

### 2.3 Two problems today

1. **The chat is ignored.** The job does not know what the tenant or the team wrote. Typical cases: the tenant
   already said they paid; the tenant already gave the arrival time; the team already agreed something else.
2. **Switched-off templates are still sent.** When a template is OFF on the site, the job falls back to an old text
   built into the code and sends that. In the last 30 days "After checkout" went out 5 times and "Rent 3 days late"
   8 times, although both are OFF. (Fixed in the new version; not fixed in production yet.)

---

## 3. The new version (sandbox, not live yet)

### 3.1 What happens

1. 08:00 ET: the same job finds the same bookings with the same rules and takes the same template.
   A template that is **OFF is skipped completely**: nothing is sent, the AI is not asked.
2. Instead of sending, the job **hands the notification to the AI**: which kind, the template text, and why it was
   planned ("a Rent payment is due tomorrow and is still Pending").
3. The AI reads:
   - **all chats of this tenant** as one history (also a second chat that was created by mistake),
   - the booking and its **payment records** in the CRM,
   - the knowledge base and the open cases of that chat.
4. The AI decides one of two things: **still needed** or **not needed / not sure**.

### 3.2 Still needed

Handled exactly like a tenant reminder.

- **Live apartment:** the text is sent at once. An AI MESSAGE in the Telegram group shows what was sent and why:

```
🤖 AI MESSAGE · 6 Oct, Tue 08:00 ET

🏠 720-201 · 👤 Vera Lopez · 📅 Rent due tomorrow

———
↪️ Vera (tenant) "Thanks, got it!" ↪️

💬 Janna (team) "You're welcome, Vera!" 💬
———
🤖 "Hello Vera, how are you? Gentle reminder that tomorrow is a due date for the payment. Please, let me know when you send it." 🤖
———
✅ Sent 08:00 – still needed: rent $2,150 due 7 Oct is Pending, nothing about it in the chat
———
```

  No buttons, posted without sound.

- **Test apartment:** nothing is sent by itself. The same message says
  `🧪 Test mode: NOT sent automatically. Press to send for real.` and has `[🤖 Send now] [✏️ Edit Answer]`.

### 3.3 Not needed, or the AI is not sure

Nothing is sent. The message says why and waits for a person:

```
🤖 AI MESSAGE · 6 Oct, Tue 08:00 ET

🏠 720-201 · 👤 Vera Lopez · 📅 Rent due tomorrow · ⏸ HELD

———
↪️ Janna (team) "Hi Vera, your October invoice is ready: $2,150, due Oct 7." ↪️

💬 Vera (tenant) "Hi Janna, I already paid October rent yesterday by Zelle" 💬
———
🤖 "How are you? Gentle reminder that tomorrow is a due date for the payment. Please, let me know when you send it." 🤖
———
⏸ NOT sent – Vera wrote on 5 Oct that she already paid by Zelle, but the payment is still Pending in the CRM
———
⏰ Janna: check Vera's October payment – today 10:00 (1/2) ⏰
———

[📤 Send anyway] [✏️ Edit Answer]
[✅ Close Reminder]
```

- Posted **with sound**: somebody has to look.
- `📤 Send anyway` sends the text for real; the message then shows `✅ Sent · Andy 08:12`.
- `✏️ Edit Answer` lets you write another text first.
- When a person has to check something (a payment the CRM does not show), the AI adds a reminder for the owner.
  When nothing has to be checked (the tenant already gave the arrival time), there is no reminder.

The AI holds a notification when, for example:

| Planned text | Held because |
|---|---|
| Rent due tomorrow | the tenant wrote they already paid / sent it |
| Move-in tomorrow ("what time will you be here?") | the tenant already gave the arrival time |
| Contract not signed | the tenant wrote they signed, or the team is already sorting it out in the chat |
| Extension? | the tenant already asked for an extension or said they are leaving |
| Move-out tomorrow ("what time will you leave?") | the tenant already gave the time |

### 3.4 The text

- The text is the template. The AI **may adapt it** a little so it fits the conversation: the tenant's first name,
  the language of the chat, one short sentence when it answers something the tenant asked and the team already
  answered.
- It must keep the template **short** and keep **every fact** of it (times, amounts).
- It may **not add** payment details, account or Zelle data, instructions, new questions or promises.
  (On the first test run the AI added the company's Zelle number to a rent reminder by itself. That is now forbidden.)

### 3.5 Safety nets

- **The AI is down or fails:** in a live apartment the template is sent as it is, like today. A notification is never
  lost because of the AI.
- **SMS hours:** texts go out only between 08:00 and 21:00 Florida time. The job runs at 08:00, so this normally
  changes nothing.
- **No chat for the booking:** like today, nothing is sent and the manager chat gets a note.
- **Rollback:** the setting `AI_AGENT_NOTIFICATIONS=direct` makes the job send directly again, as today.

### 3.6 Several chats of one tenant

Sometimes a second chat is created for the same person by mistake. The AI reads all chats of one tenant as one
history, so "I sent the rent this morning" written in the other chat is seen and the reminder is held (case H6).
The text itself goes to the chat where the tenant wrote last.

Planned, not built yet: the end-of-day report "Problems today" lists every extra chat created for a tenant who
already had one.

---

## 4. What changes for the team

| | Today | New version |
|---|---|---|
| Who decides | the job, from CRM dates only | the AI, after reading the chats and the payments |
| What the team sees | nothing | one AI MESSAGE per notification: sent, or held with the reason |
| Tenant already answered / paid | text is sent anyway | text is held, the team gets `📤 Send anyway` |
| Template switched OFF | an old built-in text is sent anyway | nothing is sent |
| Text | always the template | the template, lightly adapted to the chat |
| Test apartment | text is sent | never sent by itself, `🤖 Send now` |

Expect 1 to 7 AI MESSAGES per day from this.

---

## 5. How it was tested

Six cases in the sandbox with the real AI, all passing on 2026-10-06:

| Case | Situation | Result |
|---|---|---|
| H1 | rent due tomorrow, nothing in the chat, live | sent, AI MESSAGE |
| H2 | tenant wrote "I already paid" | held, reminder for Janna, Send anyway works |
| H3 | same as H1 in a test apartment | not sent, Send now works |
| H4 | move-in tomorrow, tenant already gave the arrival time | held, no reminder |
| H5 | move-out tomorrow | sent, text fitted to the chat |
| H6 | "I sent the rent" is in the tenant's second chat | held |

To run them again: `manage.py ai_agent_sandbox_test --group H` (from the worktree `/home/superuser/site-alerts-v5`).

---

## 6. Not done yet

- **Not live.** Production still works as in part 2. The new flow starts with the deploy of the new alerts.
- **The switched-off-template problem** (2.3, point 2) is fixed only in the new version. It can be fixed in
  production separately, with a small change to the job.
- **Daily report** with sent / held notifications and extra chats: specified, not built.
- Welcome, contract-link and "contract signed" messages are separate from this job (they are sent when the event
  happens, not at 08:00) and are not changed.
