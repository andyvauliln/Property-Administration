# CRM apartments ↔ ClickUp channels — map for Phase 4

Generated 2026-09-21 from the CRM database (87 apartments) and the ClickUp channel list (`clickup_channels.json`,
52 unit channels + 16 general ones). Machine-readable copy: `clickup_apartment_map.json`.
It is a snapshot: the ClickUp channel list was read through Claude's ClickUp connection, so ask Claude to
regenerate it after changes in ClickUp or the CRM. Nothing here is used by the code yet, except one piece:
`clickup.channel_for()` now always routes an apartment whose name contains "test" to the shared test
sandbox channel/List, never to a real apartment's channel and never left unrouted (2026-09-22).

Manager review 2026-09-22 (sections 1-3 below) - decisions recorded and applied. All 49 resolved apartments
in section 4 (plus Test_Apart2) are now mapped live: 50/87 CRM apartments mapped. Mapping now lives on
`Apartment.ai_clickup_channel_id` / `ai_clickup_list_id` / `ai_clickup_name` / `ai_clickup_active` (moved off
the short-lived separate `AIClickUpChannel` table 2026-09-22, migrations 0088/0089). `AI_AGENT_CLICKUP_TEST_LIST_ID`
has been removed from production - this mapping is now what actually decides ClickUp routing. Still open:
105 Wilson + 105 Wilson (1) (section 1) need a brand-new ClickUp channel created before they can be mapped.

## Summary

| | Count |
|---|---|
| CRM apartments | 87 |
| … with a ClickUp channel | **51** |
| … **without** a channel, occupied now or booked ahead | **3** |
| … without a channel, no current/upcoming booking | 33 |
| ClickUp unit channels with **no CRM apartment** | **3** |
| Channels that are not backed by a List (tasks have no home) | 3 |
| Channels shared by two CRM apartments | 2 |

How matching works: `630-429 (1/1)` → `630-429` = CRM building `630` + apartment `429` (the `(1/1)` part is ignored).
Houses are matched by address words and marked *please check*.

## 1. Needs a decision — active apartments without a ClickUp channel

| CRM apartment | id | Activity | Tenant chats | ClickUp channel | Tasks list | Match | Manager decision (2026-09-22) |
|---|---|---|---|---|---|---|---|
| 105 Wilson | 84 | occupied now | 0 | `8cm2tkk-4613` | `901329141720` | new | **Done 2026-09-22.** List created in "Local Portfolio" (https://app.clickup.com/9013651059/v/l/li/901329141720), channel created via API once `CLICKUP_API_TOKEN` was set, test message confirmed delivered. Fully mapped. |
| 105 Wilson (1) | 85 | occupied now | 1 | `8cm2tkk-4633` | `901329141721` | new | **Done 2026-09-22.** Same as above (https://app.clickup.com/9013651059/v/l/li/901329141721). Fully mapped. |
| Test_Apart2 | 19 | upcoming booking | 1 | **— none —** |  |  | **No channel needed here.** "Там тикетов не должно быть. Это для тестов." (No tickets should land here, it's for tests.) It already uses the separate sandbox List/channel (`AIClickUpChannel` row, `TEST-AI-sandbox`) via the name-based test routing in `clickup.channel_for()` — it does not need (and should not get) its own real-portfolio channel. |

## 2. ClickUp channels without a CRM apartment

| ClickUp channel | Channel id | Backed by a List | Manager decision (2026-09-22) |
|---|---|---|---|
| 800 Avon St House | `8cm2tkk-4153` | no | **Not our portfolio — skip.** "Обьект не в нашем портфолио. Мы им временно управляли. Фарук этим не занимался." (Not in our portfolio, we managed it temporarily, Farouk never handled it.) No CRM apartment should be matched to this channel. |
| 333-malborough | `6-901326630890-8` | yes | **Not our portfolio — skip.** Same note. |
| 1615-north-lake-dr-house | `6-901326630913-8` | yes | **Not our portfolio — skip.** Same note. |

## 3. Special cases

**Two CRM apartments → one channel** (alerts from both would land in the same channel):
- `630-518-tilak (2/2)` ← 630-518 (id 38, status Unavailable), 630-518Tilak (id 55, status Available)
- `651-402-obr` ← 651-402 (id 42, status Available), 651-402 (OBR) (id 89, status Available)

Manager note: "Обрати внимание, что две из них не активные. Взаимодействуй только с активными." (Two of
these four are not active — only work with the active ones.) CRM `status` didn't resolve this cleanly, so
asked the user directly 2026-09-22. **Resolved and mapped**:
- 630-518Tilak (id 55) confirmed active → `AIClickUpChannel` created: list `901326630885`, channel
  `6-901326630885-8`. 630-518 (id 38) left unmapped.
- 651-402 (OBR) (id 89) confirmed active → `AIClickUpChannel` created: list `901326630912`, channel
  `6-901326630912-8`. 651-402 (id 42) left unmapped.

**Channel is not backed by a List** (messages work, but `CREATE_TICKET` has no list to create the task in):
- 317 10th St. Property ( Tiny Studio) ← 317 10th St
- 630-203 LTR ← 630-203
- 720-312 (1bd) ← 720-312

Manager note: "Не понял." (Didn't understand [the question].) Re-asked the user 2026-09-22 in plain terms;
they chose to create a List for each and link it. **Resolved**: new Lists created in the "Local Portfolio"
ClickUp space (each channel kept, only the List is new) and linked via `AIClickUpChannel`:
- 317 10th St (id 81): List `901329138947` (https://app.clickup.com/9013651059/v/l/li/901329138947), channel `8cm2tkk-4373`.
- 630-203 (id 94): List `901329138948` (https://app.clickup.com/9013651059/v/l/li/901329138948), channel `8cm2tkk-4553`.
- 720-312 (id 93): List `901329138949` (https://app.clickup.com/9013651059/v/l/li/901329138949), channel `8cm2tkk-4353`.

**Note**: `AI_AGENT_CLICKUP_TEST_LIST_ID` is still set in production, so none of the 5 mappings above are
actually used for routing yet (every apartment's tasks still go to the shared test List) until that env
var is removed.

## 4. Mapped apartments (51)

| CRM apartment | id | Activity | Tenant chats | ClickUp channel | Tasks list | Match |
|---|---|---|---|---|---|---|
| 317 10th St | 81 | upcoming booking | 1 | 317 10th St. Property ( Tiny Studio) | **no list** | by address - please check |
| Additional rental | 62 | last booking ended 2026-03-29 | 6 | 422-north-lakeside-drive | yes | by address - please check |
| Boathouse, 2614 Amherst | 92 | last booking ended 2026-03-30 | 1 | 2614-amherst-ln-lake-200-early-checkin-cash | yes | by address - please check |
| 630-107 | 50 | occupied now | 0 | 630-107 (1/1) | yes | exact |
| 630-111 | 69 | occupied now | 5 | 630-111 | yes | exact |
| 630-203 | 94 | never booked | 0 | 630-203 LTR | **no list** | exact |
| 630-208 | 32 | occupied now | 0 | 630-208 (2/2) | yes | exact |
| 630-210 | 76 | occupied now | 5 | 630-210 (2/2) | yes | exact |
| 630-213 | 26 | occupied now | 3 | 630-213- | yes | exact |
| 630-214 | 35 | occupied now | 3 | 630-214 | yes | exact |
| 630-218 | 22 | occupied now | 2 | 630-218 | yes | exact |
| 630-219 | 68 | occupied now | 2 | 630-219 (2/2) | yes | exact |
| 630-222 | 8 | occupied now | 3 | 630-222 (2/2) | yes | exact |
| 630-233 | 73 | occupied now | 9 | 630-233 | yes | exact |
| 630-312 | 41 | occupied now | 5 | 630-312 (1/1) | yes | exact |
| 630-319 | 29 | occupied now | 4 | 630-319 | yes | exact |
| 630-322 | 37 | upcoming booking | 2 | 630-322 (2/2) | yes | exact |
| 630-329 | 24 | occupied now | 5 | 630-329 | yes | exact |
| 630-405 | 72 | occupied now | 4 | 630-405 (1/1) | yes | exact |
| 630-429 | 53 | last booking ended 2026-09-18 | 4 | 630-429 (1/1) | yes | exact |
| 630-505 | 7 | occupied now | 6 | 630-505 | yes | exact |
| 630-513 | 13 | upcoming booking | 4 | 630-513 (2/2) | yes | exact |
| 630-518 | 38 | last booking ended 2023-03-04 | 0 | 630-518-tilak (2/2) | yes | exact |
| 630-518Tilak | 55 | last booking ended 2026-03-17 | 0 | 630-518-tilak (2/2) | yes | exact |
| 630-531 | 14 | occupied now | 4 | 630-531 (1/1) | yes | exact |
| 630-113 | 4 | occupied now | 7 | 630-113 (1/1) | yes | exact |
| 630-507 | 5 | occupied now | 3 | 630-507 | yes | exact |
| 651 -- 210 | 91 | last booking ended 2026-03-22 | 1 | 651-210 (2/2) | yes | exact |
| 651-402 | 42 | last booking ended 2022-03-30 | 0 | 651-402-obr | yes | exact |
| 651-402 (OBR) | 89 | last booking ended 2026-04-02 | 2 | 651-402-obr | yes | exact |
| 651-608 | 77 | occupied now | 2 | 651-608 | yes | exact |
| 651-704 | 16 | occupied now | 4 | 651-704 (2/2) | yes | exact |
| 720-103 | 71 | occupied now | 1 | 720-103 (1/1) | yes | exact |
| 720-213 | 88 | occupied now | 3 | 720-213 | yes | exact |
| 720-305 | 33 | upcoming booking | 5 | 720-305 | yes | exact |
| 720-312 | 93 | occupied now | 0 | 720-312 (1bd) | **no list** | exact |
| 720-408 | 56 | occupied now | 4 | 720-408 | yes | exact |
| 720-409 | 25 | upcoming booking | 2 | 720-409 (2/2) | yes | exact |
| 720-514 | 9 | occupied now | 4 | 720-514 | yes | exact |
| 780-110 | 21 | last booking ended 2026-09-08 | 3 | 780-110 | yes | exact |
| 780-111 | 74 | occupied now | 7 | 780-111 (2/2) | yes | exact |
| 780-206 | 58 | last booking ended 2026-08-24 | 8 | 780-206 (2/2) | yes | exact |
| 780-302 | 64 | last booking ended 2026-08-26 | 2 | 780-302 | yes | exact |
| 780-306 | 59 | last booking ended 2026-04-11 | 0 | 780-306 (2/2) | yes | exact |
| 780-311 | 54 | occupied now | 2 | 780-311 | yes | exact |
| 780-408 | 66 | upcoming booking | 2 | 780-408 (2/2) | yes | exact |
| 780-502 | 3 | occupied now | 2 | 780-502 (2/2) | yes | exact |
| 780-505 | 27 | occupied now | 2 | 780-505 (2/2) | yes | exact |
| 780-511 | 17 | occupied now | 4 | 780-511 (2/2) | yes | exact |
| 780-512 | 70 | occupied now | 8 | 780-512 (1/1) | yes | exact |
| 815 Flamingo | 36 | upcoming booking | 24 | 815-flamingo | yes | by address - please check |

## 5. Apartments without a channel and without current / upcoming bookings (33)

| CRM apartment | id | Activity | Tenant chats | ClickUp channel | Tasks list | Match |
|---|---|---|---|---|---|---|
| Sarasota Mike | 49 | last booking ended 2023-01-31 | 0 | **— none —** |  |  |
| Strand | 78 | last booking ended 2025-03-31 | 0 | **— none —** |  |  |
| Edge 300-1519 | 28 | last booking ended 2026-02-28 | 0 | **— none —** |  |  |
| Whitney 628 | 60 | last booking ended 2025-10-30 | 0 | **— none —** |  |  |
| 550-521 | 67 | last booking ended 2024-11-30 | 0 | **— none —** |  |  |
| El Vidado Guest | 12 | last booking ended 2025-03-31 | 0 | **— none —** |  |  |
| El Vidado Main | 11 | last booking ended 2025-03-25 | 0 | **— none —** |  |  |
| 630-104 | 39 | last booking ended 2023-07-28 | 0 | **— none —** |  |  |
| 630-110 | 63 | last booking ended 2025-09-20 | 0 | **— none —** |  |  |
| 630-202 | 23 | last booking ended 2025-12-24 | 0 | **— none —** |  |  |
| 630-313 | 82 | last booking ended 2025-12-11 | 0 | **— none —** |  |  |
| 630-402 | 18 | last booking ended 2025-11-14 | 1 | **— none —** |  |  |
| 630-407 | 80 | never booked | 0 | **— none —** |  |  |
| 630-412 | 20 | last booking ended 2024-07-26 | 0 | **— none —** |  |  |
| 630-521 | 1 | last booking ended 2025-09-16 | 1 | **— none —** |  |  |
| 651-1002 | 43 | last booking ended 2026-01-31 | 1 | **— none —** |  |  |
| Tanya 1 bedroom | 47 | last booking ended 2022-04-22 | 0 | **— none —** |  |  |
| 651-202 | 45 | last booking ended 2025-03-31 | 0 | **— none —** |  |  |
| 651-206 | 44 | last booking ended 2024-04-09 | 0 | **— none —** |  |  |
| 651-208 | 31 | last booking ended 2025-03-31 | 0 | **— none —** |  |  |
| Tanya 2bedroom | 48 | last booking ended 2023-03-04 | 0 | **— none —** |  |  |
| 651-503 | 30 | last booking ended 2023-09-30 | 0 | **— none —** |  |  |
| 651-702 | 34 | last booking ended 2024-03-31 | 0 | **— none —** |  |  |
| 720-313 | 40 | last booking ended 2023-03-31 | 0 | **— none —** |  |  |
| 720-402 | 57 | last booking ended 2026-03-31 | 2 | **— none —** |  |  |
| 720-413 | 15 | last booking ended 2024-10-11 | 0 | **— none —** |  |  |
| 720-510 | 61 | last booking ended 2024-08-17 | 0 | **— none —** |  |  |
| 720-513 | 51 | last booking ended 2023-09-20 | 0 | **— none —** |  |  |
| 780-105 | 75 | last booking ended 2025-04-06 | 0 | **— none —** |  |  |
| 780-109 | 10 | last booking ended 2025-08-31 | 1 | **— none —** |  |  |
| 780-314 | 79 | last booking ended 2025-04-13 | 0 | **— none —** |  |  |
| 780-409 | 46 | last booking ended 2023-01-03 | 0 | **— none —** |  |  |
| 780-402 | 2 | last booking ended 2025-06-29 | 0 | **— none —** |  |  |

## General ClickUp channels (not per apartment)

`cleanings-discussions`, `wpb-entire-team`, `payments-IN`, `all-wpb-portfolio`, `future-reservations`, `sops`, `Payments/Invoices`, `Rental Guru`, `Local Portfolio`, `Local Portfolio`, `Teams Space`, `RG`, `Welcome`, `List`, `Projects`, `Channels LIST`
