"""Tests for bills, charges, partial payments, refunds and deposits."""

import os
import sys
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import RepoTestCase

from models import PaymentKind, PaymentStatus
from services import billing_service, booking_service


class BillGenerationTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.customer = self.make_customer()
        self.vehicle = self.make_vehicle(rate="50.00")
        # 3-day rental at 50.00/day = 150.00, deposit 100.00
        self.booking = self.make_booking(customer=self.customer,
                                         vehicle=self.vehicle,
                                         offset=2, days=3, deposit="100.00")

    def test_generate_bill_computes_rental_charge(self):
        bill = billing_service.generate_bill(self.repo, self.booking.booking_id)
        self.assertEqual(bill.bill_id, "BIL-0001")
        self.assertEqual(bill.rental_charge, Decimal("150.00"))
        self.assertEqual(bill.late_charge, Decimal("0.00"))
        self.assertEqual(bill.total, Decimal("150.00"))
        self.assertEqual(bill.deposit_held, Decimal("100.00"))

    def test_duplicate_bill_prevented(self):
        billing_service.generate_bill(self.repo, self.booking.booking_id)
        with self.assertRaises(ValueError) as ctx:
            billing_service.generate_bill(self.repo, self.booking.booking_id)
        self.assertIn("already exists", str(ctx.exception))
        self.assertEqual(len(self.repo.bills), 1)

    def test_bill_rejected_for_unknown_or_cancelled_booking(self):
        with self.assertRaises(ValueError):
            billing_service.generate_bill(self.repo, "BKG-9999")

        other = self.make_booking(customer=self.customer,
                                  vehicle=self.make_vehicle(rego="CXL-1"),
                                  offset=10)
        booking_service.cancel_booking(self.repo, other.booking_id)
        with self.assertRaises(ValueError):
            billing_service.generate_bill(self.repo, other.booking_id)

    def test_late_return_charges_after_recording_return(self):
        bill = billing_service.generate_bill(self.repo, self.booking.booking_id)
        booking_service.record_pickup(self.repo, self.booking.booking_id)
        late_day = self.booking.expected_return_date + timedelta(days=2)
        booking_service.record_return(self.repo, self.booking.booking_id, late_day)

        refreshed = self.repo.bills[bill.bill_id]
        # 5 rental days (2 extra) + 2 late days as penalty, all at 50.00.
        self.assertEqual(refreshed.rental_charge, Decimal("250.00"))
        self.assertEqual(refreshed.late_charge, Decimal("100.00"))
        self.assertEqual(refreshed.total, Decimal("350.00"))

    def test_same_day_return_bills_minimum_one_day(self):
        booking = self.make_booking(customer=self.customer,
                                    vehicle=self.make_vehicle(rego="SDD-1"),
                                    offset=4, days=2)
        booking_service.record_pickup(self.repo, booking.booking_id)
        booking_service.record_return(self.repo, booking.booking_id,
                                      booking.pickup_date)
        bill = billing_service.generate_bill(self.repo, booking.booking_id)
        self.assertEqual(bill.rental_charge, Decimal("50.00"))  # minimum 1 day

    def test_charges_and_discounts(self):
        bill = billing_service.generate_bill(self.repo, self.booking.booking_id)
        billing_service.add_additional_charge(self.repo, bill.bill_id, "25.50")
        billing_service.apply_discount(self.repo, bill.bill_id, "10.00")
        updated = self.repo.bills[bill.bill_id]
        self.assertEqual(updated.additional_charges, Decimal("25.50"))
        self.assertEqual(updated.discount, Decimal("10.00"))
        self.assertEqual(updated.total, Decimal("165.50"))  # 150 + 25.50 - 10

        with self.assertRaises(ValueError):  # negative charge rejected
            billing_service.add_additional_charge(self.repo, bill.bill_id, "-5")
        with self.assertRaises(ValueError):  # discount may not go below zero
            billing_service.apply_discount(self.repo, bill.bill_id, "999")


class PaymentTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.customer = self.make_customer()
        self.vehicle = self.make_vehicle(rate="50.00")
        self.booking = self.make_booking(customer=self.customer,
                                         vehicle=self.vehicle,
                                         offset=2, days=3)  # total 150.00
        self.bill = billing_service.generate_bill(self.repo, self.booking.booking_id)

    def pay(self, amount):
        return billing_service.process_payment(self.repo, self.bill.bill_id,
                                               Decimal(amount))

    def test_unpaid_then_partially_paid_then_paid(self):
        self.assertEqual(billing_service.payment_status(self.repo, self.bill.bill_id),
                         PaymentStatus.UNPAID)
        self.assertEqual(billing_service.outstanding(self.repo, self.bill.bill_id),
                         Decimal("150.00"))

        self.pay("100.00")
        self.assertEqual(billing_service.payment_status(self.repo, self.bill.bill_id),
                         PaymentStatus.PARTIAL)
        self.assertEqual(billing_service.outstanding(self.repo, self.bill.bill_id),
                         Decimal("50.00"))

        self.pay("50.00")
        self.assertEqual(billing_service.payment_status(self.repo, self.bill.bill_id),
                         PaymentStatus.PAID)
        self.assertEqual(billing_service.outstanding(self.repo, self.bill.bill_id),
                         Decimal("0.00"))

    def test_negative_and_zero_payments_rejected(self):
        with self.assertRaises(ValueError):
            self.pay("-10.00")
        with self.assertRaises(ValueError):
            self.pay("0")
        self.assertEqual(billing_service.paid_total(self.repo, self.bill.bill_id),
                         Decimal("0.00"))

    def test_payment_above_outstanding_rejected(self):
        self.pay("100.00")
        with self.assertRaises(ValueError) as ctx:
            self.pay("60.00")  # only 50.00 left
        self.assertIn("exceeds the outstanding balance", str(ctx.exception))

    def test_explicit_refund_may_exceed_balance(self):
        payment = self.pay("50.00")
        refund = billing_service.process_payment(
            self.repo, self.bill.bill_id, Decimal("100.00"),
            kind=PaymentKind.REFUND, note="goodwill credit reversal"
        )
        self.assertEqual(refund.kind, PaymentKind.REFUND)
        # Net paid: 50 - 100 = -50 (outstanding clamps display at 0... check raw)
        self.assertEqual(billing_service.paid_total(self.repo, self.bill.bill_id),
                         Decimal("-50.00"))
        self.assertEqual(billing_service.outstanding(self.repo, self.bill.bill_id),
                         Decimal("200.00"))
        self.assertEqual(billing_service.payment_status(self.repo, self.bill.bill_id),
                         PaymentStatus.UNPAID)
        self.assertEqual(len(billing_service.payments_for_bill(self.repo,
                                                               self.bill.bill_id)), 2)

    def test_refund_of_a_previous_payment(self):
        payment = self.pay("40.00")
        refund = billing_service.refund_payment(self.repo, payment.payment_id)
        self.assertEqual(refund.kind, PaymentKind.REFUND)
        self.assertEqual(billing_service.paid_total(self.repo, self.bill.bill_id),
                         Decimal("0.00"))
        with self.assertRaises(ValueError):  # refunds cannot be refunded
            billing_service.refund_payment(self.repo, refund.payment_id)

    def test_invalid_payment_kind_rejected(self):
        with self.assertRaises(ValueError):
            billing_service.process_payment(self.repo, self.bill.bill_id,
                                            Decimal("10"), kind="cheque")


class DepositTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.customer = self.make_customer()
        self.vehicle = self.make_vehicle(rate="50.00")
        self.booking = self.make_booking(customer=self.customer,
                                         vehicle=self.vehicle,
                                         offset=2, days=3, deposit="100.00")
        self.bill = billing_service.generate_bill(self.repo, self.booking.booking_id)

    def test_full_deposit_refund(self):
        result = billing_service.settle_deposit(self.repo, self.bill.bill_id, "0")
        self.assertEqual(result["refunded"], Decimal("100.00"))
        self.assertEqual(result["deducted"], Decimal("0.00"))

        # Recorded in history as a deposit refund, not as a bill payment.
        payments = billing_service.payments_for_bill(self.repo, self.bill.bill_id)
        self.assertEqual(len(payments), 1)
        self.assertTrue(payments[0].is_deposit)
        self.assertEqual(payments[0].kind, PaymentKind.REFUND)
        # Bill balance untouched by the deposit refund.
        self.assertEqual(billing_service.outstanding(self.repo, self.bill.bill_id),
                         Decimal("150.00"))

    def test_partial_deposit_deduction(self):
        result = billing_service.settle_deposit(self.repo, self.bill.bill_id, "30.00")
        self.assertEqual(result["deducted"], Decimal("30.00"))
        self.assertEqual(result["refunded"], Decimal("70.00"))
        bill = self.repo.bills[self.bill.bill_id]
        self.assertEqual(bill.deposit_deducted, Decimal("30.00"))
        self.assertEqual(bill.deposit_refunded, Decimal("70.00"))

    def test_deposit_cannot_be_settled_twice_or_over_deducted(self):
        billing_service.settle_deposit(self.repo, self.bill.bill_id, "10.00")
        with self.assertRaises(ValueError):
            billing_service.settle_deposit(self.repo, self.bill.bill_id, "0")

        other = self.make_booking(customer=self.customer,
                                  vehicle=self.make_vehicle(rego="DEP-2"),
                                  offset=10, deposit="100.00")
        other_bill = billing_service.generate_bill(self.repo, other.booking_id)
        with self.assertRaises(ValueError):
            billing_service.settle_deposit(self.repo, other_bill.bill_id, "150.00")


class BillingPersistenceTests(RepoTestCase):
    def test_bills_and_payments_survive_restart(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle(rate="50.00")
        booking = self.make_booking(customer=customer, vehicle=vehicle,
                                    offset=2, days=2)  # 100.00
        bill = billing_service.generate_bill(self.repo, booking.booking_id)
        billing_service.process_payment(self.repo, bill.bill_id, Decimal("60.00"))

        restarted = self.reload()
        stored_bill = restarted.bills[bill.bill_id]
        self.assertEqual(stored_bill.total, Decimal("100.00"))
        self.assertEqual(
            billing_service.outstanding(restarted, bill.bill_id),
            Decimal("40.00"),
        )
        self.assertEqual(
            billing_service.payment_status(restarted, bill.bill_id),
            PaymentStatus.PARTIAL,
        )

    def test_reports_distinguish_collected_from_billed(self):
        from services import report_service
        customer = self.make_customer()
        vehicle = self.make_vehicle(rate="50.00")
        booking = self.make_booking(customer=customer, vehicle=vehicle,
                                    offset=2, days=2)
        bill = billing_service.generate_bill(self.repo, booking.booking_id)
        billing_service.process_payment(self.repo, bill.bill_id, Decimal("40.00"))

        today = __import__("datetime").date.today()
        stats = report_service.payments_collected(
            self.repo, today - timedelta(days=1), today + timedelta(days=1)
        )
        self.assertEqual(stats["collected"], Decimal("40.00"))  # money in hand
        self.assertEqual(stats["billed"], Decimal("100.00"))     # merely invoiced


if __name__ == "__main__":
    __import__("unittest").main()
