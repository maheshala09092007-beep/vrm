"""Tests for booking creation, overlap prevention and the rental lifecycle."""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import RepoTestCase

from models import BookingStatus
from services import booking_service, vehicle_service


class BookingCreationTests(RepoTestCase):
    def test_creates_booking_with_generated_id_and_locked_rate(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle(rate="45.00")
        booking = self.make_booking(customer=customer, vehicle=vehicle,
                                    offset=2, days=4, deposit="90.00")
        self.assertEqual(booking.booking_id, "BKG-0001")
        self.assertEqual(booking.customer_id, customer.customer_id)
        self.assertEqual(booking.vehicle_id, vehicle.vehicle_id)
        self.assertEqual(booking.status, BookingStatus.BOOKED)
        self.assertEqual(booking.daily_rate, Decimal("45.00"))
        self.assertEqual(booking.security_deposit, Decimal("90.00"))
        self.assertEqual(booking.estimated_cost, Decimal("180.00"))  # 4 days

    def test_estimated_cost_uses_exclusive_end_and_min_one_day(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle(rate="50.00")
        start, _ = self.future_range(offset=1)
        booking = booking_service.create_booking(
            self.repo, customer.customer_id, vehicle.vehicle_id,
            start, start + timedelta(days=1)  # single day rental
        )
        self.assertEqual(booking.rental_days, 1)
        self.assertEqual(booking.estimated_cost, Decimal("50.00"))

    def test_unknown_customer_or_vehicle_rejected(self):
        vehicle = self.make_vehicle()
        customer = self.make_customer()
        start, end = self.future_range()
        with self.assertRaises(ValueError):
            booking_service.create_booking(self.repo, "CUS-9999",
                                           vehicle.vehicle_id, start, end)
        with self.assertRaises(ValueError):
            booking_service.create_booking(self.repo, customer.customer_id,
                                           "VEH-9999", start, end)

    def test_maintenance_vehicle_cannot_be_booked(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle(status="Maintenance")
        start, end = self.future_range()
        with self.assertRaises(ValueError):
            booking_service.create_booking(self.repo, customer.customer_id,
                                           vehicle.vehicle_id, start, end)

    def test_invalid_dates_rejected(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle()
        today = date.today()
        start, end = self.future_range()
        with self.assertRaises(ValueError):  # return before/at pickup
            booking_service.create_booking(self.repo, customer.customer_id,
                                           vehicle.vehicle_id, start, start)
        with self.assertRaises(ValueError):  # pickup in the past
            booking_service.create_booking(self.repo, customer.customer_id,
                                           vehicle.vehicle_id,
                                           today - timedelta(days=1),
                                           today + timedelta(days=2))

    def test_default_deposit_is_twice_the_daily_rate(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle(rate="40.00")
        start, end = self.future_range()
        booking = booking_service.create_booking(
            self.repo, customer.customer_id, vehicle.vehicle_id, start, end
        )
        self.assertEqual(booking.security_deposit, Decimal("80.00"))


class OverlapTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.customer = self.make_customer()
        self.vehicle = self.make_vehicle()
        self.start, self.end = self.future_range(offset=2, days=3)
        self.first = booking_service.create_booking(
            self.repo, self.customer.customer_id, self.vehicle.vehicle_id,
            self.start, self.end
        )

    def test_overlapping_booking_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            booking_service.create_booking(
                self.repo, self.customer.customer_id, self.vehicle.vehicle_id,
                self.start + timedelta(days=1),
                self.end + timedelta(days=1)
            )
        self.assertIn("overlap", str(ctx.exception))
        self.assertEqual(len(self.repo.bookings), 1)

    def test_fully_contained_booking_rejected(self):
        with self.assertRaises(ValueError):
            booking_service.create_booking(
                self.repo, self.customer.customer_id, self.vehicle.vehicle_id,
                self.start + timedelta(days=1), self.start + timedelta(days=2)
            )

    def test_adjacent_bookings_allowed(self):
        # Next rental starts exactly on the return day (end exclusive).
        booking = booking_service.create_booking(
            self.repo, self.customer.customer_id, self.vehicle.vehicle_id,
            self.end, self.end + timedelta(days=2)
        )
        self.assertEqual(booking.booking_id, "BKG-0002")

    def test_booking_before_existing_range_allowed(self):
        booking = booking_service.create_booking(
            self.repo, self.customer.customer_id, self.vehicle.vehicle_id,
            self.start - timedelta(days=2), self.start
        )
        self.assertEqual(booking.booking_id, "BKG-0002")

    def test_cancelled_booking_frees_the_vehicle(self):
        booking_service.cancel_booking(self.repo, self.first.booking_id)
        retry = booking_service.create_booking(
            self.repo, self.customer.customer_id, self.vehicle.vehicle_id,
            self.start, self.end
        )
        self.assertEqual(retry.booking_id, "BKG-0002")
        # Original record is preserved as history.
        self.assertIn(self.first.booking_id, self.repo.bookings)
        self.assertEqual(self.repo.bookings[self.first.booking_id].status,
                         BookingStatus.CANCELLED)


class RentalLifecycleTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.customer = self.make_customer()
        self.vehicle = self.make_vehicle(rate="50.00")
        self.booking = self.make_booking(customer=self.customer,
                                         vehicle=self.vehicle)

    def test_pickup_and_return(self):
        booking = booking_service.record_pickup(self.repo, self.booking.booking_id)
        self.assertEqual(booking.status, BookingStatus.PICKED_UP)

        start, _ = self.future_range(offset=1, days=3)
        returned = booking_service.record_return(self.repo, self.booking.booking_id,
                                                 start + timedelta(days=3))
        self.assertEqual(returned.status, BookingStatus.RETURNED)
        self.assertEqual(returned.actual_return_date, start + timedelta(days=3))
        self.assertEqual(returned.late_days, 0)

    def test_cannot_pickup_cancelled_booking(self):
        booking_service.cancel_booking(self.repo, self.booking.booking_id)
        with self.assertRaises(ValueError) as ctx:
            booking_service.record_pickup(self.repo, self.booking.booking_id)
        self.assertIn("cancelled", str(ctx.exception))

    def test_double_pickup_rejected(self):
        booking_service.record_pickup(self.repo, self.booking.booking_id)
        with self.assertRaises(ValueError):
            booking_service.record_pickup(self.repo, self.booking.booking_id)

    def test_return_requires_pickup(self):
        with self.assertRaises(ValueError):
            booking_service.record_return(self.repo, self.booking.booking_id)

    def test_return_before_pickup_date_rejected(self):
        booking_service.record_pickup(self.repo, self.booking.booking_id)
        with self.assertRaises(ValueError):
            booking_service.record_return(
                self.repo, self.booking.booking_id,
                self.booking.pickup_date - timedelta(days=1)
            )

    def test_cancel_only_allowed_before_pickup(self):
        booking_service.record_pickup(self.repo, self.booking.booking_id)
        with self.assertRaises(ValueError):
            booking_service.cancel_booking(self.repo, self.booking.booking_id)

    def test_late_days_are_calculated_from_actual_return(self):
        booking_service.record_pickup(self.repo, self.booking.booking_id)
        late_return = self.booking.expected_return_date + timedelta(days=2)
        booking = booking_service.record_return(
            self.repo, self.booking.booking_id, late_return
        )
        self.assertEqual(booking.late_days, 2)
        self.assertEqual(booking.late_charge, Decimal("100.00"))  # 2 x 50


class BookingSearchTests(RepoTestCase):
    def test_search_and_history(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle()
        booking = self.make_booking(customer=customer, vehicle=vehicle)

        by_id = booking_service.search_bookings(self.repo, booking.booking_id)
        self.assertEqual(len(by_id), 1)
        self.assertEqual(booking_service.search_bookings(self.repo, "cancelled"), [])
        self.assertEqual(booking_service.search_bookings(self.repo, vehicle.vehicle_id)[0].booking_id,
                         booking.booking_id)

        history = booking_service.booking_history(self.repo, customer.customer_id)
        self.assertEqual([b.booking_id for b in history], [booking.booking_id])

        with self.assertRaises(ValueError):
            booking_service.booking_history(self.repo, "CUS-9999")

    def test_unknown_booking_id_raises(self):
        with self.assertRaises(ValueError):
            booking_service.get_booking(self.repo, "BKG-9999")


if __name__ == "__main__":
    __import__("unittest").main()
