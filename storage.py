"""Pickle-based persistence layer.

This is the *only* module that touches the disk, so it can later be swapped
out for a SQL implementation without changing the services or the CLI.

Data lives in ``data/`` (created automatically) as five separate files::

    data/customers.pkl   data/vehicles.pkl   data/bookings.pkl
    data/bills.pkl       data/payments.pkl

Safety properties:

* Records are loaded once at start-up and kept in memory (dicts keyed by ID).
* Saving serializes everything *first*, writes it to a temporary file in the
  same directory, then atomically replaces the target with ``os.replace`` -
  a crash can never leave a half-written file.
* The previous version of a file is copied to ``<name>.pkl.bak`` first.
* Missing files simply start empty; corrupt/unreadable files are preserved
  with a ``<name>.pkl.<timestamp>.corrupt`` suffix instead of being
  overwritten, and the application starts with an empty collection for them.

SECURITY: pickle can execute arbitrary code while loading. Only ever load
data files that this application created. Never load pickle files from
untrusted or user-uploaded sources.
"""

from __future__ import annotations

import os
import pickle
import shutil
import tempfile
import time
from datetime import date
from decimal import Decimal
from typing import Callable, Dict

from models import Bill, Booking, Customer, Payment, PaymentKind, Vehicle, VehicleStatus

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Entity name -> file name
FILES: Dict[str, str] = {
    "customers": "customers.pkl",
    "vehicles": "vehicles.pkl",
    "bookings": "bookings.pkl",
    "bills": "bills.pkl",
    "payments": "payments.pkl",
}


class StorageError(Exception):
    """Raised when data cannot be loaded or saved safely."""


def _atomic_write(path: str, payload: bytes) -> None:
    """Write ``payload`` to ``path`` atomically (temp file + os.replace)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    # Keep a backup of the previous, known-good file before replacing it.
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError as exc:  # backup failure must not block the save
            raise StorageError(f"Could not back up {path}: {exc}") from exc

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".pkl", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise StorageError(f"Could not save {path}: {exc}") from exc


class Repository:
    """In-memory cache of every record, backed by pickle files on disk."""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.customers: Dict[str, Customer] = {}
        self.vehicles: Dict[str, Vehicle] = {}
        self.bookings: Dict[str, Booking] = {}
        self.bills: Dict[str, Bill] = {}
        self.payments: Dict[str, Payment] = {}
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ load
    def _path(self, entity: str) -> str:
        return os.path.join(self.data_dir, FILES[entity])

    def _quarantine(self, path: str) -> str:
        """Move a corrupt file aside instead of overwriting it."""
        corrupted = f"{path}.{int(time.time())}.corrupt"
        try:
            shutil.move(path, corrupted)
        except OSError:
            corrupted = path  # fall back to leaving it in place
        return corrupted

    def _load_entity(self, entity: str) -> dict:
        path = self._path(entity)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "rb") as handle:
                data = pickle.load(handle)  # noqa: S301 - trusted, self-written files
            if not isinstance(data, dict):
                raise ValueError(f"expected a dict, found {type(data).__name__}")
            return data
        except Exception as exc:  # corrupt file, truncated pickles, bad perms...
            quarantined = self._quarantine(path)
            self.warnings.append(
                f"Could not read {path} ({exc}). "
                f"File preserved as {os.path.basename(quarantined)}; starting with no {entity}."
            )
            return {}

    def load(self) -> "Repository":
        """Load all records. Missing files are created lazily on first save."""
        os.makedirs(self.data_dir, exist_ok=True)
        self.customers = self._load_entity("customers")
        self.vehicles = self._load_entity("vehicles")
        self.bookings = self._load_entity("bookings")
        self.bills = self._load_entity("bills")
        self.payments = self._load_entity("payments")
        return self

    # ------------------------------------------------------------------ save
    def save(self, entity: str) -> None:
        """Atomically persist one collection after a successful operation."""
        if entity not in FILES:
            raise StorageError(f"Unknown entity '{entity}'.")
        data = getattr(self, entity)
        try:
            payload = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            raise StorageError(f"Could not serialize {entity}: {exc}") from exc
        _atomic_write(self._path(entity), payload)

    def save_all(self) -> None:
        for entity in FILES:
            self.save(entity)

    # ------------------------------------------------------------ uniqueness
    @staticmethod
    def next_id(existing: Dict[str, object], prefix: str) -> str:
        """Return the next unused ``PREFIX-0001`` style identifier."""
        highest = 0
        for key in existing:
            if isinstance(key, str) and key.startswith(prefix + "-"):
                tail = key[len(prefix) + 1:]
                if tail.isdigit():
                    highest = max(highest, int(tail))
        return f"{prefix}-{highest + 1:04d}"

    # -------------------------------------------------------- sample data
    def generate_sample_data(self) -> dict:
        """Create a small, consistent set of records for demos and testing.

        Skipped (returns ``{"created": False}``) when any customers already
        exist, so existing records are never overwritten.
        """
        if self.customers:
            return {"created": False, "reason": "Sample data skipped: customers already exist."}

        today = date.today()

        customers = []
        for name, phone, email, address, licence in [
            ("Alice Morgan", "+1-555-0101", "alice@example.com", "12 River Street", "DL-9001"),
            ("Brian Njeri", "+1-555-0102", "brian@example.com", "48 Hill Avenue", "DL-9002"),
            ("Chen Wei", "+1-555-0103", "chen@example.com", "7 Lake Road", "DL-9003"),
        ]:
            customer = Customer(self.next_id(self.customers, "CUS"), name, phone,
                                email, address, licence)
            self.customers[customer.customer_id] = customer
            customers.append(customer)

        vehicles = []
        for rego, make, model, year, vtype, fuel, seats, rate, status in [
            ("ABC-101", "Toyota", "Corolla", 2021, "car", "petrol", 5, "45.00", VehicleStatus.ACTIVE),
            ("ABC-102", "Honda", "CR-V", 2022, "suv", "diesel", 5, "70.00", VehicleStatus.ACTIVE),
            ("XYZ-201", "Yamaha", "MT-07", 2020, "bike", "petrol", 2, "35.00", VehicleStatus.ACTIVE),
            ("VAN-301", "Ford", "Transit", 2019, "van", "diesel", 9, "85.00", VehicleStatus.MAINTENANCE),
        ]:
            vehicle = Vehicle(self.next_id(self.vehicles, "VEH"), rego, make, model, year,
                              vtype, fuel, seats, Decimal(rate), status=status)
            self.vehicles[vehicle.vehicle_id] = vehicle
            vehicles.append(vehicle)

        # One past (returned) booking and one upcoming booking.
        past_pickup = today.replace(day=1) if today.day > 3 else today
        if past_pickup >= today:
            past_pickup = today
        bookings = []
        specs = [
            (customers[0], vehicles[0], past_pickup, past_pickup, "45.00", "90.00", True),
            (customers[1], vehicles[1], today, today, "70.00", "140.00", False),
        ]
        for customer, vehicle, pickup, ret, rate, deposit, already_returned in specs:
            booking = Booking(
                self.next_id(self.bookings, "BKG"), customer.customer_id,
                vehicle.vehicle_id, today, pickup, ret,
                Decimal(rate), Decimal(deposit),
                status="Returned" if already_returned else "Booked",
                actual_return_date=ret if already_returned else None,
            )
            self.bookings[booking.booking_id] = booking
            bookings.append(booking)

        bills, payments = [], []
        for booking in bookings:
            bill = Bill(self.next_id(self.bills, "BIL"), booking.booking_id, today,
                        rental_charge=booking.estimated_cost,
                        deposit_held=booking.security_deposit)
            bill.recalculate_total()
            self.bills[bill.bill_id] = bill
            bills.append(bill)

        # First bill is paid in full, the second stays unpaid so that
        # outstanding-balance reporting has something to show.
        payment = Payment(self.next_id(self.payments, "PAY"), bills[0].bill_id,
                          bills[0].total, today, method="card")
        self.payments[payment.payment_id] = payment
        payments.append(payment)

        self.save_all()
        return {
            "created": True,
            "customers": len(customers),
            "vehicles": len(vehicles),
            "bookings": len(bookings),
            "bills": len(bills),
            "payments": len(payments),
        }
