# Shoppi tutorial

A short tour of the app using the demo shop. It takes about 20 minutes to go
through end to end. For installation and configuration see the [README](README.md).

**Contents**

1. [Start the app and sign in](#1-start-the-app-and-sign-in)
2. [Getting around](#2-getting-around)
3. [Walkthrough: take in a customer and book the job](#3-walkthrough-take-in-a-customer-and-book-the-job)
4. [The screens, one by one](#4-the-screens-one-by-one)
5. [The customer's own intake form](#5-the-customers-own-intake-form)
6. [Working as a mechanic](#6-working-as-a-mechanic)
7. [The assistant](#7-the-assistant)
8. [Setting up your own shop](#8-setting-up-your-own-shop)
9. [Quick reference](#9-quick-reference)

---

## 1. Start the app and sign in

With setup from the README done, load the demo shop (once) and start the server:

```bash
python scripts/seed_demo.py
```

```bash
python scripts/create_dev_login.py
```

```bash
python scripts/run_web.py
```

Open <http://localhost:8000>. The demo shop, **Northside Auto & Truck**, comes with three logins:

| Login | Role | Use it to see |
|---|---|---|
| `owner@northside.test` | Owner | Everything |
| `dev@northside.test` | Owner | Everything (a spare owner login for testing) |
| `sam@northside.test` | Mechanic | The view-only experience and change requests |

Passwords are the defaults at the top of `scripts/seed_demo.py` (first and third login) and `scripts/create_dev_login.py` (second).

The demo shop has four bays, five team members with certifications and shifts, five customers each with a work order (most already booked, one left to schedule), parts, a purchase order on the way, two documents, and one phone call.

To start from an empty shop instead, choose **Create an account** on the sign-in page.

## 2. Getting around

- **Sidebar (left):** every area of the app. A number next to **Intake** means submissions are waiting for review; a number next to **Requests** means something is waiting for a decision.
- **Top bar (right):** **+ New customer intake** starts the intake wizard from anywhere. **✦ Assistant** opens the chat panel.
- **Your name (bottom left):** sign out, or switch shop if you belong to more than one.
- **History tabs:** customers, work orders and calendar items each show who changed them and when.
- All times are shown in the shop's timezone, whatever the timezone of your computer.

## 3. Walkthrough: take in a customer and book the job

This is the core flow. Sign in as an owner and click **+ New customer intake**.

1. **Customer.** Type a name and phone number. If the phone or email matches an existing customer, the wizard offers that record instead of creating a duplicate. You can also choose *Search existing customers instead*.
2. **Vehicle.** Enter a VIN and click **Decode** to fill in year, make, model, engine, fuel type and weight class. Check the result: fuel type decides which certifications a job needs, and weight class decides which bays can take the vehicle. No VIN? Fill the fields in by hand.
3. **Concern & services.** Write the concern in the customer's words, then tick what the visit needs:
   - **Diagnosis** (top of the list) when the cause isn't known yet. It's a normal service with a duration, so it can be scheduled.
   - Any services from the catalog. You can combine them with Diagnosis.
   - If you tick nothing, Diagnosis is added for you.
4. **Schedule.** The wizard lists times that already satisfy every rule: a mechanic certified for the work and on shift, a bay that fits the vehicle and is open, and parts in stock or due in. The first is marked **Best match**; the badges say why. Click **Book**, or **Skip scheduling for now**.
   - If nothing fits, it tells you why (for example, *no one holds the EV high-voltage certification that day*) and what would make it fit.
5. **Done.** Open the work order, or start another intake.

Then look at the result:

- **Calendar** shows the job on the mechanic's and the bay's schedule.
- **Work orders → the new order** shows the services, schedule and parts needed.
- **Inventory** shows the parts as *Reserved*.
- **History** shows each record that was created, with your name.

## 4. The screens, one by one

### Today

The start page. Four counters (jobs today, bays in use, intake waiting for review, items needing attention), today's schedule, work orders not yet scheduled, jobs that need reassigning, certifications about to expire, and parts to reorder. Each links to the place where you deal with it.

### Calendar

The shop's own calendar: jobs, events, shifts, time off, bay closures, parts deliveries, certification expiries and promise times in one place.

- Switch between **Month**, **Week**, **Day** and **Agenda**. In Day view, choose **By mechanic** or **By bay** to see one column per person or bay.
- The checkboxes above the grid show or hide each kind of item.
- **+ New event** (or double-click an empty slot) adds a meeting, reminder, vendor visit or shop closure. Events can repeat daily, weekly or monthly.
- **Drag a job** to move it to another time, mechanic or bay. The scheduler checks the move first. If it breaks a rule, you see which one. An owner or advisor can override most rules with a reason, which is kept in the history. Legally required certifications and double bookings can never be overridden.
- Click any item for its details and history.

### Bays

A live board with one card per bay: the vehicle in it, the mechanic, what's next, and how booked the bay is for the chosen day.

- **Start**, **Pause**, **Resume** and **Finish** update the job's progress.
- **Close bay for a period…** takes a bay out of service (maintenance, broken lift). Jobs already booked there are flagged for reassignment.
- Bays are defined under **Settings → Bays**.

### Intake

The review queue. Forms customers filled in themselves, and drafts created from phone calls, wait here. Nothing is added to your records until you act:

- **Review** opens the wizard pre-filled, and shows any existing customer or vehicle it matches.
- **Dismiss** discards it.

The **Customer intake links** card creates links for the customer form (see [section 5](#5-the-customers-own-intake-form)).

### Customers

Search by name, phone, email, plate or VIN. A customer's page has their vehicles, work orders, notes, phone calls and history.

- **+ Add customer**, **+ Add vehicle**, **Edit**, and **New intake**.
- **Delete** (owners and advisors only) permanently removes the customer with their vehicles, work orders and phone calls. The dialog lists exactly what will go and asks you to type the customer's name. It is refused while one of their jobs is in progress.

### Work orders

Every job from intake to completion. Filter by status: Open, Not scheduled, Scheduled, In progress, Waiting for parts, Completed, Cancelled.

On a work order:

- **Schedule / Reschedule** opens the same time picker as the wizard. **Unschedule** takes it off the calendar.
- **+ Add line** adds a catalog service or a one-off line with a description and price.
- **Find parts** tab searches connected parts catalogs for this vehicle.
- **Mark completed**, **Cancel**, and **Reopen**.
- **Delete** (owners and advisors only) removes the work order and frees its schedule and reserved parts. Use **Cancel** instead if you want to keep the record.
- A red notice appears if a booked job needs reassigning, for example because a certification expired or a bay was closed. **Find a new time** fixes it.

### Inventory

| Tab | What it's for |
|---|---|
| **Parts** | Stock on hand, reserved for booked jobs, available, and on order. **Adjust** corrects a count; **+ Add part** adds one. |
| **Reorder** | Parts at or below their reorder level. **Draft a purchase order for all** turns the list into an order. |
| **Purchase orders** | **Mark as ordered** with an expected delivery date, then **Receive all** when it arrives. Jobs needing those parts aren't offered times before the delivery. |
| **Find parts** | Search supplier catalogs. Upload a supplier's price list as a CSV to search it too. |
| **Suppliers** | Names, contacts and account numbers. |

### Team

- **People & certifications:** each person's role, skill level and certifications with expiry dates. Each certification carries a status badge, so ones that are expiring or expired stand out. **+ Add certification** records a new one.
- **Shifts & time off:** a week view of who works when. Add a shift (optionally repeating weekly) or time off. A mechanic is only offered jobs during their shifts.

Apprentices are only scheduled when a supervisor who holds the job's certifications is on shift.

### Documents

Upload PDFs or text files (manuals, policies, warranty terms), or **+ Write a note**. Documents are indexed so the **Search** box and the assistant can answer from them, for example *"What is our warranty on brake pads?"*

### Calls

Phone-call transcripts. **+ Add transcript** lets you paste or upload one (`.txt`, `.vtt`, `.json`); a connected phone system sends them automatically.

- A call is matched to a customer by phone number. Unmatched calls are under **Not matched to a customer**, where you can match them by hand.
- If the caller asked for service, a draft appears in the **Intake** queue for you to review. Nothing from a call is applied automatically.

### History

Every change in the shop, newest first: who, what, when, and the before and after values. Filter by type of record. Visible to everyone on the team. Deleted records stay here.

### Requests

- **Proposed changes:** edits made by someone with view-only access, and changes drafted by the assistant. **Approve** applies the change exactly as shown; **Decline** discards it. If the record has changed since the request was made, it is marked *Out of date* rather than applied.
- **Edit access:** pending requests from mechanics for edit rights in one area, and who currently has extra access. **+ Grant access** gives someone edit rights for an area, from 4 hours up to *Until revoked*.

### Settings

| Tab | What it's for |
|---|---|
| **Shop** | Name, phone, address, timezone, opening hours, how many apprentices one supervisor may oversee, how long call transcripts are kept, and whether mechanics may read them. |
| **Services** | The service catalog. Each service has steps with a duration, the bay equipment needed, the certifications required, and the parts used. This is what the scheduler works from. One service can be marked as the intake **Diagnosis** option. |
| **Bays** | Each bay's size limits, lift capacity, equipment and its own opening hours. |
| **Members** | Who has a login and their role. **+ Invite someone** creates a one-time link to send them (the app doesn't send email itself). |
| **Integrations** | Optional connections: VIN decoding, parts catalogs, call transcripts, Google Calendar. Owners and advisors only. |

## 5. The customer's own intake form

Customers can enter their own details on a tablet at the counter or from a link you send them. It's a separate, login-free page with no access to shop data.

1. Go to **Intake → Customer intake links** and create a link:
   - **Kiosk link:** reusable, for a tablet at the front desk.
   - **+ One-time link:** for one customer, to text or email. It works once and expires in 14 days.
2. Copy the link when it's shown. For security the full link is only displayed once.
3. The customer goes through four steps: *About you*, *Your vehicle*, *What's going on*, *Review*.
4. Their submission appears in your **Intake** queue. **Review** it to create the customer, vehicle and work order.

The demo shop already has a kiosk link; `seed_demo.py` printed it when it ran.

## 6. Working as a mechanic

Sign out and sign in as `sam@northside.test` to see this.

- **Today** shows *My jobs today*.
- Mechanics can see intake, the calendar, bays, customers, work orders, inventory, the team and documents. They can't see shop settings or integrations, and can only read phone calls if the shop allows it.
- On their own jobs they can press **Start**, **Pause** and **Finish**.
- **Any other change becomes a request.** The mechanic makes the edit as normal, and it is sent to an owner or advisor instead of being applied. They can follow it under **Requests**.
- A banner on each screen offers **Request edit access** for that area, with a reason and a duration. Once an owner or advisor approves, the mechanic's changes in that area apply immediately until the access expires or is removed.
- Mechanics can never delete customers or work orders, even with edit access.

Try it: as Sam, edit a customer's phone number. Sign back in as an owner, open **Requests**, and approve it. Then check **History**.

## 7. The assistant

Click **✦ Assistant** in the top bar. It needs a language model running (Ollama by default; see the README).

It can look things up across the shop and in your documents:

- *"Which bays can take a Class 6 truck on Thursday?"*
- *"Who is certified for EV high-voltage work, and when does it expire?"*
- *"What parts are low?"*
- *"What's our warranty on an engine rebuild?"*
- *"What did Laura Chen say on the phone?"*

It can also draft changes, such as booking a work order or adding a calendar event. A draft appears in the chat with **Confirm** and **Decline**; nothing changes until you confirm. For a mechanic, the draft goes to an owner or advisor like any other request. The assistant can't delete customers or work orders.

Scheduling suggestions come from the same rule-checking scheduler as the rest of the app, not from the language model's judgment.

## 8. Setting up your own shop

After **Create an account** (leave *Start with typical bays and a service catalog* ticked for a head start), work through these in order. Each one feeds the scheduler:

1. **Settings → Shop:** opening hours and timezone.
2. **Settings → Bays:** your bays, what each can lift, and its equipment.
3. **Team → People & certifications:** your mechanics and the certifications they hold, with expiry dates.
4. **Team → Shifts & time off:** who works when.
5. **Settings → Services:** adjust durations, prices and required certifications to match how your shop works.
6. **Inventory → Parts:** stock levels and reorder points; link parts to services under Settings → Services.
7. **Settings → Members:** invite your advisors and mechanics. Link each mechanic's login to their employee record so they see their own jobs.
8. **Intake → Customer intake links:** a kiosk link for the front desk.

## 9. Quick reference

| I want to… | Go to |
|---|---|
| Check in a walk-in customer | **+ New customer intake** (top bar) |
| See who's working on what right now | **Bays** |
| Move a job | **Calendar**, drag it; or the work order → **Reschedule** |
| Find out why a job can't be booked | The time picker lists the reasons and what would fix them |
| Take a bay out of service | **Bays → Close bay for a period…** |
| Record a new certification | **Team → People & certifications → + Add certification** |
| Book time off | **Team → Shifts & time off** |
| Order parts that are running low | **Inventory → Reorder** |
| Receive a delivery | **Inventory → Purchase orders → Receive all** |
| Approve a mechanic's change | **Requests → Proposed changes** |
| Give a mechanic edit rights | **Requests → Edit access → + Grant access** |
| See who changed something | **History**, or the History tab on the record |
| Change the Diagnosis time or price | **Settings → Services** |
| Remove a customer or work order | Its page → **Delete** (owners and advisors) |
| Add a team member's login | **Settings → Members → + Invite someone** |
