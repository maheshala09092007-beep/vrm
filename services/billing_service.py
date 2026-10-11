"""Billing and payments.

Money rules (all ``Decimal``):
* ``rental_charge`` = billable days x daily rate, minimum one day.
* ``late_charge``   = late days x daily rate (actual return after expected).
* ``total``         = rental + late + additional charges - discount,
  floored at zero.
* ``paid``          = sum(payments) - sum(non-deposit refunds).
* ``outstanding``   = total - paid (never negative).
* Security deposits are tracked *separately* on the bill: held, deducted
  and refunded. Deposit refunds are recorded as payment-history entries
  flagged ``is_deposit`` so they never distort the bill balance.

One bill per booking; payments must be positive and may not exceed the
outstanding balance - only explicit refunds may exceed it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from models import Bill, BookingStatus, Payment, PaymentKind, PaymentStatus
from storage import Repository
from validators import parse_date, parse_decimal


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def find_bill_for_booking(repo: Repository, booking_id: str) -> Optional[Bill]:
    for bill in repo.bills.values():
        if bill.booking_id == booking_id:
            return bill
    return None


def get_bill(repo: Repository, bill_id: str) -> Bill:
    key = (bill_id or "").strip().upper()
    bill = repo.bills.get(key)
    if bill is None:
        raise ValueError(f"No bill found with ID '{bill_id}'.")
    return bill


def list_bills(repo: Repository) -> List[Bill]:
    return sorted(repo.bills.values(), key=lambda b: b.bill_id)


def payments_for_bill(repo: Repository, bill_id: str) -> List[Payment]:
    return sorted(
        (p for p in repo.payments.values() if p.bill_id == bill_id),
        key=lambda p: p.payment_id,
    )


# ---------------------------------------------------------------------------
# Charges & totals
# ---------------------------------------------------------------------------

def recalculate_bill(repo: Repository, bill: Bill) -> Bill:
    """Re-derive rental/late charges from the booking and refresh the total."""
    booking = repo.bookings.get(bill.booking_id)
    if booking is None:
        raise ValueError(
            f"Bill {bill.bill_id} references missing booking {bill.booking_id}."
        )
    bill.rental_charge = booking.estimated_cost
    bill.late_charge = booking.late_charge
    bill.recalculate_total()
    repo.save("bills")
    return bill


def generate_bill(repo: Repository, booking_id: str) -> Bill:
    """Create the single bill for a booking (duplicates are rejected)."""
    booking = repo.bookings.get((booking_id or "").strip().upper())
    if booking is None:
        raise ValueError(f"No booking found with ID '{booking_id}'.")
    if find_bill_for_booking(repo, booking.booking_id) is not None:
        raise ValueError(
            f"A bill already exists for booking {booking.booking_id}. "
            "Duplicate bills for the same booking are not allowed."
        )
    if booking.status == BookingStatus.CANCELLED:
        raise ValueError(
            f"Booking {booking.booking_id} is cancelled; no bill can be generated for it."
        )

    bill = Bill(
        bill_id=Repository.next_id(repo.bills, "BIL"),
        booking_id=booking.booking_id,
        issue_date=date.today(),
        rental_charge=booking.estimated_cost,
        late_charge=booking.late_charge,
        deposit_held=booking.security_deposit,
    )
    bill.recalculate_total()
    repo.bills[bill.bill_id] = bill
    repo.save("bills")
    return bill


def add_additional_charge(repo: Repository, bill_id: str, amount, note: str = "") -> Bill:
    """Add an extra charge (damage, cleaning, ...). Amount must be positive."""
    bill = get_bill(repo, bill_id)
    value = parse_decimal(amount, "Additional charge", allow_zero=False)
    bill.additional_charges += value
    bill.recalculate_total()
    repo.save("bills")
    return bill


def apply_discount(repo: Repository, bill_id: str, amount) -> Bill:
    """Apply a discount; the total may never go below zero."""
    bill = get_bill(repo, bill_id)
    value = parse_decimal(amount, "Discount", allow_zero=False)
    ceiling = bill.rental_charge + bill.late_charge + bill.additional_charges - bill.discount
    if value > ceiling:
        raise ValueError(
            f"Discount {value} exceeds the billable charges ({ceiling}); "
            "the total cannot go below zero."
        )
    bill.discount += value
    bill.recalculate_total()
    repo.save("bills")
    return bill


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def paid_total(repo: Repository, bill_id: str) -> Decimal:
    """Net money actually collected for a bill (payments minus refunds)."""
    net = Decimal("0")
    for payment in payments_for_bill(repo, bill_id):
        if payment.kind == PaymentKind.PAYMENT:
            net += payment.amount
        elif not payment.is_deposit:  # deposit refunds are outside the bill balance
            net -= payment.amount
    return net


def outstanding(repo: Repository, bill_id: str) -> Decimal:
    bill = get_bill(repo, bill_id)
    remaining = bill.total - paid_total(repo, bill_id)
    return remaining if remaining > 0 else Decimal("0")


def payment_status(repo: Repository, bill_id: str) -> str:
    bill = get_bill(repo, bill_id)
    if bill.total <= 0:
        return PaymentStatus.PAID
    paid = paid_total(repo, bill_id)
    if paid <= 0:
        return PaymentStatus.UNPAID
    if paid >= bill.total:
        return PaymentStatus.PAID
    return PaymentStatus.PARTIAL


def process_payment(
    repo: Repository,
    bill_id: str,
    amount,
    method: str = "cash",
    kind: str = PaymentKind.PAYMENT,
    note: str = "",
) -> Payment:
    """Record a payment (or an explicit refund) against a bill."""
    bill = get_bill(repo, bill_id)
    value = parse_decimal(amount, "Payment amount", allow_zero=False)

    if kind not in PaymentKind.ALL:
        raise ValueError(f"Payment kind must be one of: {', '.join(PaymentKind.ALL)}.")

    if kind == PaymentKind.PAYMENT:
        due = outstanding(repo, bill.bill_id)
        if value > due:
            raise ValueError(
                f"Payment {value} exceeds the outstanding balance {due}. "
                "Amounts above the balance are only allowed as explicit refunds."
            )
    # Explicit refunds may exceed the outstanding balance by design.

    clean_method = (method or "cash").strip().lower() or "cash"
    payment = Payment(
        payment_id=Repository.next_id(repo.payments, "PAY"),
        bill_id=bill.bill_id,
        amount=value,
        payment_date=date.today(),
        method=clean_method,
        kind=kind,
        is_deposit=False,
        note=note.strip(),
    )
    repo.payments[payment.payment_id] = payment
    repo.save("payments")
    return payment


def refund_payment(repo: Repository, payment_id: str, note: str = "") -> Payment:
    """Reverse a previous payment by recording an explicit refund."""
    source = repo.payments.get((payment_id or "").strip().upper())
    if source is None:
        raise ValueError(f"No payment found with ID '{payment_id}'.")
    if source.kind != PaymentKind.PAYMENT:
        raise ValueError(f"Payment {payment_id} is already a refund.")
    return process_payment(
        repo, source.bill_id, source.amount,
        method=source.method, kind=PaymentKind.REFUND,
        note=note or f"Refund of {source.payment_id}",
    )


def settle_deposit(repo: Repository, bill_id: str, deducted_amount="0") -> Dict:
    """Refund the security deposit, optionally deducting an amount first.

    Returns a dict with ``held``, ``deducted`` and ``refunded`` values. The
    refund itself is recorded in the payment history (flagged as a deposit).
    """
    bill = get_bill(repo, bill_id)
    if bill.deposit_held <= 0:
        raise ValueError(f"Bill {bill.bill_id} has no security deposit held.")
    if bill.deposit_refunded > 0 or bill.deposit_deducted > 0:
        raise ValueError(f"The security deposit for {bill.bill_id} has already been settled.")

    deducted = parse_decimal(deducted_amount, "Deposit deduction", allow_zero=True)
    if deducted > bill.deposit_held:
        raise ValueError(
            f"Deposit deduction {deducted} exceeds the held deposit {bill.deposit_held}."
        )

    refund = bill.deposit_held - deducted
    bill.deposit_deducted = deducted
    bill.deposit_refunded = refund
    repo.save("bills")

    if refund > 0:
        payment = Payment(
            payment_id=Repository.next_id(repo.payments, "PAY"),
            bill_id=bill.bill_id,
            amount=refund,
            payment_date=date.today(),
            method="cash",
            kind=PaymentKind.REFUND,
            is_deposit=True,
            note="Security deposit refund",
        )
        repo.payments[payment.payment_id] = payment
        repo.save("payments")

    return {"held": bill.deposit_held, "deducted": deducted, "refunded": refund}


def bill_summary(repo: Repository, bill_id: str) -> Dict:
    """Everything the CLI/tests need to display one bill."""
    bill = get_bill(repo, bill_id)
    paid = paid_total(repo, bill.bill_id)
    return {
        "bill": bill,
        "paid": paid,
        "outstanding": outstanding(repo, bill.bill_id),
        "status": payment_status(repo, bill.bill_id),
        "payments": payments_for_bill(repo, bill.bill_id),
        "deposit_held": bill.deposit_held,
        "deposit_deducted": bill.deposit_deducted,
        "deposit_refunded": bill.deposit_refunded,
    }
