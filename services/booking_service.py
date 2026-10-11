"""Booking and rental lifecycle: create, cancel, pickup, return.

Key rules:
* Bookings are never deleted - history is preserved.
* A booking blocks its vehicle for ``[pickup_date, expected_return_date)``
  with the start inclusive and the end exclusive (minimum one day).
* Overlapping bookings for the same vehicle are rejected.
* Only ``Booked`` bookings can be cancelled; cancelled bookings can never be
  picked up.
* Charges are derived from the locked-in ``daily_rate`` and the rental days.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional

from models import Booking, BookingStatus, VehicleStatus
from storage import Repository
from services import vehicle_service
from validators import parse_date, validate_date_range

#: Security deposit charged when the caller does not specify one.
DEFAULT_DEPOSIT_MULTIPLIER = Decimal("2")

#: Free-text match fields for booking search.
SEARCH_FIELDS = ("booking_id", "customer_id", "vehicle_id", "status")


def create_booking(
    repo: Repository,
    customer_id: str,
    vehicle_id: str,
    pickup_date: date,
    expected_return_date: date,
    security_deposit: Optional[Decimal] = None,
) -> Booking:
    """Create a booking for an existing customer and Active vehicle."""
    customer = repo.customers.get((customer_id or "").strip().upper())
    if customer is None:
        raise ValueError(f"No customer found with ID '{customer_id}'. Bookings need an existing customer.")

    vehicle = vehicle_service.get_vehicle(repo, vehicle_id)
    if vehicle.status != VehicleStatus.ACTIVE:
        raise ValueError(
            f"{vehicle.label} is '{vehicle.status}' and cannot be booked. "
            "Only Active vehicles can be rented."
        )

    validate_date_range(pickup_date, expected_return_date)

    conflict = vehicle_service.find_conflict(
        repo, vehicle.vehicle_id, pickup_date, expected_return_date
    )
    if conflict is not None:
        raise ValueError(
            f"{vehicle.label} is already booked from {conflict.pickup_date.isoformat()} "
            f"to {conflict.expected_return_date.isoformat()} "
            f"(booking {conflict.booking_id}); the requested dates overlap."
        )

    if security_deposit is None:
        deposit = (vehicle.daily_rate * DEFAULT_DEPOSIT_MULTIPLIER).quantize(Decimal("0.01"))
    else:
        deposit = Decimal(security_deposit)
        if deposit < 0:
            raise ValueError("Security deposit cannot be negative.")

    booking = Booking(
        booking_id=Repository.next_id(repo.bookings, "BKG"),
        customer_id=customer.customer_id,
        vehicle_id=vehicle.vehicle_id,
        booking_date=date.today(),
        pickup_date=pickup_date,
        expected_return_date=expected_return_date,
        daily_rate=vehicle.daily_rate,
        security_deposit=deposit,
        status=BookingStatus.BOOKED,
    )
    repo.bookings[booking.booking_id] = booking
    repo.save("bookings")
    return booking


def get_booking(repo: Repository, booking_id: str) -> Booking:
    key = (booking_id or "").strip().upper()
    booking = repo.bookings.get(key)
    if booking is None:
        raise ValueError(f"No booking found with ID '{booking_id}'.")
    return booking


def list_bookings(repo: Repository) -> List[Booking]:
    return sorted(repo.bookings.values(), key=lambda b: b.booking_id)


def search_bookings(repo: Repository, query: str) -> List[Booking]:
    if not (query or "").strip():
        raise ValueError("Search term is required.")
    q = query.strip().lower()
    return [
        b for b in list_bookings(repo)
        if any(q in str(getattr(b, field)).lower() for field in SEARCH_FIELDS)
    ]


def cancel_booking(repo: Repository, booking_id: str) -> Booking:
    """Cancel a booking that has not been picked up yet."""
    booking = get_booking(repo, booking_id)
    if booking.status != BookingStatus.BOOKED:
        raise ValueError(
            f"Booking {booking.booking_id} is '{booking.status}' and can no longer be "
            "cancelled. Only bookings that have not been picked up can be cancelled."
        )
    booking.status = BookingStatus.CANCELLED
    repo.save("bookings")
    return booking


def record_pickup(repo: Repository, booking_id: str) -> Booking:
    """Hand the vehicle over. Cancelled/returned bookings are rejected."""
    booking = get_booking(repo, booking_id)
    if booking.status == BookingStatus.CANCELLED:
        raise ValueError(
            f"Booking {booking.booking_id} is cancelled - the vehicle cannot be picked up."
        )
    if booking.status == BookingStatus.PICKED_UP:
        raise ValueError(f"Booking {booking.booking_id} is already picked up.")
    if booking.status == BookingStatus.RETURNED:
        raise ValueError(f"Booking {booking.booking_id} is already returned and closed.")

    booking.status = BookingStatus.PICKED_UP
    repo.save("bookings")
    return booking


def record_return(repo: Repository, booking_id: str, return_date: Optional[date] = None) -> Booking:
    """Record the actual vehicle return and refresh any existing bill."""
    booking = get_booking(repo, booking_id)
    if booking.status != BookingStatus.PICKED_UP:
        raise ValueError(
            f"Booking {booking.booking_id} is '{booking.status}'; "
            "only a picked-up booking can be returned."
        )

    actual = return_date if return_date is not None else date.today()
    if actual < booking.pickup_date:
        raise ValueError(
            f"Return date {actual.isoformat()} cannot be before the pickup date "
            f"{booking.pickup_date.isoformat()}."
        )

    booking.actual_return_date = actual
    booking.status = BookingStatus.RETURNED
    repo.save("bookings")

    # Late charges depend on the actual return date: refresh an existing bill.
    from services import billing_service  # local import avoids a module cycle
    bill = billing_service.find_bill_for_booking(repo, booking.booking_id)
    if bill is not None:
        billing_service.recalculate_bill(repo, bill)

    return booking


def booking_history(repo: Repository, customer_id: str) -> List[Booking]:
    """All bookings for a customer, oldest first (history is preserved)."""
    customer_id = (customer_id or "").strip().upper()
    if customer_id not in repo.customers:
        raise ValueError(f"No customer found with ID '{customer_id}'.")
    history = [b for b in repo.bookings.values() if b.customer_id == customer_id]
    return sorted(history, key=lambda b: (b.booking_date, b.booking_id))


def upcoming_and_active(repo: Repository, today: Optional[date] = None) -> dict:
    """Split bookings into active (out now) and upcoming (reserved)."""
    today = today if today is not None else date.today()
    active = [
        b for b in list_bookings(repo)
        if b.status == BookingStatus.PICKED_UP
    ]
    upcoming = [
        b for b in list_bookings(repo)
        if b.status == BookingStatus.BOOKED and b.expected_return_date >= today
    ]
    return {"active": active, "upcoming": upcoming}
