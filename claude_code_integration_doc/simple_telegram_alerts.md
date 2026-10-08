# Simple Telegram alerts (draft v5.3)

Draft of 2026-10-06, fourth version: all notes and answers so far are applied (list: part 9). No open questions.
Goal: anyone in the "[PM] AI GROUP" understands an alert in 5 seconds.
Replaces the long card from client spec v4 (`client_spec_v4.md`, `mysite/ai_agent/cards.py`). Nothing here is built yet.

- Part 1: the rules
- Part 2: alert types and buttons
- Parts 3–8: **example catalog**: one example per branch of the logic, each with an ID (`A1`, `C4`, …) so it can
become a test in `testbed/`
- Part 9: your notes and what changed
- Part 10: decisions (the former open questions)
- Part 11: test strategy and process (sandbox test runner)
- Part 12: what changes in code

---



## 1. Rules



### 1.1 Who does what

1. **Nothing happens without a button press**, except these automatic things:
  - 🌙 after-hours message to the tenant (live only)
  - 🚨 emergency: safety reply + call to Farid + ClickUp task (live only)
  - ⏰ reminders are **created automatically** when needed (there is no "Create Reminder" button)
  - ⏰ a tenant reminder is **sent automatically when it is due** in live mode (1/2 after 2 h, 2/2 next day), unless
  someone pressed `✅ Close Reminder` before
  - 📅 an **automatic notification** (move-in, rent due, contract not signed, …) is sent in live mode when the AI finds it
  is still needed (part 8a); when it is not sure, it holds it and asks
2. **A button press always does the real action**, in live and in test, even when the AI's own ClickUp writing is
  OFF. Pressing is the confirmation. `🤖 Send Answer` **really sends to the tenant also in a test apartment**.
3. **A typed reply never changes anything by itself.** The bot first explains what it will change and shows a button;
  the change happens only after the press (part 7).
4. **Modes are switched on the site only, never in Telegram.** The alert shows them as status:
  - `📤 AI sends to chat: ON (live)` / `OFF (test)`: may the AI send to the tenant **by itself** (after-hours,
   emergency, auto-sent reminders)? Test = the AI never sends by itself.
  - `🎫 AI Auto ClickUp: ON` / `OFF`: may the AI write to ClickUp **by itself** (emergency task)?
5. **No alert when the manager has nothing to do**: tenant "thanks", "ok", team "ok thanks", a reminder that is no
  longer needed.
6. **No "Approve all", no "I'll handle", no "Ignore", no "Stop all", no confirmation questions.** One button = one action
  (the only double one: `🤖🎫 Send + Create Task`, F1). An alert nobody
  presses just stays as it is; the end-of-day report lists it.



### 1.2 How an alert looks

1. Header: alert type + date/time (Florida time, ET). Then one line: `🏠 apartment · 👤 tenant · urgency`.
  Team messages: `🏠 apartment · 👤 tenant · 🧑‍🔧 team member(s)`, **no urgency** (the team wrote it, it is not an
   alarm; the priority of a proposed task is on the task line).
2. One block per thing, blocks split by `———`. The same icon at the start and the end of a block.
3. Empty blocks are not shown. No task → no task block and no task button.
4. Several of the same kind are numbered: `🎫1`, `🎫2`, buttons `Create Task 1`, `Create Task 2`.
5. Every task block has a second line *who · priority · due*. Every knowledge block has a second line *from where*.
6. After a press, the button turns into the result: `🤖 Send Answer` → `✅ Answer sent · Andy 14:36`.
7. **Urgency, 3 levels**: 🟢 routine (posted **without sound**), 🔴 urgent (with sound), 🚨 emergency (with sound).
  Team message alerts are always without sound, unless they propose an urgent task.
8. One thread per case: a newer alert about the same case is posted as a reply to the first one.
9. When the tenant writes again, older alerts of that chat lose their buttons and get `⚠️ Outdated – see the newer
  alert`. The new alert covers all unanswered messages.
10. **Long texts are never cut.** Button labels are always short (a few words). A Telegram bot message can hold at most
  4096 characters (Telegram does not split it for the bot, the send just fails), so a longer alert is sent as
    **several messages in a row**, marked `(1/2)`, `(2/2)`. The buttons are on the last part. A reply to any part
    works.
11. **Footer on every alert** (also reminders, AI messages and the bot's answers about one chat):

```
↩ Reply to this message for questions, notes or custom actions.

📤 AI sends to chat: ON (live) · 🎫 AI Auto ClickUp: OFF
🔗 AI run: http://68.183.124.79/ai-runs/627/
💬 CRM chat: http://68.183.124.79/chat/CH5f2e…/
```

   In the examples the footer is written in full in A1, B1, C1 and D2; elsewhere it is shown as `(footer)`.

1. **Message part = one block with two joined messages** (not one line per message, no times):

```
———
↪️ Edy (team) "Hi Vera, welcome! The wifi details are on the fridge." ↪️

💬 "Hello? Hi, the kitchen sink is dripping since yesterday" 💬
———
```

- `↪️ Name (side) "…" ↪️` = **before**: the last messages from the other side, joined into one text. For a tenant
alert that is the team (or the AI answer that was sent); for a team alert, the tenant. Not shown if the other
side never wrote in this chat.
- Empty line, then `💬 "…" 💬` = **now**: all messages of this side written after that, joined into one text: the
new message(s) plus the earlier ones in a row, also when an older alert already showed them. No name: the header
already says who wrote. Several team members in a row are one side (header `🧑‍🔧 Edy, Janna`).
- Reminder alerts have no author in the header, so there the `💬` line also has the name: `💬 AI (sent) "…" 💬`.
- Nothing is cut; a very long block makes the alert split into parts (rule 10).
- Today's card shows only the new messages: this rule is new (the AI itself already reads the whole history).



### 1.3 Reminders

1. Created automatically when the case needs one. The alert shows the reminder block with a **Close Reminder**
  button.
2. **Max 2 per case and side** (the team's reminders, the tenant's reminders): `1/2` within 2 hours (same day), `2/2` next day. Urgent and emergency: `1/2` after 30 minutes. Plus the deadline reminders 24 h and 2 h before
  a tenant deadline (Kevin tagged at 2 h); these don't count in the 2.
3. Time windows stay: routine team reminders only 09:00–18:00 (else next 09:00), urgent/emergency any time; tenant SMS
  only 08:00–21:00 (else next 08:00).
4. **Before a reminder fires, the system re-checks** the chat and the ClickUp task. Already done → it closes quietly,
  no alert, only listed in the report. ClickUp task closed → the AI proposes the "team marked it done" message.
5. `⏰ Remind again in 24 hours` is only on the **last** reminder (2/2). On 1/2 it is not needed: 2/2 comes tomorrow
  anyway.
6. Tenant reminder: **live** → sent automatically when due (1/2 after 2 h, 2/2 next day), after the re-check
  (rule 4); the team sees it in the first alert and can stop it with `✅ Close Reminder`; after sending an AI MESSAGE
  is posted (D5). **Test** → never sent by itself: a REMINDER alert with `🤖 Send now` is posted instead (C4); the
  reminder stays in the database, nothing happens if nobody presses.
7. A reminder alert shows the same two lines (with names, rule 1.2.12): the last joined message of each side, the
  older side first, so you see where the conversation stopped without opening the chat.
8. The re-check (rule 4) is done by the AI: it reads the chat, the case and the ClickUp task and says "still needed"
  (then the REMINDER alert, or the tenant reminder is sent) or "already done" with the reason (then no alert, C5).
  The AI never adds reminders on a reminder alert: the backend sets the 2/2 (the next day 10:00, for the team the
  next working day) when the 1/2 is still needed.
9. When a reminder becomes due, the button of the alert that showed it changes to `⏰ Due 16:34 – see the reminder
  alert`; when it closed quietly, to `✅ Reminder closed · done – checked 16:34`.



### 1.4 Knowledge

1. One knowledge block per fact, with **two buttons**: `🏠📚 Apartment` and `🌍📚 Global`. The AI marks one as
  recommended (`⭐`). Pressing one removes the other.
2. The second line shows **where the fact came from** (whose message and when). A fact the AI can't point to a message
  for is not shown at all.
3. Saved as **verified** (a person pressed it).



### 1.5 Learning from the team

1. Every `Send Answer` press marks the answer **approved**. Every edited answer is stored as **corrected** (old + new).
  On new messages the AI gets the most similar approved/corrected answers as examples ("this is how the team answers
   such questions").
2. `✏️ Edit Answer` flow (E3): the bot shows the corrected answer and a proposed **rule** ("next time …"); `🤖 Send
  Answer`sends it,`📏 Save rule` saves the rule.
3. End-of-day report: alerts nobody pressed are compared with what the team really wrote in the chat; if the AI can
  learn something, it proposes a rule with `📏 Save rule` (G3).



### 1.6 Icons


| Icon         | Meaning                                                            |
| ------------ | ------------------------------------------------------------------ |
| 🏠           | apartment                                                          |
| 👤 / 🧑‍🔧   | tenant / team member                                               |
| 🟢 / 🔴 / 🚨 | routine / urgent / emergency                                       |
| ↪️           | before: the other side's last messages, joined into one            |
| 💬           | now: this side's messages in a row, joined into one; CRM chat link |
| 🤖           | AI answer (to be sent / sent to the tenant)                        |
| 🎫           | new ClickUp task                                                   |
| 🔄           | update of an existing ClickUp task (comment, close, priority)      |
| ⏰            | reminder                                                           |
| 🏠📚 / 🌍📚  | apartment knowledge / global knowledge                             |
| ⚖️           | contract basis (legal question)                                    |
| 🌙           | after-hours message                                                |
| 📞           | phone call to Farid                                                |
| 🧪           | test mode                                                          |


---



## 2. Alert types and buttons



### 2.1 Alert types


| Alert              | When                                                                | Buttons                                                                                            |
| ------------------ | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 📨 TENANT MESSAGE  | the tenant wrote and the manager has something to decide            | Send Answer, Edit Answer, Create Task N, Apply Update N, Close Reminder N, Apartment / Global      |
| 🧑‍🔧 TEAM MESSAGE | the team wrote in a tenant chat and there is something to do        | Create Task N, Apply Update N, Close Reminder N, Apartment / Global                                |
| ⏰ REMINDER         | a reminder is due and still needed                                  | Close Reminder; tenant reminder (test only): Send now, Edit Answer; last one: Remind again in 24 h |
| 🤖 AI MESSAGE      | the AI sent something by itself (or would have, in test)            | none, or buttons for what is still left                                                            |
| ↩ REPLY            | the bot answers a team reply: first explains, then a button does it | Apply Change, Send Answer, Save rule, Apartment / Global                                           |
| 📋 REPORTS         | every day 18:00 ET                                                  | Close N, Remind tomorrow N, Save rule N                                                            |




### 2.2 Buttons


| Button                         | What it does                                                          | Becomes                      |
| ------------------------------ | --------------------------------------------------------------------- | ---------------------------- |
| 🤖 Send Answer                 | sends the answer to the tenant chat, marks it approved                | ✅ Answer sent · who · time   |
| 🤖🎫 Send + Create Task        | answer + new task(s) in one press (shown when the alert has both)     | ✅ Answer sent + ✅ Task created |
| ✏️ Edit Answer                 | the bot asks for the correct text (E3)                                | ✏️ Waiting for text · who    |
| 🎫 Create Task N               | creates the ClickUp task (also creates the case if there is none yet) | ✅ Task created · link        |
| 🔄 Apply Update N / Close Task | comments on / changes / closes the existing ClickUp task              | ✅ Commented / ✅ Closed · who |
| ✅ Apply Change                 | does the change the bot explained after a typed reply                 | ✅ Changed · who              |
| ✅ Close Reminder N             | stops that reminder                                                   | ✅ Reminder closed · who      |
| ⏰ Remind again in 24 h         | only on the last reminder: one more reminder tomorrow                 | ⏰ Next: Thu 10:00            |
| 🤖 Send now                    | tenant reminder in test: sends it to the tenant (for real)            | ✅ Sent · who                 |
| 🏠📚 Apartment / 🌍📚 Global   | saves the fact, verified, in that scope                               | ✅ Saved to apartment · who   |
| 📏 Save rule                   | saves the proposed rule (E3, G3)                                      | ✅ Rule saved · who           |


Every press is logged with who pressed and when. A second press on a done button: `already done by Andy 14:36`.

---



## 3. Example catalog – A. Tenant messages



### A1. Routine maintenance, live (the full alert)

Tenant Vera, 720-201, Tue 6 Oct 14:34 (office hours). Kitchen sink is dripping.

```
📨 TENANT MESSAGE · 6 Oct, Tue 14:34 ET

🏠 720-201 · 👤 Vera Lopez · 🟢 Routine

———
↪️ Edy (team) "Hi Vera, welcome! The wifi details are on the fridge." ↪️

💬 "Hi, the kitchen sink is dripping since yesterday" 💬
———
🤖 "Hello Vera, thank you for letting us know. I've logged the dripping kitchen sink, and our maintenance team will follow up to schedule a visit." 🤖
———
🎫 Kitchen sink dripping – 720-201 🎫
   Edy · routine · due Fri 9 Oct 14:34
———
⏰ Check the sink task has a visit date – today 16:34 (1/2) ⏰
———

↩ Reply to this message for questions, notes or custom actions.

📤 AI sends to chat: ON (live) · 🎫 AI Auto ClickUp: OFF
🔗 AI run: http://68.183.124.79/ai-runs/627/
💬 CRM chat: http://68.183.124.79/chat/CH5f2e…/

[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Task]
[🎫 Create Task]
[✅ Close Reminder]
```

Posted without sound (routine). After Andy presses Send Answer and Create Task:

```
[✅ Answer sent · Andy 14:36]
[✅ Task created · Andy 14:36] [↗ Open in ClickUp]
[✅ Close Reminder]
```

Test: the answer says it is logged and that the maintenance team will follow up to schedule a visit. It promises no time and no deadline (no "within 24 hours").
`🤖🎫 Send + Create Task` does both in one press (F1); there is no confirmation question.

### A2. Urgent: locked out

```
📨 TENANT MESSAGE · 6 Oct, Tue 15:10 ET

🏠 630-214 · 👤 Mark Chen · 🔴 Urgent

———
↪️ Edy (team) "Hi Mark, welcome! Check-in is from 3pm, the door code
is in your booking email." ↪️

💬 "The door code doesn't work, I'm standing outside with my kids" 💬
———
🤖 "Hi Mark, sorry! Please try 4521# – the code was changed on Monday.
If it still doesn't open, reply here and we'll call you right away." 🤖
———
⏰ Check Mark got in – today 15:40 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```

Posted **with sound**. An urgent team reminder may fire at any hour.

### A3. Property fact, known in the knowledge base

```
📨 TENANT MESSAGE · 6 Oct, Tue 15:45 ET

🏠 630-214 · 👤 Mark Chen · 🟢 Routine

———
↪️ AI (sent) "Hi Mark, sorry! Please try 4521# – the code was changed
on Monday." ↪️

💬 "We're in, thanks! What's the wifi password?" 💬
———
🤖 "Great! The wifi is MyHome-5G, password B123H4689." 🤖
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
```

No task, no reminder → no blocks and no buttons for them. The open "Check Mark got in" reminder closes quietly
(he said "we're in") – only in the report.

### A3b. The fact we gave does not work

Mark got the wifi password from the knowledge base (A3) and writes that it does not work.

```
📨 TENANT MESSAGE · 6 Oct, Tue 15:30 ET

🏠 630-214 · 👤 Mark Chen · 🟢 Routine

———
↪️ AI (sent) "Great! The wifi is MyHome-5G, password B123H4689." ↪️

💬 "The wifi password doesn't work" 💬
———
🤖 "Sorry about that, Mark! The team will check the correct wifi
password and send it to you." 🤖
———
⏰ Edy: check the wifi password for Mark – today 17:30 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```

The knowledge base may be out of date: the AI does **not** invent another password and does not repeat the old one
as if it were new. No task (nothing to repair until the team has checked), one reminder for the team.
The normal next step is a reply under this alert with the right value – see E4.

### A4. Property fact, NOT in the knowledge base

```
📨 TENANT MESSAGE · 6 Oct, Tue 15:20 ET

🏠 105 Wilson · 👤 Ana Ruiz · 🟢 Routine

———
↪️ Edy (team) "Welcome Ana! Let us know if you need anything." ↪️

💬 "Thanks! Where can I leave my bike?" 💬
———
🤖 "Hi Ana, let me check with the team and get back to you today." 🤖
———
❓ Not in the knowledge base: bike storage for 105 Wilson
———
⏰ Edy: tell Ana where to leave her bike – today 17:20 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```

Follow-up test: Edy later writes "Ana, the bike room is on the 1st floor, next to the mailboxes" in the chat → B2.

### A4b. Parking spot: from the CRM, not the knowledge base

Parking is **CRM data**. The AI gets it with every message in the PARKING block of its context: the spot booked for
this booking (Parking page of the site) and the apartment's own spot. It is never a knowledge-base fact.

```
📨 TENANT MESSAGE · 6 Oct, Tue 15:25 ET

🏠 105 Wilson · 👤 Ana Ruiz · 🟢 Routine

———
↪️ Edy (team) "Welcome Ana! Let us know if you need anything." ↪️

💬 "Thanks! Which parking spot is mine?" 💬
———
🤖 "Hi Ana, your parking spot is #14, in the garage next to the
mailboxes." 🤖
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
```

No ❓ line, no reminder, no task.

### A4c. Parking: the apartment has a spot, the booking has no parking record (CRM change)

The apartment's own spot #14 is free for the dates of the booking, but nothing is booked for Ana's booking in the CRM.
The AI answers with the spot **and proposes the missing record**:

```
📨 TENANT MESSAGE · 6 Oct, Tue 15:25 ET

🏠 105 Wilson · 👤 Ana Ruiz · 🟢 Routine

———
↪️ Edy (team) "Welcome Ana! Let us know if you need anything." ↪️

💬 "Thanks! Which parking spot is mine?" 💬
———
🤖 "Hi Ana, your parking spot is #14, in the garage next to the
mailboxes." 🤖
———
🗂 CRM: book parking spot #14 for this booking (6 Oct – 30 Oct 2026) 🗂
   why: the apartment's own spot is free, nothing is booked for this booking
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🗂 Apply in CRM]
```

`🗂 Apply in CRM` writes the record (here: the parking booking of this booking) and turns into
`✅ Done in CRM · Andy 15:31`. A second press only answers "already done by Andy". No task, no reminder.

**CRM changes in general (🗂 block).** The AI sees CRM data in its context. When a record is missing or wrong, the chat
is about it, and the change is on the list below, the AI proposes it as a 🗂 block with its own button.

- Nothing is ever written to the CRM without the press. The words of the block come from the CRM, not from the AI.
- Before the block is shown and again at the press, the change is checked against the CRM as it is at that moment
  (a spot that was taken in the meantime → `⛔ Not changed – spot #14 is taken …`).
- The AI can only propose changes from this list; it can not write anything else to the CRM:

| Change | What it does | When the AI proposes it |
|---|---|---|
| `book_parking` | creates the parking record of the booking on a spot | the booking has no parking, a spot of the apartment is free for its dates, and the tenant asks about their spot |
| `cancel_parking` | deletes the parking record of the booking | a spot is booked for the booking and the tenant wrote they have no car / do not need it |

More kinds of records are added to this list one by one (file `mysite/ai_agent/crm_changes.py`).
A reply "remove the CRM change" takes the block off the alert (with ✅ Apply Change, like every reply).

### A5. Payment question with contract basis (legal)

```
📨 TENANT MESSAGE · 6 Oct, Tue 16:00 ET

🏠 720-514 · 👤 Rita Gomez · 🟢 Routine

———
↪️ Janna (team) "Hi Rita, your October invoice is ready: $2,150, due
Oct 5." ↪️

💬 "Can I pay October rent on the 10th instead of the 5th?" 💬
———
🤖 "Hi Rita, I'll check with our accounting team and get back to you
by tomorrow." 🤖
⚖️ Contract: section 4 – rent due on the 5th, $50 late fee after the 7th
———
⏰ Janna: decide on Rita's late rent request – Wed 09:00 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```

Branches: owner is Janna (payments); 16:00 + 2 h = 18:00 is after office hours → routine reminder moves to Wed 09:00.

### A6. Booking / extension

```
📨 TENANT MESSAGE · 7 Oct, Wed 15:50 ET

🏠 720-201 · 👤 Vera Lopez · 🟢 Routine

———
↪️ AI (sent) "Great to hear, Vera! Thanks for letting us know." ↪️

💬 "Also, can I stay 2 more weeks, until Nov 3?" 💬
———
🤖 "Hi Vera, great to hear you'd like to stay longer! I'm checking
availability and will confirm by tomorrow." 🤖
———
⏰ Edy: check 720-201 availability Oct 20 – Nov 3 – today 17:50 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```



### A7. Complaint / dispute

```
📨 TENANT MESSAGE · 6 Oct, Tue 16:30 ET

🏠 651-402 · 👤 Joe Park · 🔴 Urgent

———
↪️ Janna (team) "Hi Joe, your deposit refund of $900 was sent today
($300 kept for cleaning)." ↪️

💬 "You kept $300 of my deposit for cleaning, the place was clean.
I want it back or I'll leave a review" 💬
———
🤖 "Hi Joe, I'm sorry you're unhappy with this. I've passed it to our
manager, who will review the deposit details and reply to you." 🤖
⚖️ Contract: section 9 – cleaning fee may be deducted if the checkout
checklist was not done; checkout photos on file
———
⏰ Farid: review Joe's deposit dispute – today 17:00 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```

Branch: the AI never promises money in a dispute; owner Farid; urgent, so the reminder may fire after 18:00.

### A8. Time-sensitive, with a tenant deadline

```
📨 TENANT MESSAGE · 6 Oct, Tue 17:10 ET

🏠 317 10th St · 👤 Sam Lee · 🔴 Urgent

———
↪️ Edy (team) "Hi Sam, your booking from Oct 7 is confirmed!" ↪️

💬 "I land at 11pm tomorrow, how do I get the keys?" 💬
———
🤖 "Hi Sam, the lockbox is at the front door, code 7788. Safe travels!" 🤖
———
⏰ Deadline Wed 7 Oct 23:00 – reminders 24 h and 2 h before (Kevin at 2 h) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```

Branch: tenant deadline → deadline reminders (C7); they don't count in the 2.

### A9. Tenant asks for an update (existing task, repeat ask)

```
📨 TENANT MESSAGE · 7 Oct, Wed 11:00 ET

🏠 720-201 · 👤 Vera Lopez · 🔴 Urgent

———
↪️ AI (sent) "Hello Vera, thank you for letting us know. I've logged the dripping kitchen sink, and our maintenance team will follow up to schedule a visit." ↪️

💬 "Any news about the sink? It's getting worse" 💬
🔁 Asked 2 times · waiting 20 h · task open, no visit date yet
———
🤖 "Hi Vera, sorry for the wait. I've pushed it to the team as urgent
and we'll confirm a visit time today." 🤖
———
🔄 Task "Kitchen sink dripping – 720-201": make urgent + comment
"Tenant asked again, getting worse – please schedule today" 🔄
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🔄 Apply Update]
```

Branch: the case already has a reminder → no new one (max 2).

### A10. Tenant says the problem is gone

```
📨 TENANT MESSAGE · 7 Oct, Wed 11:30 ET

🏠 720-201 · 👤 Vera Lopez · 🟢 Routine

———
↪️ Edy (team) "Vera, the plumber comes tomorrow 9–11am, please leave
the gate open" ↪️

💬 "The sink stopped dripping, the plumber fixed it. Thanks!" 💬
———
🤖 "Great to hear, Vera! Thanks for letting us know." 🤖
———
🔄 Close task "Kitchen sink dripping – 720-201" 🔄
———
⏰ Open reminder "Ask Vera if the sink is fixed" (Wed 12:00) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🔄 Close Task]
[✅ Close Reminder]
```

Branch: the chat shows the issue is gone → the AI proposes closing the task and the reminder.

### A11. Two problems in one message

```
📨 TENANT MESSAGE · 6 Oct, Tue 15:30 ET

🏠 630-214 · 👤 Mark Chen · 🟢 Routine

———
↪️ AI (sent) "Great! The wifi is MyHome-5G, password B123H4689." ↪️

💬 "The dryer doesn't start and the bathroom light is flickering" 💬
———
🤖 "Thanks Mark, we've logged both and will schedule a visit." 🤖
———
🎫1 Dryer doesn't start – 630-214 🎫1
   Edy · routine · due Fri 9 Oct
———
🎫2 Bathroom light flickering – 630-214 🎫2
   Edy · routine · due Fri 9 Oct
———
⏰ Check both tasks have a visit date – today 17:30 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Tasks]
[🎫 Create Task 1] [🎫 Create Task 2]
[✅ Close Reminder]
```



### A12. After hours, routine

Tue 22:15. First the AI MESSAGE about the after-hours text is posted (as in D2, but "✅ Sent to the tenant"). Then:

```
📨 TENANT MESSAGE · 6 Oct, Tue 22:15 ET

🏠 720-201 · 👤 Vera Lopez · 🟢 Routine · 🌙 after-hours message sent 22:15

———
↪️ AI (sent) "Hello Vera, thank you for letting us know. I've logged the dripping kitchen sink, and our maintenance team will follow up to schedule a visit." ↪️

💬 "Also the hallway bulb is out" 💬
———
🤖 "Thanks Vera, we've noted it and will replace the bulb tomorrow." 🤖
———
🎫 Hallway bulb out – 720-201 🎫
   Edy · routine · due Fri 9 Oct
———
⏰ Check the bulb task – Wed 09:00 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Task]
[🎫 Create Task]
[✅ Close Reminder]
```

Variants to test: a second message within 5 h → no new after-hours message (`🌙 already sent 22:15`);
weekend / US federal holiday → also after hours; the Send Answer press at 22:20 → held until 08:00 (SMS hours),
button shows `⏳ Answer will be sent 08:00 · Andy`.

### A13. Tenant writes URGENT after hours

```
📨 TENANT MESSAGE · 6 Oct, Tue 23:40 ET

🏠 630-214 · 👤 Mark Chen · 🔴 Urgent · 📞 Farid called 23:40 – answered

———
↪️ AI (sent) "Thanks Mark, we've logged both and will schedule a
visit." ↪️

💬 "URGENT the fridge stopped, all food is going bad" 💬
———
🤖 "Hi Mark, we got your urgent message and are on it. We'll call you
shortly." 🤖
———
🎫 Fridge not working – 630-214 🎫
   Edy · 🔴 urgent · due Wed 7 Oct 23:40
———
⏰ Check the fridge task – Wed 00:10 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Task]
[🎫 Create Task]
[✅ Close Reminder]
```

Variants: "not urgent" in the text does not count; URGENT during office hours = 🔴 alert, no call.

### A15. Nothing to do → no alert

The tenant writes "ok thanks 👍" → no alert at all.

### A16. Test apartment

Same as A1, but:

```
🏠 Test_Apart2 · 👤 Vera Lopez · 🟢 Routine · 🧪 TEST
…
📤 AI sends to chat: OFF (test) · 🎫 AI Auto ClickUp: OFF
```

Buttons work for real (rule 1.1.2): `🤖 Send Answer` really sends to the tenant. Nothing is sent by the AI by itself.

### A17. Tenant writes again before anyone pressed

The old alert (A1) changes to (buttons removed):

```
⚠️ Outdated – Vera wrote again 14:50, see the newer alert
```

The new alert joins both of Vera's messages into one and is 🔴 urgent now:

```
📨 TENANT MESSAGE · 6 Oct, Tue 14:50 ET

🏠 720-201 · 👤 Vera Lopez · 🔴 Urgent

———
↪️ Edy (team) "Hi Vera, welcome! The wifi details are on the fridge." ↪️

💬 "Hi, the kitchen sink is dripping since yesterday. Now there's water
on the floor" 💬
———
🤖 "Hi Vera, sorry! Please close the valve under the sink for now – we're
sending a plumber today and will confirm the time within the hour." 🤖
———
🎫 Kitchen sink leaking, water on the floor – 720-201 🎫
   Edy · 🔴 urgent · due Wed 7 Oct 14:50
———
⏰ Check the plumber is booked – today 15:20 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Task]
[🎫 Create Task]
[✅ Close Reminder]
```



### A18. Team answered the tenant directly before anyone pressed

The alert changes to:

```
[✅ Edy answered in the chat 14:40 – AI answer not needed]
```

Send Answer and Edit Answer are removed; task / reminder buttons stay. The AI's answer and Edy's are stored for
learning (1.5, G3).

### A19. Very long tenant message (alert split in two)

Tue 19:05 is after office hours: first the AI MESSAGE about the after-hours text is posted, as in A12. Then:

```
(1/2)
📨 TENANT MESSAGE · 6 Oct, Tue 19:05 ET

🏠 720-514 · 👤 Rita Gomez · 🟢 Routine · 🌙 after-hours message sent 19:05

———
↪️ Janna (team) "Hi Rita, the 10th is fine this time, no late fee" ↪️

💬 "Hi, so a few things: first the AC makes a noise at night … (the whole
message, 3 900 characters)" 💬
———
```

```
(2/2)
🤖 "Hi Rita, thanks for the details! We've logged the AC noise, the
balcony door and the parking question, and will come back to you
tomorrow." 🤖
———
🎫1 … 🎫1
🎫2 … 🎫2
———
⏰ Check both tasks have a visit date – Wed 09:00 (1/2) ⏰
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Tasks]
[🎫 Create Task 1] [🎫 Create Task 2]
[✅ Close Reminder]
```

Branch: nothing is cut; the buttons are on the last part; a reply to (1/2) or (2/2) works the same.

### A20. Several messages in a row, some already shown earlier

Vera wrote at 09:10 and 09:12 (one alert), the team pressed only Create Task, nobody answered her. At 11:40 and 13:05
she writes again. The new alert:

```
📨 TENANT MESSAGE · 7 Oct, Wed 13:05 ET

🏠 720-201 · 👤 Vera Lopez · 🔴 Urgent

———
↪️ AI (sent) "Hello Vera, thank you for letting us know. I've logged the dripping kitchen sink, and our maintenance team will follow up to schedule a visit." ↪️

💬 "Hi, any news? The plumber didn't come yesterday. Hello?? I've been
waiting 2 days, this is unacceptable" 💬
🔁 Asked 3 times · waiting 22 h
———
🤖 "Hi Vera, I'm really sorry for the wait. I'm escalating it now and
you'll get a visit time within the hour." 🤖
———
🔄 Task "Kitchen sink dripping – 720-201": make urgent + comment
"Tenant waiting 2 days, upset – schedule today" 🔄
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[🔄 Apply Update]
```

Branches to test: the other side's last message is the **AI answer that was sent** (`↪️ AI (sent)`); four messages
over 4 hours become one `💬` line; messages already shown in an older alert are shown again.

### A21. The other side never wrote

The first message ever in a new chat: no `↪️` line, only the `💬` line.

---



## 4. Example catalog – B. Team messages

No urgency in the header (1.2.1). Posted without sound unless a proposed task is urgent.

### B1. Team gives a visit time

```
🧑‍🔧 TEAM MESSAGE · 6 Oct, Tue 16:10 ET

🏠 720-201 · 👤 Vera Lopez · 🧑‍🔧 Edy

———
↪️ Vera (tenant) "Hi, the kitchen sink is dripping since yesterday" ↪️

💬 "Vera, the plumber comes tomorrow 9–11am, please leave the
gate open" 💬
———
🔄 Comment on task "Kitchen sink dripping – 720-201":
"Plumber visit Wed 7 Oct 9–11am" 🔄
———
⏰ Ask Vera if the sink is fixed – Wed 12:00 (1/2) ⏰
———

↩ Reply to this message for questions, notes or custom actions.

📤 AI sends to chat: ON (live) · 🎫 AI Auto ClickUp: OFF
🔗 AI run: http://68.183.124.79/ai-runs/633/
💬 CRM chat: http://68.183.124.79/chat/CH5f2e…/

[🔄 Apply Update]
[✅ Close Reminder]
```



### B2. Team states a fact for this apartment

```
🧑‍🔧 TEAM MESSAGE · 6 Oct, Tue 16:20 ET

🏠 105 Wilson · 👤 Ana Ruiz · 🧑‍🔧 Edy

———
↪️ Ana (tenant) "Thanks! Where can I leave my bike?" ↪️

💬 "Ana, the bike room is on the 1st floor, next to the mailboxes" 💬
———
📚 "105 Wilson: bike room is on the 1st floor, next to the mailboxes" 📚
   from: Edy's message 6 Oct 16:20
———
✅ Reminder "tell Ana where to leave her bike" closed – answered in the chat
———
(footer)

[🏠📚 Apartment ⭐] [🌍📚 Global]
```



### B3. Team states a fact for all apartments

```
🧑‍🔧 TEAM MESSAGE · 6 Oct, Tue 16:45 ET

🏠 720-514 · 👤 Rita Gomez · 🧑‍🔧 Janna

———
↪️ Rita (tenant) "How can I pay the rent, do you take Zelle?" ↪️

💬 "Rita, yes – you can pay by Zelle to pay@ourcompany.com" 💬
———
📚 "Rent can be paid by Zelle to pay@ourcompany.com" 📚
   from: Janna's message 6 Oct 16:45
———
(footer)

[🏠📚 Apartment] [🌍📚 Global ⭐]
```



### B4. Team says it's fixed

```
🧑‍🔧 TEAM MESSAGE · 7 Oct, Wed 11:15 ET

🏠 720-201 · 👤 Vera Lopez · 🧑‍🔧 Edy

———
↪️ Vera (tenant) "Ok, I'll leave the gate open" ↪️

💬 "Vera, the plumber fixed the sink 👍" 💬
———
🔄 Close task "Kitchen sink dripping – 720-201" 🔄
———
⏰ Ask Vera to confirm the sink works – today 13:15 (1/2, to tenant) ⏰
———
(footer)

[🔄 Close Task]
[✅ Close Reminder]
```



### B5. Team asks the tenant for something

```
🧑‍🔧 TEAM MESSAGE · 6 Oct, Tue 16:30 ET

🏠 651-402 · 👤 Joe Park · 🧑‍🔧 Edy

———
↪️ Joe (tenant) "Ok, what do you need from me for the water bill?" ↪️

💬 "Joe, please send us a photo of the water meter" 💬
———
⏰ Remind Joe about the meter photo – today 18:30 (1/2, to tenant) ⏰
———
(footer)

[✅ Close Reminder]
```

When it fires → C3 (live) / C4 (test).

### B6. Team task that is urgent

```
🧑‍🔧 TEAM MESSAGE · 6 Oct, Tue 17:00 ET

🏠 630-214 · 👤 Mark Chen · 🧑‍🔧 Kevin

———
↪️ Mark (tenant) "The kitchen outlet makes a buzzing sound when I plug
in the kettle" ↪️

💬 "Mark, I'll send an electrician, don't use that outlet" 💬
———
🎫 Kitchen outlet buzzing – 630-214 🎫
   Kevin · 🔴 urgent · due Wed 7 Oct 17:00
———
(footer)

[🎫 Create Task]
```

Posted **with sound** because the task is urgent.

### B7. Nothing to do → no alert

"ok thanks", "👍", "will do" → no alert. (See part 9, note 10.10, about skipping the AI run for these.)

### B8. One-time arrangement for this tenant → no knowledge, no alert

Edy writes "Rita, I'll ask Janna.", then Janna: "Rita, the 10th is fine this time, no late fee".

No alert. This is not knowledge: it is true only for this booking, not for the apartment and not for all apartments
(decision 2026-10-06). The AI keeps it as a note on the case, so it knows it on Rita's next messages, and the reminder
"Janna: decide on Rita's late rent request" closes quietly (only in the report).

## 5. Example catalog – C. Reminders



### C1. Team reminder 1/2, task still open

```
⏰ REMINDER · 6 Oct, Tue 16:34 ET

🏠 720-201 · 👤 Vera Lopez · 🟢 Routine · 1/2 (2/2 tomorrow 10:00)

———
⏰ Check the sink task has a visit date (for Edy) ⏰
   ClickUp: to do · no comment yet
———
↪️ Vera (tenant) "Hi, the kitchen sink is dripping since yesterday" ↪️

💬 AI (sent) "Hello Vera, thank you for letting us know. I've logged the dripping kitchen sink, and our maintenance team will follow up to schedule a visit." 💬
———

↩ Reply to this message for questions, notes or custom actions.

📤 AI sends to chat: ON (live) · 🎫 AI Auto ClickUp: OFF
🔗 AI run: http://68.183.124.79/ai-runs/640/
💬 CRM chat: http://68.183.124.79/chat/CH5f2e…/

[✅ Close Reminder]
```



### C2. Team reminder 2/2 (last)

```
⏰ REMINDER · 7 Oct, Wed 10:00 ET

🏠 720-201 · 👤 Vera Lopez · 🟢 Routine · 2/2 (last)

———
⏰ Check the sink task has a visit date (for Edy) ⏰
   ClickUp: to do · last comment 2026-10-06 17:02 Edy
———
↪️ Vera (tenant) "Hi, the kitchen sink is dripping since yesterday" ↪️

💬 AI (sent) "Hello Vera, thank you for letting us know. I've logged the dripping kitchen sink, and our maintenance team will follow up to schedule a visit." 💬
———
(footer)

[✅ Close Reminder]
[⏰ Remind again in 24 hours]
```

Not pressed → listed in the report as "2/2 no answer".

### C3. Tenant reminder, live

No REMINDER alert. At 18:30 (2 h after Edy's request in B5) the re-check runs:

- Joe still owes the photo → the reminder is **sent to Joe automatically** and the AI MESSAGE D5 is posted.
- Joe already sent it → closed quietly (C5).
- Someone pressed `✅ Close Reminder` on the B5 alert → nothing is sent.

2/2 the next day works the same way. Outside SMS hours the send waits for 08:00 (C8).

### C4. Tenant reminder, test

```
⏰ REMINDER · 6 Oct, Tue 18:30 ET

🏠 Test_Apart2 · 👤 Joe Park · 🟢 Routine · 1/2 (to tenant) · 🧪 TEST

———
↪️ Joe (tenant) "Ok, what do you need from me for the water bill?" ↪️

💬 Edy (team) "Joe, please send us a photo of the water meter" 💬
———
🤖 "Hi Joe, just a reminder to send us the photo of the water meter
when you can. Thanks!" 🤖
———
🧪 Test mode: NOT sent automatically. Press to send for real.
———
(footer)

[🤖 Send now] [✏️ Edit Answer]
[✅ Close Reminder]
```



### C5. Already done → no alert

Joe sent the photo at 17:00. At 18:30 the re-check sees it → the reminder closes quietly (`status_note: done in chat`),
no alert, listed in the report under "closed today".

### C6. ClickUp task closed → "marked as done" message

```
⏰ REMINDER · 7 Oct, Wed 10:00 ET

🏠 720-201 · 👤 Vera Lopez · 🟢 Routine · 2/2

———
🎫 Task "Kitchen sink dripping – 720-201" was CLOSED (complete, 2026-10-07 09:12)
———
↪️ Vera (tenant) "Ok, I'll leave the gate open" ↪️

💬 Edy (team) "Vera, the plumber comes tomorrow 9–11am, please leave
the gate open" 💬
———
🤖 "Hi Vera, our team marked the sink repair as done. If you still have
any problem, just let us know." 🤖
———
(footer)

[🤖 Send Answer] [✏️ Edit Answer]
[✅ Close Reminder]
```

Here the tenant wrote last, so the tenant is the `💬` line and the team is the `↪️` line… no: the order is by time,
older side first (rule 1.3.7). Edy wrote 16:10, Vera answered 16:15 → `↪️ Edy` would be older. Correct order:

```
↪️ Edy (team) "Vera, the plumber comes tomorrow 9–11am, please leave
the gate open" ↪️

💬 Vera (tenant) "Ok, I'll leave the gate open" 💬
```

(Test: the order of the two lines always follows the time of the last message of each side.)

### C7. Deadline reminders

```
⏰ REMINDER · 7 Oct, Wed 21:00 ET

🏠 317 10th St · 👤 Sam Lee · 🔴 Urgent · deadline in 2 h · @Kevin

———
⏰ Sam lands at 23:00 – check the lockbox code works (for Edy) ⏰
   tenant deadline Wed 7 Oct 23:00
———
↪️ Sam (tenant) "I land at 11pm tomorrow, how do I get the keys?" ↪️

💬 AI (sent) "Hi Sam, the lockbox is at the front door, code 7788.
Safe travels!" 💬
———
(footer)

[✅ Close Reminder]
```

The 24 h one is the same without `@Kevin`.

### C8. Reminder due outside hours

- Routine team reminder due 19:00 → fires Wed 09:00 (line `moved from 19:00, after hours`).
- Tenant reminder due 21:30 → sent 08:00 next day (live) / its alert posted 08:00 (test).
- Urgent / emergency team reminders fire at any hour.

---



## 6. Example catalog – D. AI messages (sent by the AI itself)



### D2. After-hours message, test

```
🤖 AI MESSAGE · 6 Oct, Tue 22:15 ET

🏠 Test_Apart2 · 👤 Vera Lopez · 🌙 After-hours message · 🧪 TEST

———
↪️ AI (sent) "Hi Vera, thanks for letting us know…" ↪️

💬 "Also the hallway bulb is out" 💬
———
🤖 "Thanks for your message. Our office is open Mon–Fri 9am–6pm …" 🤖
———
🧪 NOT sent – test mode. In live this text would go to the tenant now.
———
(footer)
```



### D3. Emergency, live

```
🚨 AI MESSAGE · EMERGENCY · 6 Oct, Tue 23:02 ET

🏠 630-429 · 👤 Tom Reyes · 🚨 Emergency

———
↪️ Edy (team) "Hi Tom, welcome! Let us know if you need anything." ↪️

💬 "Water is coming from the ceiling, a lot!!" 💬
———
🤖 "Please turn off the main water valve under the kitchen sink and call
911 if water touches electric outlets. We are calling our team now." 🤖
✅ Sent 23:02
———
📞 Farid called 23:02 – answered
———
🎫 Ceiling leak – 630-429 🎫
   Edy · 🚨 emergency · due Wed 03:02 · ✅ created 23:02 (no wait)
———
⏰ Check the leak is stopped – 23:32 (1/2) ⏰
———
(footer)

[✅ Close Reminder]
```

Variants: `🎫 AI Auto ClickUp: OFF` → `🎫 NOT created – AI Auto ClickUp OFF` + button `[🎫 Create Task]`;
test → `🧪 NOT sent`, `📞 call simulated`, task button instead of auto-create.

### D4. Emergency, Farid doesn't answer

```
📞 Farid called 23:02 (+1 561 460…) – no answer · 23:03 (+1 561 220…) – no answer
⚠️ Nobody answered – someone please call Tom: +1 561 555 0123
```



### D5. Tenant reminder auto-sent (live)

```
🤖 AI MESSAGE · 6 Oct, Tue 18:30 ET

🏠 651-402 · 👤 Joe Park · ⏰ Reminder 1/2 auto-sent

———
↪️ Joe (tenant) "Ok, what do you need from me for the water bill?" ↪️

💬 Edy (team) "Joe, please send us a photo of the water meter" 💬
———
🤖 "Hi Joe, just a reminder to send us the photo of the water meter
when you can. Thanks!" 🤖
———
✅ Sent 18:30 – reminder 1/2 was due, still needed, nobody closed it
⏰ Next: 2/2 tomorrow 10:00
———
(footer)

[✅ Close Reminder]
```

`✅ Close Reminder` stops the 2/2. The 2/2 itself (the last one) has no button: nothing is left to stop.



### D6. Send failed

```
❌ Could not send to Joe (Twilio 30008: unknown error) – retry at 18:33
```

then either `✅ Sent on retry 18:33` or `❌ Retry failed – please send by hand` (the CRM chat link is in the footer).

---



## 7. Example catalog – E. Replies (the team replies to an alert)

Rules:

- A question gets an answer, nothing else.
- A change request is **first explained** (what will change · why · what happens after the press) with a button.
**Nothing changes until someone presses it.** Anyone in the group may press, like all buttons.
- Every bot answer ends with the footer links (AI run, CRM chat).



### E1. Question

```
↩ Andy: why a task for this?

🤖 A dripping sink needs a plumber visit, and visits are tracked in
ClickUp. Nothing is created until you press 🎫 Create Task.
```



### E2. Change a task

```
↩ Andy: task for Kevin, due tomorrow 10am

✏️ I WILL CHANGE
🎫 Kitchen sink dripping – 720-201
   now:   Edy · due Fri 9 Oct 14:34
   after: Kevin · due Wed 7 Oct 10:00
Why: you asked for it.
After the press: the task on the alert shows the new values; it is still
created only with 🎫 Create Task.

[✅ Apply Change]
```

After the press: `[✅ Changed · Andy 14:40]`, and the task block on the alert is updated.

### E3. Edit Answer (button flow)

1. Andy presses `✏️ Edit Answer`. The bot replies in the thread:

```
✏️ Andy, reply to THIS message with the correct answer for Vera.
```

1. Andy replies: `Hi Vera, Edy will come himself today at 5pm to look at it.`
2. The bot shows the text word for word and what else it will do:

```
✏️ NEW ANSWER for Vera
🤖 "Hi Vera, Edy will come himself today at 5pm to look at it." 🤖
✏️ I WILL CHANGE
🔄 add a COMMENT to "Kitchen sink dripping": Edy visits today at 17:00
Why: your answer says it.
After the press: it is done for real.

[🤖 Send Answer]
[✅ Apply Change]
```

1. Each press does only its part: `✅ Answer sent · Andy 14:42`, `✅ Applied · Andy`.

The text goes out exactly as typed – there is no draft step and no "Send this" button. The task comment appears
only when the text names a visit or a time and the task already exists. A rule (`📏 Rule I learned` with
`[📏 Save rule]`) is proposed only when the text or a reply speaks about the future ("next time", "always", "from
now on") – one corrected answer alone is not a rule.

The answer is stored as **corrected** (old + new) for learning in any case (1.5.1).

### E4. A fact in a reply that changes the answer

Under the A3b alert ("The wifi password doesn't work"), before the answer was sent:

```
↩ Andy: the wifi password changed to Sun2026

✏️ NEW ANSWER for Mark
🤖 "Sorry about that! The wifi password has changed. Please use
network MyHome-5G, password Sun2026." 🤖
📚 "WiFi password: Sun2026" 📚
   from: Andy's reply 6 Oct 15:40

[🤖 Send Answer]
[🏠📚 Apartment ⭐] [🌍📚 Global]
```

The draft of the alert was not sent yet, so the reply gives a **new answer** in its place. Nothing is sent or saved
before a press.

### E4b. A correction after the answer was already sent

Under the A3 alert, after `🤖 Send Answer` was pressed (Mark already got the old password):

```
↩ Andy: the wifi password changed to Sun2026

✉️ FOLLOW-UP MESSAGE for Mark (the answer was already sent 15:45)
🤖 "Sorry, my mistake – the wifi password has changed. The correct
password is Sun2026." 🤖
📚 "WiFi password: Sun2026" 📚
   from: Andy's reply 6 Oct 15:50

[🤖 Send Message]
[🏠📚 Apartment ⭐] [🌍📚 Global]
```

An answer that already went out is never written again. The AI writes a **short second message**: no new greeting,
nothing repeated from the first answer, only what is new or corrected. `🤖 Send Message` sends it as one more
message to the tenant.

### E5. Test dry run

```
↩ Andy: test close the task and tell Vera it's done

🧪 DRY RUN – nothing will happen. I would: close task "Kitchen sink
dripping", send "Hi Vera, the sink repair is done…".
```

Same explanation as a normal reply, but **without buttons**.

### E6. "done" on a task

```
↩ Edy: done

✏️ I WILL CHANGE
🔄 Close task "Kitchen sink dripping – 720-201"
⏰ Close reminder "Check the sink task…" (2/2 tomorrow)
Why: you wrote "done".
After the press: the AI proposes the "marked as done" message to Vera (C6).

[✅ Apply Change]
```



### E7. Reply on an outdated alert

```
⚠️ This alert is outdated – please reply to the newer one (link).
```

### E8. A change request about the agent itself

A reply is not always about the alert. When the team writes how the **agent should work** in general ("ClickUp off
means ...", "never create a task for ...", "always ask for the apartment number first"), it is a change request for
the agent's rules - and it is handled from Telegram like every other reply, not sent to a developer. The bot answers
with the plan: the rule(s) word for word, where they go, and a button.

```
↩ Andy: clickup off it's mean only that we not create there task anyhow
        automaticly, but still if team press button it ll be created plus
        for sure you can have read access for that

🤖 Today it already works like this: with ClickUp off no task is created
by itself, a press on 🎫 Create Task always creates it, and I read the
chat's ClickUp tasks. I will write it down as a rule so it stays explicit.
✏️ I WILL CHANGE
📏 AGENT RULE: "When ClickUp writes are OFF, never create a ClickUp task
   by yourself; a task is still created whenever a team member presses the
   button. You always have read access to the chat's ClickUp tasks."
   where: AI Management → Agent - team rules · all apartments
   (already so today - the rule only makes it explicit)
Why: you asked for it.
After the press: the AI follows the rule from its next run on, in every
alert. This alert does not change.

[✅ Apply Change]
```

After the press: `[✅ Changed · Andy 15:32]`. The rule is one line in the prompt **Agent - team rules** (AI Management),
the last part of the agent's instructions; "this apartment only" rules are included only in that apartment's chats.
The same scope and topic again replaces the older line. In a sandbox test the rule is kept sandbox-only.

Two limits, both said in words:

- **Harmful** (give tenants' or the team's data to outsiders, lie to or threaten a tenant, switch off emergency
  handling or the on-call call, give out hidden access codes, send to tenants without any approval, break a law or a
  contract, treat people unequally): `⛔ NOT APPLIED: <why>. Nothing was changed.` - no button.
- **Needs code** (a new button or block, new data in the alert, a new page or integration): `🛠 Needs a change in the
  code, not possible from a reply: <what>` - the rest of the request, if any, is still proposed as rules.

Team rules never override the access-code, safety, emergency or payment rules of the agent.

### E9. An operation from a reply (test reminder)

Some requests are not rules but **operations**: something the system does once. They are a short fixed list; each
one gets its own button and happens only after the press. Today the list has one operation.

```
↩ Andy: send a test reminder in the sandbox apartment to check

🤖 I can make a test reminder on the Sandbox Test apartment.
✏️ I WILL CHANGE
🧪 TEST REMINDER on the "Sandbox Test" apartment (test apartment: nothing
   reaches a tenant), due 1 minute after the press
Why: you asked for it.
After the press: in about 1 minute the ⏰ REMINDER alert comes to this group.

[🧪 Send test reminder]
```

A restart, a deploy or a run of the sandbox story is not an operation: a restart does not change any alert (the
worker always runs the deployed code), and code changes are made outside Telegram - the bot says so in one sentence.
The bot answers only from what it can see (the alert, the run, how the system works). When that does not show the
cause, it says "I do not know" and what it sees - it never lists guessed causes, and it never sends the team to a
developer.

---



## 8. Example catalog – F. Button edge cases, G. Reports



### F1. Answer and task in one press (no confirmation questions)

The bot never asks "are you sure?" or "create it too?" - one press, one action. An alert that has an answer AND a
new task gets one more button that does both:

```
[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Task]
[🎫 Create Task]
```

- `🤖🎫 Send + Create Task` → creates the task(s) and sends the answer: `✅ Answer sent · Andy 14:36`, `✅ Task created · Andy 14:36`.
  If ClickUp fails, the answer is NOT sent and the button stays for a retry.
- `🤖 Send Answer` → only sends the answer, without a question; `🎫 Create Task` stays as its own button.
- The button is shown only while the answer is not sent and a task is not created.

### F3. ClickUp error on Create Task

```
[❌ Task not created – ClickUp error · press to retry]
```



### F4. Two people press

Second press: popup `already done by Andy 14:36`. Nothing happens twice.

### F6. Apply Change on an outdated proposal

The tenant wrote again after the bot explained E2 → `[✅ Apply Change]` is removed and the line
`⚠️ Outdated – Vera wrote again 14:50` is added (same rule as alerts).

### G. End-of-day reports: every day 18:00 ET (weekends and holidays too)

Four messages, each one always sent, also when empty. Every line links to the CRM chat. A long report is split into
parts like an alert (1.2.10).

#### G1. Reminders

```
📋 REMINDERS · 6 Oct, Tue 18:00 ET

OPEN (3)
1. 🔴 720-201 · Vera · Check sink visit date · 2/2 tomorrow 10:00
2. 🟢 651-402 · Joe · Meter photo · 1/2 sent 18:30, 2/2 tomorrow 10:00
3. 🟢 105 Wilson · Ana · Bike storage · 1/2 tomorrow 09:00

CLOSED TODAY (2)
4. 720-514 · Rita · Late rent request – closed by Janna
5. 630-214 · Mark · Got in – closed quietly (done in chat)

[✅ Close 1] [✅ Close 2] [✅ Close 3]
[⏰ Remind tomorrow 4] [⏰ Remind tomorrow 5]
```

Empty:

```
📋 REMINDERS · 6 Oct, Tue 18:00 ET

✅ Nothing for today – no open reminders, none closed.
```



#### G2. ClickUp tasks (only AI-created `[AI]` tasks)

```
📋 CLICKUP TASKS · 6 Oct, Tue 18:00 ET

OPEN (2)
1. 🔴 720-201 · Kitchen sink dripping · Edy · due today 14:34 ⚠️ late
2. 🟢 630-214 · Dryer doesn't start · Edy · due Fri 9 Oct

CLOSED TODAY (1)
3. 630-429 · Ceiling leak · Edy

[✅ Close 1] [✅ Close 2]
```

Empty: `✅ Nothing for today – no open AI tasks, none closed.`

#### G3. Not pressed today + what the AI can learn

```
📋 WAITING FOR A DECISION · 6 Oct, Tue 18:00 ET

1. 720-201 · Vera · 14:34 answer not sent → Edy answered himself 14:40
   AI: "…our team will contact you to schedule a visit."
   Edy: "I'll come today at 5pm to check it."
   📏 Proposed rule: "For small plumbing issues offer a same-day visit."
2. 105 Wilson · Ana · 15:20 nobody answered yet ⚠️

[📏 Save rule 1]
```

Empty: `✅ Nothing waiting – every alert got a decision.`

#### G4. Problems today

```
📋 PROBLEMS TODAY · 6 Oct, Tue 18:00 ET

❌ 1 AI run failed – 630-214 15:10 (Claude timeout)
❌ 1 SMS failed – 651-402 18:30, retry OK
❌ 1 ClickUp error – Create Task 720-201 (OK on retry)
✏️ 3 answers corrected by the team (out of 12 alerts)
⚠️ 1 strange decision – 720-514: AI proposed a task for a payment question
👥 1 extra chat for the same tenant – 720-201 · Vera Lopez has 2 chats, the new one was created today 11:20 (the AI reads both)
📊 Today: 14 runs · 12 alerts · 9 answers sent · 4 tasks · cost $1.84
```

Empty: `✅ No problems today.` plus the 📊 line.

"Extra chat" = a chat created today for a tenant who already had one (it happens by mistake); the AI reads all chats of a
tenant as one history, so nothing is lost, but the team should know. Decision 2026-10-06.

"Strange decision" = the checks that already exist (rejected actions, safety-net alerts, answer says "logged" without
an action) plus answers the team corrected.

---



## 8a. Example catalog – H. Automatic notifications (through the AI)

Today a job sends template texts every day at 08:00 ET without reading the chat: move-in tomorrow, contract not signed
(1 / 3 / 7 days), deposit not received, rent due tomorrow, rent 3 days late, stay ends soon (extension?), move-out
tomorrow, after checkout. So a tenant who wrote "I already paid" still gets "reminder: your rent is due".

New (decisions 2026-10-06): the job no longer sends. It hands each planned notification to the AI, which reads **all
chats of this tenant**, the booking and the payment records, and decides:

- **Still needed** → handled like a tenant reminder (1.3.6): **live** → sent at once, an AI MESSAGE says so (H1);
  **test** → never sent by itself, an AI MESSAGE with `🤖 Send now`.
- **Not needed or doubtful** (the tenant already answered it, says they paid, the plan changed) → **held**: not sent,
  the alert says why and has `📤 Send anyway` (H2, H4).
- **The text**: the template, which the AI may adapt to the conversation (same meaning, same language as the chat, no
  new facts or promises) (H5).
- A template switched OFF on the site is not sent and not given to the AI at all.
- If the AI run fails, the template is sent as it is (a notification is never lost).

The two conversation lines are the same as on a reminder (1.3.7): the last joined message of each side, with names,
the older side first. They come from all chats of the tenant.

### H1. Rent due tomorrow, still needed, live

Vera, 720-201. Rent $2,150 is due Wed 7 Oct and is Pending. Nothing in the chat about it. Tue 6 Oct 08:00.

```
🤖 AI MESSAGE · 6 Oct, Tue 08:00 ET

🏠 720-201 · 👤 Vera Lopez · 📅 Rent due tomorrow

———
↪️ Vera (tenant) "Thanks, got it!" ↪️

💬 Janna (team) "You're welcome, Vera!" 💬
———
🤖 "How are you? Gentle reminder that tomorrow is a due date for the payment. Please, let me know when you send it." 🤖
———
✅ Sent 08:00 – still needed: rent $2,150 due 7 Oct is Pending, nothing about it in the chat
———
(footer)
```

No buttons. Posted without sound.

### H2. Rent due tomorrow, but the tenant says she already paid → held

The same, but yesterday Vera wrote "Hi Janna, I already paid October rent yesterday by Zelle". The CRM still shows the
payment as Pending.

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
(footer)

[📤 Send anyway] [✏️ Edit Answer]
[✅ Close Reminder]
```

Posted with sound: somebody has to look. `📤 Send anyway` sends the text for real → `✅ Sent · Andy 08:12`.

### H4. Move-in tomorrow, the tenant already gave the arrival time → held

Template: "Hey! How are you? What time are you planning to be here tomorrow?" – but Sam wrote yesterday "We land at 2pm
and should be at the apartment around 3:30pm tomorrow".

```
🤖 AI MESSAGE · 6 Oct, Tue 08:00 ET

🏠 317 10th St · 👤 Sam Lee · 📅 Move-in tomorrow · ⏸ HELD

———
↪️ Edy (team) "Hi Sam, your booking from Oct 7 is confirmed!" ↪️

💬 Sam (tenant) "We land at 2pm and should be at the apartment around 3:30pm tomorrow" 💬
———
🤖 "Hey! How are you? What time are you planning to be here tomorrow?" 🤖
———
⏸ NOT sent – Sam already wrote on 5 Oct that they arrive around 3:30pm tomorrow
———
(footer)

[📤 Send anyway] [✏️ Edit Answer]
```

No reminder: nothing has to be checked.

### H5. The text is adapted to the conversation

Move-out tomorrow. Yesterday Joe asked "Can I leave the keys in the lockbox when I go?" and Edy answered "Yes, lockbox
is fine". The template asks what time he leaves; the AI keeps the meaning and fits it to the chat.

```
🤖 AI MESSAGE · 6 Oct, Tue 08:00 ET

🏠 651-402 · 👤 Joe Park · 📅 Move-out tomorrow

———
↪️ Joe (tenant) "Can I leave the keys in the lockbox when I go?" ↪️

💬 Edy (team) "Yes, lockbox is fine" 💬
———
🤖 "Hi Joe! What time do you think you will be leaving tomorrow? I need to arrange the cleaners – standard check-out is 10am. And yes, the keys can go in the lockbox." 🤖
———
✅ Sent 08:00 – still needed: Joe has not said when he leaves
———
(footer)
```

### H6. The tenant has two chats: the answer is in the other one → held

Vera has two chats (one was created by mistake). The rent reminder is planned; in the OTHER chat she wrote "I sent the
October rent this morning". The AI reads both chats as one history.

```
🤖 AI MESSAGE · 6 Oct, Tue 08:00 ET

🏠 720-201 · 👤 Vera Lopez · 📅 Rent due tomorrow · ⏸ HELD

———
↪️ Janna (team) "Hi Vera, your October invoice is ready: $2,150, due Oct 7." ↪️

💬 Vera (tenant) "I sent the October rent this morning" 💬
———
🤖 "How are you? Gentle reminder that tomorrow is a due date for the payment. Please, let me know when you send it." 🤖
———
⏸ NOT sent – Vera wrote on 5 Oct (in her other chat) that she sent the October rent; it is still Pending in the CRM
———
⏰ Janna: check Vera's October payment – today 10:00 (1/2) ⏰
———
(footer)

[📤 Send anyway] [✏️ Edit Answer]
[✅ Close Reminder]
```

The extra chat itself is listed in the "Problems today" report (G4).

---



## 9. Your notes and what changed


| Your note                                                                                                                                                       | What changed                                                                                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Examples for every case, to make tests                                                                                                                          | Parts 3–8: catalog A1–G4 with IDs                                                                                                                                           |
| Don't need Ignore                                                                                                                                               | Removed everywhere                                                                                                                                                          |
| Not sure team messages need urgency                                                                                                                             | Removed from the team header; priority is on the task line, sound only for an urgent task (1.2.1, B6)                                                                       |
| Team KB example: where did the info come from?                                                                                                                  | My example was wrong: the fact was not in the message. Now every knowledge block shows `from:`, and a fact without a source message is not shown (1.4)                      |
| Add the CRM chat link everywhere                                                                                                                                | In the footer of every alert, in bot answers and in report lines (1.2.11)                                                                                                   |
| Include earlier messages of the same person in a row + the last message from the other side, as one message, in the format `↪️ Edy (team) "…" ↪️` / `💬 "…" 💬` | Rule 1.2.12 and 1.3.7; **every** tenant, team, reminder and AI-message example now uses it                                                                                  |
| Buttons: short text; long messages: split, don't link ("I don't understand link to where")                                                                      | No cutting and no "full text in the link" anywhere. Short button labels; a long alert or report is sent as `(1/2)`, `(2/2)` with the buttons on the last part (1.2.10, A19) |
| Reply: change request only after approval – first explain, then a button applies it                                                                             | Rule 1.1.3; part 7: E2, E3, E6 explain first and have `✅ Apply Change` / `🤖 Send Answer` / `📏 Save rule`; E5 test = same text without buttons; F6                         |
| Why "Remind again in 24 h" if it's tomorrow anyway                                                                                                              | Only on the last reminder 2/2 (1.3.5)                                                                                                                                       |
| "would send, nothing sent" unclear                                                                                                                              | Two clear versions: `✅ Sent to the tenant` (live) and `🧪 NOT sent – test mode…` (test), D1/D2                                                                              |
| Reports: if nothing, still say "nothing for today"                                                                                                              | Every report always sent, empty text given (G1–G4)                                                                                                                          |
| 9.4: messages about a task should update/close it                                                                                                               | 🔄 update block: A9, A10, A20, B1, B4                                                                                                                                       |
| 9.5: no alert if nothing to do                                                                                                                                  | 1.1.5, A15, B7, C5                                                                                                                                                          |
| 9.10: Edit Answer flow?                                                                                                                                         | E3; now with buttons for the answer and the rule (approval first)                                                                                                           |
| 9.11: same check for reminders?                                                                                                                                 | Not needed: reminders are created automatically now (F1)                                                                                                                    |
| 9.12: reports every day                                                                                                                                         | Every day 18:00, weekends and holidays too                                                                                                                                  |
| 9.13: learn from what the manager answered                                                                                                                      | G3 + 1.5                                                                                                                                                                    |
| Daily report of errors / wrong logic                                                                                                                            | G4                                                                                                                                                                          |
| 10.1/10.2: test = no auto-send, live = auto; mark confirmed answers for learning                                                                                | 1.3.6, C3/C4, 1.5                                                                                                                                                           |
| 10.3: just 2 reminders                                                                                                                                          | 1.3.2                                                                                                                                                                       |
| 10.4: no buttons for switches                                                                                                                                   | Status only (1.1.4)                                                                                                                                                         |
| 10.5: a press creates the task even in test                                                                                                                     | 1.1.2                                                                                                                                                                       |
| 10.6: no I'll handle; reminders automatic, button = Close Reminder                                                                                              | 1.1.1, 1.1.6, all examples                                                                                                                                                  |
| 10.7: no Approve all, one button = one action                                                                                                                   | 1.1.6; the backend creates the case itself on the first press                                                                                                               |
| 10.8: 3 urgency levels                                                                                                                                          | 🟢 🔴 🚨 (1.2.7)                                                                                                                                                            |
| 10.9: recommended scope + 2 options                                                                                                                             | `⭐` + two buttons (1.4)                                                                                                                                                     |
| 10.10: didn't get it                                                                                                                                            | Explained below                                                                                                                                                             |




**About 10.10, in simple words:** today *every* team message in a tenant chat starts a Claude run (costs money and ~30 s), even "ok thanks", which then ends with no alert. Suggestion: skip Claude for very short messages like "ok", "thanks", "👍", "will do" (a simple word list, no AI). Everything else runs as today. Saves money, the team sees no difference. **Approved.**

---



## 10. Decisions (the former open questions)


| Question                                               | Your answer                                               | Where                |
| ------------------------------------------------------ | --------------------------------------------------------- | -------------------- |
| Tenant reminder: auto-send after 15 min or 2 h?        | Sent when due: 1/2 after 2 h, 2/2 next day; no extra wait | 1.1.1, 1.3.6, C3, D5 |
| Send Answer in a test apartment – really send?         | Yes, it sends                                             | 1.1.2, A16           |
| Emergency reminder 1/2 after 30 min?                   | OK                                                        | D3                   |
| Rule from Edit Answer saved at once?                   | No: proposed, `📏 Save rule` saves it                     | E3                   |
| Skip the Claude run for "ok" / "thanks" from the team? | OK                                                        | 9 (10.10), B7        |


---



## 11. Test strategy and process

### 11.1 The idea

The example catalog (parts 3–8) is the **specification and the test list at the same time**. Every ID (A1 … G4) is a
test case, and the example in this document is its expected result. If an example changes here, the test changes
with it; a bug found later gets a new ID here first.

**For now we test in the sandbox, with the real AI**, one chapter at a time, and you watch the alerts in Telegram:

| Mode | Input | Expected result |
|---|---|---|
| **The story** | the chapters of one stay, in time order (A21, B7, H1, H4, A2, …): the cases of this document | the example of that case in this document |
| **Real conversation** | the messages of a real chat you give (conversation id) | the rules of this document + what the team really did next in that chat |

**The story** (decided 2026-10-07): the cases are not run one by one on a wiped sandbox any more. They are the
chapters of ONE stay of one tenant (Vera Lopez, 105 Wilson Ave 2B, 6 Oct – 19 Dec) in a sandbox apartment of its own,
**"Sandbox Test"**, that nothing else on the site uses. At the start the runner writes the real CRM rows of that stay
on that apartment - the knowledge page, the booking, the payments, the parking spot, the contract - and the chapters
follow each other with nothing wiped in between: every chapter sees what the earlier ones did (messages, tasks,
reminders, the knowledge the team saved, the parking spot booked by a press). Nothing is asked twice; variants of one
moment that can not both happen in one story were dropped (the `story:` block and the chapters are in
`testbed/sandbox_cases.yaml`). After every chapter the sandbox rows are saved, so `rerun` and `run <chapter>` start from
the state that chapter started from.

What is the same as production: the AI's context is built by the production code from those rows - the test injects
nothing into it; the only extra text the AI gets is the sandbox block of the system prompt (the rules the team saves in
a test). What the sandbox still replaces: what would reach somebody - SMS (a row in the sandbox chat), phone calls
(shown, not dialled), the ClickUp list (the TEST list, `[SANDBOX]`), the Telegram group (the test group), global
knowledge and company-wide rules (kept sandbox-only) - and the contract text (there is no DocuSeal submission for the
sandbox booking; the story's contract is a text). Live / test mode and AI Auto ClickUp on / off are what the chapter
says. Both modes run in the **sandbox only**: the DB-only sandbox chat (`/chat/CHSANDBOXAIAGENT00000000000000001/`).
Nothing can reach a real tenant.

### 11.2 The test runner: `manage.py ai_agent_sandbox_test`

```
python manage.py ai_agent_sandbox_test --all                 the whole story, from its first chapter
python manage.py ai_agent_sandbox_test --all --from A5       the story from chapter A5 (from the saved state before it)
python manage.py ai_agent_sandbox_test --cases A1            one chapter, from the saved state before it
python manage.py ai_agent_sandbox_test --cases A1,A2,C3      several chapters, in story order
python manage.py ai_agent_sandbox_test --group A             the A chapters
python manage.py ai_agent_sandbox_test --conversation CHxxxx              a real chat (all its messages)
python manage.py ai_agent_sandbox_test --conversation CHxxxx --last 20    only the last 20 messages
python manage.py ai_agent_sandbox_test --conversation CHxxxx --from 2026-10-01
python manage.py ai_agent_sandbox_test --list                show all cases and the last result of each
```

**Fast time is the default:** reminders, auto-sends and other timers fire at once instead of after 2 h / next day; the
alerts and the check still show the times the real system would use (`today 16:34 (1/2)`), so the timing logic is
tested too. `--real-time` waits the real delays (for a last check of the timers).

Options: `--auto` (go to the next chapter without waiting for you; presses the chapter's `presses:` itself, as the
team would by hand), `--real-time` (see above), `--serve` (stay alive and take the group commands of 11.5).

**What one run does:**

1. **Story start** (only at the first chapter, and on `restart`). The sandbox is wiped: chat messages, AI runs and
   events, issues, reminders, case notes, payments, parking, sandbox knowledge and rules; open `[SANDBOX]` ClickUp
   tasks in the TEST list are closed. Then the story's rows are written on the Sandbox Test apartment: knowledge page,
   tenant, booking, payments, parking spot, contract. All dates are moved forward by whole weeks (the week day stays)
   so nothing of the story is ever already due for the live worker.
2. **The chapter's switches**: live or test mode, AI Auto ClickUp ON / OFF, and its changes of the CRM rows (`crm:` -
   a payment marked paid, the booking extended, …); the **chapter time** (for example Tue 6 Oct 14:40 ET – the AI is
   told this time, so office hours, after hours and holidays work like in the example). The clock only moves forward.
3. **Send the trigger** (the new tenant / team message, a reminder becoming due, a reply or a press on an earlier
   alert, or the 18:00 report) and **run the real agent** on it, exactly like the worker does.
4. **The alert is posted** in Telegram, with a test header and a **🧪 TEST NOTES** block (11.3).
5. **Check**: the result is compared with the expected one (11.4); the verdict is posted as a reply under the alert and
   saved in a report file.
6. **Wait for you** (unless `--auto`): press buttons, reply, then write `next` (or `rerun`, …, see 11.5). What you
   pressed is part of the story: the next chapter starts from it.
7. **Save the state** and go on with the **next chapter**. A chapter of a feature that is not built yet (`not_built:`)
   is skipped with a note and keeps its place in the story.

At the end: a **summary** message in Telegram and in `logs/sandbox_tests/<date-time>/summary.md`:

```
🧪 SANDBOX TEST RUN · 6 Oct, Tue 15:40 ET · group A (21 cases)

✅ 17 passed
❌ 3 failed: A5 (no ⚖️ contract line), A11 (one task instead of two),
   A17 (old alert not marked outdated)
⏭ 1 skipped: A19
💰 cost $2.10 · 14 min

🔗 logs/sandbox_tests/2026-10-06_1526/summary.md
```

### 11.3 How a sandbox alert looks

The agent's alert is the normal alert (parts 3–7), with two additions: a test header line and the test notes block
at the end, before the footer.

```
🧪 SANDBOX TEST · case A1 · 1 of 21

📨 TENANT MESSAGE · 6 Oct, Tue 14:34 ET

🏠 Test_Apart2 · 👤 Vera Lopez · 🟢 Routine · 🧪 TEST

———
↪️ Edy (team) "Hi Vera, welcome! The wifi details are on the fridge." ↪️

💬 "Hi, the kitchen sink is dripping since yesterday" 💬
———
🤖 "Hello Vera, thank you for letting us know. I've logged the dripping kitchen sink, and our maintenance team will follow up to schedule a visit." 🤖
———
🎫 Kitchen sink dripping – Test_Apart2 🎫
   Edy · routine · due Fri 9 Oct 14:34
———
⏰ Check the sink task has a visit date – today 16:34 (1/2) ⏰
———
🧪 TEST NOTES
Checks: routine maintenance → answer + 1 task + reminder 1/2 in 2 h,
no sound, both conversation lines.
Try:
1. Press 🤖🎫 Send + Create Task → the answer is sent and the task is
   created in one press, no question (F1).
2. Press 🎫 Create Task → task "[SANDBOX] Kitchen sink dripping" in the
   TEST list, button turns into ✅ Task created.
3. Press it again → "already done by …" (F4).
Then write "next".
———

↩ Reply to this message for questions, notes or custom actions.

📤 AI sends to chat: OFF (test) · 🎫 AI Auto ClickUp: ON (test list)
🔗 AI run: http://68.183.124.79/ai-runs/701/
💬 CRM chat: http://68.183.124.79/chat/CHSANDBOXAIAGENT00000000000000001/

[🤖 Send Answer] [✏️ Edit Answer]
[🤖🎫 Send + Create Task]
[🎫 Create Task]
[✅ Close Reminder]
```

**Who writes the test notes:** a separate "test assistant" step that knows the case, not the agent itself. The agent
runs exactly as in production and does **not** know it is a test – otherwise its decisions could change and the test
would prove nothing. The test assistant reads the case and the agent's result and writes the notes: what the case
checks and which buttons / replies to try, with what should happen.

In the sandbox, buttons act inside the sandbox only: `🤖 Send Answer` writes the answer into the sandbox chat (no SMS),
`🎫 Create Task` creates a `[SANDBOX]` task in the ClickUp TEST list, apartment knowledge goes to the Sandbox Test
apartment's real page (nothing else uses it), global knowledge and company-wide rules are kept sandbox-only (not
visible to real apartments, deleted by the story start), `🗂 Apply in CRM` really changes the sandbox booking's rows.
Calls to Farid are simulated.

### 11.4 How the result is checked

Every case has an expected result, in two parts:

1. **Structured expectations** in `claude_code_integration_doc/testbed/sandbox_cases.yaml` – checked by code, exact:

```yaml
A1:
  title: Routine maintenance, live
  time: 2026-10-06 14:34
  mode: live            # or test
  clickup: off
  history:
    - {from: team, name: Edy, at: 2026-10-05 11:02, text: "Hi Vera, welcome! The wifi details are on the fridge."}
  trigger:
    - {from: tenant, name: Vera Lopez, text: "Hi, the kitchen sink is dripping since yesterday"}
  expect:
    alert: TENANT MESSAGE
    urgency: routine
    sound: false
    conversation: {before: "Edy (team)", now_contains: "kitchen sink"}
    answer: true
    tasks: 1
    updates: 0
    reminders: [{who: Edy, kind: staff, in: 2h, n: "1/2"}]
    knowledge: []
    legal: false
    buttons: [Send Answer, Edit Answer, Create Task, Close Reminder]
  presses:                       # optional: buttons the runner presses itself in --auto mode
    - {button: "Send + Create Task", expect: "Answer sent"}
```

2. **The example text in this document** (the code block under `### A1.`) – the runner reads it straight from this
   file, so the document stays the only source. A Claude **judge** compares the agent's alert with it **by meaning**,
   not word by word (the answer may be worded differently; it must say the same thing, promise the same, have the
   same blocks and buttons).

The verdict, posted as a reply under the alert:

```
🧪 RESULT · A1 · ✅ PASS

✅ alert type, urgency routine, no sound
✅ ↪️ Edy (team) + 💬 tenant message
✅ answer – same meaning as the example
✅ 1 task (Edy, routine, due +3 days)
✅ reminder 1/2 for Edy in 2 h
✅ buttons: Send Answer, Edit Answer, Create Task, Close Reminder
```

```
🧪 RESULT · A11 · ❌ FAIL

✅ answer – same meaning as the example
❌ tasks: expected 2 (dryer, light), got 1 ("Dryer and light broken")
✅ reminder 1/2 in 2 h
Suggestion: the prompt should say "one task per separate problem".
```

Saved per case in `logs/sandbox_tests/<run>/A1.md`: input, the agent's full alert, buttons pressed, expected vs got,
verdict, link to the AI run report.

**Real conversation mode** has no example to compare with, so it checks:
- the **rules** of this document that apply to every alert (format, `↪️` / `💬` lines, no alert when nothing to do,
  one task per problem, knowledge with a source, no money promises, …);
- **what the team really did next** in the real chat (the next team messages and ClickUp tasks after that point): did
  the AI propose the same thing? Different → shown side by side, with a proposed rule if the AI can learn from it.

### 11.5 Replying during a test

You can reply to the sandbox alert or to the verdict, like to any alert (part 7: questions, change requests,
`test …`). Plus test commands, only in sandbox threads:

| Reply | What happens |
|---|---|
| `next` | go to the next case |
| `rerun` | the same chapter again, from the saved state it started from (for example after a prompt change) |
| `rerun with: <text>` | the same case with a changed tenant / team message |
| `change test: <what>` | change the case itself, for example "change test: make it 23:40, after hours" or "expected 2 tasks here, not 1". The bot explains what it will change in `sandbox_cases.yaml` / in this document and shows `[✅ Apply Change]` (same approval rule as part 7) |
| `accept` | the agent's result is right and the example is wrong: the bot proposes to update the example with the agent's version + `[✅ Apply Change]` |
| `skip` | mark the case skipped, go on |
| `stop` | end the list, post the summary; the run then waits for `continue` / `restart` |
| `continue` | the same as `next`; after `stop` or after a replay: go on with the next case of the list |
| `restart` | clean the whole test chat (the bot's messages and what people wrote) and start the story again from its first chapter (the sandbox is wiped and seeded again) |
| `run A5` / `run A,B` | run other chapters, in story order, from the saved state before the first of them |
| `test-conversation CHxxxx` (optional: `last 20`) | replay a real chat, see 11.6 |

A command is written as a reply to any bot message in the test group, or as a `/command` (`/continue`, `/restart`,
`/run A5`, `/test_conversation CHxxxx`). The most used ones are also buttons: `🧪 Next test · 🧪 Rerun · 🧪 Stop` under the
alert, `▶️ Continue · 🔄 Restart` when nothing is running.

To delete what people wrote, the test bot must be an **admin** of the test group with the "Delete messages" right;
as an admin it also sees commands written without a reply.

A message written in the sandbox chat on the site (`💬 CRM chat` link, "Manager" or "Client") while a run is alive is
handled by the test run as one more step of the case: Manager = Edy (or `Kevin: text`), Client = the tenant.

### 11.6 Real conversation mode in detail

`test-conversation CHxxxx` in the test group (or `--conversation CHxxxx` on the command line; `last 20` / `--last N`
for only the last messages) takes the messages of a real chat and plays them **one by one** into the clean sandbox.
A replay is for **looking**: there is no verdict and no "not passed" message. Under every alert:
`🧪 Next message · ➕ Add to tests · 🧪 Stop`.

`➕ Add to tests` makes a test case of the message you are looking at: the bot proposes the case (with invented names,
unit and codes instead of the real ones) and its example for this document, as `➕ I WILL ADD the test B9 …` with
`[✅ Apply Change]`. Nothing is added before the press; after it `run B9` runs the new case.


- The messages are copied with their real sender roles (tenant / team member names) and real times; messages
  within 1 minute are one burst, like in production.
- The agent runs at **every tenant and team message**, with all earlier messages of that chat as history.
- The real AI answers of that time are in the history as `AI (sent)` only if they were really sent to the tenant.
- The agent reads the **real apartment's knowledge base** (read only), so its answers are realistic; anything it
  saves goes to the sandbox, never to the real apartment.
- Every alert gets `🧪 REPLAY · CH…a91f · message 3 of 12` in the header, and the test notes show what the team really
  did next:

```
🧪 TEST NOTES
Real chat 720-201, 3 Oct 10:12. What the team did next:
- Edy answered at 10:40: "I'll send the plumber tomorrow"
- ClickUp task "Sink leak 720-201" created 10:45
🧪 Next message = message 4 of 12.
➕ Add to tests = make a test case of this message (you approve it first).
```

Nothing is sent to the real tenant, nothing is written to the real chat, the real apartment or its ClickUp List.

### 11.7 Where the test alerts go

All sandbox alerts go to a separate Telegram group, **"[PM] AI TEST"** (`AI_AGENT_SANDBOX_CHAT_ID`), so the team's
"[PM] AI GROUP" stays clean. Until that group exists, they go to "[PM] AI GROUP" with the `🧪 SANDBOX TEST` header.

### 11.8 Automatic tests without Claude (later, for regressions)

The existing testbed (`bash claude_code_integration_doc/testbed/run_tests.sh`: throwaway SQLite, fake Claude, fake
Telegram / Twilio / ClickUp, no cost) gets a file `e2e_alerts_v5.py` that replays the same `sandbox_cases.yaml` with
the **agent's answers recorded during passing sandbox runs** instead of real Claude. It proves our code (layout,
buttons, timers, reports) in one minute on every change, with a fixed clock. The old suites stay green; tests of
removed things (Approve all, ticks, I'll handle, Stop all, 15-minute timer) are deleted or rewritten, listed in the
commit message.

### 11.9 Shadow, then live

1. Deploy with all apartments in test mode (as today): alerts from real traffic, the AI sends nothing by itself, only
   button presses act.
2. About 5 days: read G3 / G4 every day; every wrong alert → a new case (or `--conversation` of that chat) → fix.
3. Exit: no G4 errors from the new code for 3 days in a row, and the team says the alerts are clear.
4. Live one or two apartments first (`Apartment.ai_group_chat_enabled`), then the rest.

### 11.10 Process, step by step

| # | Step | Done when |
|---|---|---|
| 1 | This document approved | the user says OK |
| 2 | Build the runner `ai_agent_sandbox_test` + `sandbox_cases.yaml` (all catalog IDs) + the "[PM] AI TEST" group | `--list` shows all cases |
| 3 | Build the new alerts on a separate branch in a worktree (`/home/superuser/site-alerts-v5`) – the live worker never runs half-done code. The sandbox worker for tests runs from the worktree | – |
| 4 | Run `--group A` … `--group G`, fix, `rerun`, until all pass; adjust cases with `change test:` / `accept` | summary: all ✅ |
| 5 | Run `--conversation` on 5–10 real chats you pick | no rule broken, differences understood |
| 6 | Record the passing runs → `e2e_alerts_v5.py` in the testbed, all old suites green | `run_tests.sh` all PASS |
| 7 | Deploy, **with the user's OK** (production): merge, migrations, prompt reset, `pm2 restart ai-agent --update-env`, `./start.sh`, `pm2 restart telegram-notifications` (18:00 job) | alerts arrive in the new format |
| 8 | Shadow, then live one by one (11.9) | exit criteria met |

**Rollback:** switch `AI_AGENT_ALERT_STYLE=v5|v4` for the first weeks: `v4` + worker restart brings the old card back
without a deploy.

**Rule for later changes:** every change to the alerts = change the example here + `sandbox_cases.yaml` in the same
commit, and rerun that case.

The runner can be built before the new alerts (step 2 before 3): then it runs the catalog against **today's** v4 card
and shows what differs, which is a useful list of what to build.

---

## 12. What changes in code (when approved)

- `mysite/ai_agent/cards.py`: new layouts (parts 3–7), 3 urgency levels, no urgency on team alerts, silent routine
posts, footer with AI run + CRM chat links, the `↪️` / `💬` conversation block (other side's last messages joined
  - this side's messages in a row joined, read from the chat messages, not only `new_messages_text`), splitting long
  alerts into `(1/2)`, `(2/2)` messages with the buttons on the last one.
- `mysite/ai_agent/approval.py`: one button per block (Send, Edit, Create Task N, Apply Update N, Close Reminder N,
Apartment/Global); remove Approve all / ticks / I'll handle / Stop all; button → result label; `🤖🎫 Send + Create Task` (F1), no confirmation questions.
- `mysite/ai_agent/plan.py` + `actions.py`: reminders created at once (no approval); case auto-created on first press;
🔄 update items (comment / close / priority) with their own buttons.
- `mysite/ai_agent/policy.py`: 2 reminders (2 h, next day), emergency 30 min, deadline reminders unchanged,
re-check closes quietly.
- Tenant reminders in live: sent by the worker when due (after the re-check), D5 posted; in test: C4 alert only.
- `knowledge.py`: knowledge needs a source message; approved/corrected answers stored and given to the AI as examples.
- `answer_review.py`: typed replies explain first and get `✅ Apply Change` (today some are done at once); Edit Answer
flow (E3) with Send Answer / Save rule buttons; "test …" = same explanation without buttons.
- New command `ai_agent_daily_report` (G1–G4, `--now` for tests) + `cron.js` job every day 18:00 ET.
- `service.py`: skip the Claude run for trivial staff messages (word list).
- Prompt (`ai_agent_runtime_notes`): the new rules.
- Switch `AI_AGENT_ALERT_STYLE=v5|v4` for rollback (11.6).
- Tests (part 11): new command `ai_agent_sandbox_test` (catalog cases + `--conversation` replay, test notes,
  checker + judge, reply commands), `testbed/sandbox_cases.yaml`, sandbox-only knowledge / rules / `[SANDBOX]` tasks
  wiped by the clean start, `AI_AGENT_SANDBOX_CHAT_ID`, a case-time override for sandbox runs, later
  `testbed/e2e_alerts_v5.py`.

