# Vehicle Rental Management System

A text-based (CLI) vehicle rental management system written in **pure Python**.
Persistent storage uses Python's built-in `pickle` module - no SQL, SQLite,
MariaDB, MySQL or any other database, and no third-party packages.

> **Phase 1 scope:** Python backend + command-line interface only.
> No website, GUI, HTML, CSS or JavaScript.

## Project overview

The system manages four connected areas of a rental business:

1. **Vehicle availability** - derived from operational status + existing bookings.
2. **Booking and rentals** - create, cancel, pickup, return, with overlap protection.
3. **Customers** - registration, search, updates, unique IDs and licences.
4. **Billing and payments** - bills, late charges, discounts, partial payments,
   outstanding balances, and security deposits.

All money is handled with `decimal.Decimal` (never floating point).

## Features

### Customer management
- Register customers with auto-generated unique IDs (`CUS-0001`, `CUS-0002`, ...).
- Stores name, phone, email, address and driving licence number.
- View all customers; search by ID, name, phone or licence number.
- Update any field with full validation.
- Duplicate IDs and duplicate driving licence numbers are rejected.
- Customers are never deleted, so records tied to bookings are preserved.

### Vehicle management
- Auto-generated unique vehicle IDs (`VEH-0001`, ...).
- Registration number, make, model, year, type (car/bike/suv/van), fuel type,
  seating capacity, daily rate and operational status
  (Active / Maintenance / Retired).
- Add, list, search, update vehicles; change operational status.
- Duplicate registration numbers are rejected.
- Historical vehicles linked to bookings are never deleted.
- **Availability is calculated**, not stored: a vehicle is available for a
  date range only if it is `Active` and no non-cancelled booking overlaps
  that range.

### Booking and rentals
- Bookings require an existing customer and an existing `Active` vehicle.
- Auto-generated booking IDs (`BKG-0001`, ...).
- Stores booking date, pickup date, expected return date, locked daily rate,
  security deposit and status (`Booked`, `Picked Up`, `Returned`, `Cancelled`).
- Estimated rental cost is calculated automatically.
- Pickup must be in the future; return must be after pickup (minimum 1 day).
- **Overlapping bookings on the same vehicle are rejected.**
  Start date is *inclusive*, return date is *exclusive*, so a vehicle returned
  on day X is bookable from day X.
- Cancel (only before pickup), record pickup, record actual return.
- Cancelled bookings can never be picked up.
- Booking history is always preserved.

### Billing and payments
- One bill per booking (`BIL-0001`, ...); duplicates are rejected.
- Charges: rental charge, late charge, additional charges, discount.
- Late-return charge: one daily rate per late day, recalculated automatically
  when the actual return is recorded.
- Payments (`PAY-0001`, ...): partial payments supported.
- Payment status: `Unpaid`, `Partially Paid`, `Paid`.
- Outstanding balance = total - (payments - refunds).
- Negative or zero payments are rejected; payments above the outstanding
  balance are rejected **unless** processed explicitly as a refund.
- Security deposit: held, optionally deducted, then refunded; the refund is
  recorded in the bill's payment history.

### Reports
- Available vehicles for a date range.
- Active and upcoming bookings.
- Booking history for a customer.
- Payments collected during a date range (**collected** money is reported
  separately from **billed** money).
- Outstanding bills.
- Most frequently rented vehicles.
- Vehicles under maintenance.

## Python version requirements

- **Python 3.9 or newer** (developed and tested with Python 3.14).
- **No external packages** - the standard library only
  (`pickle`, `dataclasses`, `decimal`, `datetime`, `argparse`, `unittest`).
  `requirements.txt` documents this; there is nothing to install.

## Installation and execution

```bash
# 1. Get the project (clone or unzip it), then cd into it:
cd vehicle_rental_system

# 2. (Optional) confirm your interpreter
python3 --version

# 3. Run the application - the data/ directory is created automatically
python3 main.py
```

Useful flags:

```bash
python3 main.py --sample                # load sample data if data/ is empty
python3 main.py --data-dir /tmp/vrm     # use a different data directory
python3 main.py --help
```

## Menu options

```
======== Vehicle Rental Management System ========
  1. Customer Management
  2. Vehicle Management
  3. Booking and Rental Management
  4. Billing and Payments
  5. Reports
  6. Exit
```

| Section | Submenu operations |
|---|---|
| 1. Customer Management | Register, view all, search, view details, update |
| 2. Vehicle Management | Add, list, search, update, change status, availability for a date range |
| 3. Booking and Rental Management | Create, list, search, view, cancel, record pickup, record return |
| 4. Billing and Payments | Generate bill, view bill, list bills, add charge, apply discount, process payment, refund payment, settle deposit, payment history |
| 5. Reports | Available vehicles, active/upcoming bookings, customer history, payments collected, outstanding bills, most rented, maintenance vehicles |

Invalid input never crashes the program: bad menu choices, malformed dates
(`YYYY-MM-DD`), non-numeric amounts and unknown IDs all print a clear error
and return you to the current menu.

## Pickle data storage

Records live in five separate files under `data/`:

```
data/
├── customers.pkl
├── vehicles.pkl
├── bookings.pkl
├── bills.pkl
└── payments.pkl
```

The storage layer is isolated in `storage.py` (a `Repository` class) so it can
be swapped for SQL later without touching the services or the CLI.

Safety behaviour:

- The `data/` directory is created automatically if missing.
- Records are loaded once at start-up; every successful operation saves the
  affected collection immediately.
- Saves are **atomic**: data is fully serialized in memory first, written to a
  temporary file in the same directory, then moved into place with
  os.replace - a crash can never leave a half-written file.
- The previous version of a file is copied to `<name>.pkl.bak` before being
  replaced.
- Missing files simply start empty; corrupt/unreadable files are **preserved**
  as `<name>.pkl.<timestamp>.corrupt` (never overwritten), a warning is shown
  at start-up, and the application continues with an empty collection for that
  entity.
- IDs are derived from existing records, so they stay unique across restarts.

> **SECURITY WARNING:** pickle files can execute arbitrary code while loading.
> Only ever load data files generated and trusted by this application.
> Never load pickle files from user uploads or other untrusted sources.

## Sample workflows

### Register a customer and add a vehicle
1. `1` → `1` → enter name, phone, email, address, licence → note the new `CUS-0001`.
2. `0` → `2` → `1` → enter rego, make, model, year, type (`car`), fuel
   (`petrol`), seats, rate → note `VEH-0001`.

### Book, pick up and return a vehicle
1. `3` → `1` → customer `CUS-0001`, vehicle `VEH-0001`, pickup
   `2026-11-03`, return `2026-11-07`, deposit (Enter for default = 2 x daily rate).
2. `3` → `6` → `BKG-0001` to record pickup.
3. `3` → `7` → `BKG-0001` → actual return date (Enter for today).
   If the return is late, the late charge appears immediately.
4. `3` → `4` → `BKG-0001` to view the booking at any time.

### Bill and take payments
1. `4` → `1` → `BKG-0001` generates `BIL-0001` (duplicate attempts are rejected).
2. `4` → `6` → `BIL-0001` → amount → method. Pay less than the total to see
   `Partially Paid`; pay the rest to reach `Paid`.
3. `4` → `8` → `BIL-0001` → deduction (0 for a full refund) to settle the deposit.
4. `5` → `5` lists every bill with money still outstanding.

### Check availability before booking
- `2` → `6` (or `5` → `1`) → enter a start and end date: only `Active`
  vehicles with no overlapping booking are listed.

## Running the automated tests

Tests use **temporary directories**, so the real `data/` records are never
read, modified or deleted.

```bash
python3 -m unittest discover -v          # run everything (76 tests)
python3 -m unittest tests.test_bookings  # run one file
python3 -m unittest discover             # quiet run
```

Coverage: customer creation/validation, duplicate licences, duplicate vehicle
registrations, availability (including half-open date boundaries), booking
overlaps, rental cost and minimum one-day charge, late-return charges,
partial payments and balances, refund rules, duplicate-bill prevention,
deposit settlement, pickle persistence, atomic saves, corruption recovery
and the sample-data generator.

## Project structure

```
vehicle_rental_system/
├── main.py                  # CLI menus and input handling
├── models.py                # dataclasses: Customer, Vehicle, Booking, Bill, Payment
├── storage.py               # pickle repository (atomic save, recovery, sample data)
├── validators.py            # shared input validation (dates, money, enums...)
├── requirements.txt         # no third-party packages
├── README.md
├── .gitignore
├── data/
│   └── .gitkeep             # data files are created at runtime
├── services/
│   ├── customer_service.py
│   ├── vehicle_service.py
│   ├── booking_service.py
│   ├── billing_service.py
│   └── report_service.py
└── tests/
    ├── base.py              # temp-directory test scaffolding
    ├── test_customers.py
    ├── test_vehicles.py
    ├── test_bookings.py
    ├── test_billing.py
    └── test_storage.py
```

## Charge formulas

| Item | Formula |
|---|---|
| Rental days | `(return date - pickup date)` in days, start inclusive / end exclusive, **minimum 1 day** |
| Rental charge | `rental days x daily rate` |
| Late charge | `late days x daily rate` (one daily rate per late day) |
| Bill total | `rental + late + additional - discount`, floored at 0 |
| Outstanding | `total - (payments - refunds)`, never negative |
| Deposit refund | `deposit held - deposit deducted` |

## Known limitations

- **Single user / single process:** pickle files have no locking, so do not run
  two copies of the application against the same `data/` directory at once.
- **No authentication or user accounts** (phase 1 backend only).
- **No GUI/web frontend** - text CLI only, by design for this phase.
- Customers and vehicles cannot be deleted; data is retained for history
  (retire a vehicle by setting its status to `Retired`).
- Search is simple case-insensitive substring matching, not a full-text index.
- Late fees are charged at the daily rate per late day; no configurable fee
  policies or seasonal pricing.
- Security deposit deductions are recorded on the bill but are not added to
  the invoice total (they are money already held, not a new charge).
- Reports cover data currently in `data/`; no export to CSV/PDF yet.
