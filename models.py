"""Data models for the Vehicle Rental Management System.

All monetary values are ``decimal.Decimal`` (never float). Dates are
``datetime.date`` objects. Instances of these dataclasses are what get
pickled by :mod:`storage`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Enumerations (kept as plain constants so pickled data stays simple)
# ---------------------------------------------------------------------------

VEHICLE_TYPES = ("car", "bike", "suv", "van")
FUEL_TYPES = ("petrol", "diesel", "electric", "hybrid", "cng")


class VehicleStatus:
    ACTIVE = "Active"
    MAINTENANCE = "Maintenance"
    RETIRED = "Retired"

    ALL = (ACTIVE, MAINTENANCE, RETIRED)


class BookingStatus:
    BOOKED = "Booked"          # reserved, not picked up yet
    PICKED_UP = "Picked Up"    # vehicle handed to customer
    RETURNED = "Returned"      # vehicle back, history preserved
    CANCELLED = "Cancelled"    # cancelled before pickup

    ALL = (BOOKED, PICKED_UP, RETURNED, CANCELLED)
    # Statuses that occupy the vehicle for a date range
    OCCUPYING = (BOOKED, PICKED_UP, RETURNED)


class PaymentKind:
    PAYMENT = "payment"
    REFUND = "refund"

    ALL = (PAYMENT, REFUND)


class PaymentStatus:
    UNPAID = "Unpaid"
    PARTIAL = "Partially Paid"
    PAID = "Paid"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class Customer:
    customer_id: str
    name: str
    phone: str
    email: str
    address: str
    license_number: str
    created_at: str = field(default_factory=now_iso)

    def matches(self, query: str) -> bool:
        """Case-insensitive substring match on id, name, phone or licence."""
        q = query.strip().lower()
        if not q:
            return False
        return any(
            q in value.lower()
            for value in (self.customer_id, self.name, self.phone, self.license_number, self.email)
        )


@dataclass
class Vehicle:
    vehicle_id: str
    registration_number: str
    make: str
    model: str
    year: int
    vehicle_type: str          # one of VEHICLE_TYPES
    fuel_type: str             # one of FUEL_TYPES
    seating_capacity: int
    daily_rate: Decimal
    status: str = VehicleStatus.ACTIVE  # one of VehicleStatus.ALL
    created_at: str = field(default_factory=now_iso)

    @property
    def label(self) -> str:
        return f"{self.make} {self.model} ({self.registration_number})"

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return False
        haystack = " ".join(
            [
                self.vehicle_id,
                self.registration_number,
                self.make,
                self.model,
                str(self.year),
                self.vehicle_type,
                self.fuel_type,
                self.status,
            ]
        ).lower()
        return q in haystack


@dataclass
class Booking:
    booking_id: str
    customer_id: str
    vehicle_id: str
    booking_date: date
    pickup_date: date
    expected_return_date: date
    daily_rate: Decimal            # rate locked in at booking time
    security_deposit: Decimal
    status: str = BookingStatus.BOOKED
    actual_return_date: Optional[date] = None
    created_at: str = field(default_factory=now_iso)

    @property
    def rental_days(self) -> int:
        """Billable days: start inclusive, end exclusive, minimum one day."""
        end = self.actual_return_date or self.expected_return_date
        return max(1, (end - self.pickup_date).days)

    @property
    def estimated_cost(self) -> Decimal:
        return self.daily_rate * self.rental_days

    @property
    def late_days(self) -> int:
        if self.actual_return_date is None:
            return 0
        return max(0, (self.actual_return_date - self.expected_return_date).days)

    @property
    def late_charge(self) -> Decimal:
        return self.daily_rate * self.late_days


@dataclass
class Bill:
    bill_id: str
    booking_id: str
    issue_date: date
    rental_charge: Decimal
    late_charge: Decimal = Decimal("0")
    additional_charges: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    deposit_held: Decimal = Decimal("0")
    deposit_deducted: Decimal = Decimal("0")
    deposit_refunded: Decimal = Decimal("0")
    created_at: str = field(default_factory=now_iso)

    def recalculate_total(self) -> Decimal:
        self.total = (
            self.rental_charge
            + self.late_charge
            + self.additional_charges
            - self.discount
        )
        if self.total < 0:
            self.total = Decimal("0")
        return self.total


@dataclass
class Payment:
    payment_id: str
    bill_id: str
    amount: Decimal
    payment_date: date
    method: str = "cash"
    kind: str = PaymentKind.PAYMENT   # payment | refund
    is_deposit: bool = False          # True for security-deposit refunds
    note: str = ""
    created_at: str = field(default_factory=now_iso)
