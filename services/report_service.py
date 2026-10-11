"""Reporting queries used by the Reports menu.

Reports only read data (except that they reuse availability helpers). The
payments report deliberately separates **money collected** (actual payments
received) from **money merely billed**, so the two are never confused.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from models import Bill, Booking, BookingStatus, PaymentKind, Vehicle
from storage import Repository
from services import billing_service, booking_service, vehicle_service


def available_vehicles_report(repo: Repository, start: date, end: date) -> List[Vehicle]:
    """Active vehicles with no overlapping booking in ``[start, end)``."""
    return vehicle_service.available_vehicles(repo, start, end)


def active_and_upcoming_bookings(repo: Repository, today: Optional[date] = None) -> Dict[str, List[Booking]]:
    return booking_service.upcoming_and_active(repo, today)


def customer_booking_history(repo: Repository, customer_id: str) -> List[Booking]:
    return booking_service.booking_history(repo, customer_id)


def payments_collected(repo: Repository, start: date, end: date) -> Dict[str, Decimal]:
    """Money **collected** vs money **billed** for ``[start, end)``.

    ``end`` is exclusive. ``collected`` counts real payment inflows,
    ``billed`` is simply the sum of invoice totals issued in the window -
    it is *not* income until it is paid.
    """
    if end < start:
        raise ValueError("End date must not be before the start date.")

    collected = Decimal("0")      # payments received (inflows)
    refunded = Decimal("0")       # non-deposit refunds given back (outflows)
    deposit_refunded = Decimal("0")
    billed = Decimal("0")         # invoice totals issued (NOT income)

    for payment in repo.payments.values():
        if not start <= payment.payment_date < end:
            continue
        if payment.kind == PaymentKind.PAYMENT:
            collected += payment.amount
        elif payment.is_deposit:
            deposit_refunded += payment.amount
        else:
            refunded += payment.amount

    for bill in repo.bills.values():
        if start <= bill.issue_date < end:
            billed += bill.total

    return {
        "collected": collected,
        "refunded": refunded,
        "deposit_refunded": deposit_refunded,
        "net": collected - refunded - deposit_refunded,
        "billed": billed,
    }


def outstanding_bills(repo: Repository) -> List[Dict]:
    """Every bill with an outstanding balance, largest balance first."""
    rows = []
    for bill in billing_service.list_bills(repo):
        remaining = billing_service.outstanding(repo, bill.bill_id)
        if remaining > 0:
            rows.append({
                "bill": bill,
                "paid": billing_service.paid_total(repo, bill.bill_id),
                "outstanding": remaining,
                "status": billing_service.payment_status(repo, bill.bill_id),
            })
    rows.sort(key=lambda row: row["outstanding"], reverse=True)
    return rows


def most_rented_vehicles(repo: Repository, limit: int = 5) -> List[Dict]:
    """Most frequently rented vehicles (cancelled bookings excluded)."""
    counts: Dict[str, int] = {}
    for booking in repo.bookings.values():
        if booking.status == BookingStatus.CANCELLED:
            continue
        counts[booking.vehicle_id] = counts.get(booking.vehicle_id, 0) + 1

    rows = []
    for vehicle_id, count in counts.items():
        vehicle = repo.vehicles.get(vehicle_id)
        if vehicle is None:
            continue  # keep reports resilient if a record was hand-edited
        rows.append({"vehicle": vehicle, "bookings": count})
    rows.sort(key=lambda row: (-row["bookings"], row["vehicle"].vehicle_id))
    return rows[: max(1, limit)]


def vehicles_under_maintenance(repo: Repository) -> List[Vehicle]:
    return [
        v for v in vehicle_service.list_vehicles(repo)
        if v.status == "Maintenance"
    ]
