"""Tests for vehicle registration, validation, status and availability."""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import RepoTestCase

from models import VehicleStatus
from services import booking_service, vehicle_service


class VehicleCreationTests(RepoTestCase):
    def test_add_vehicle_assigns_sequential_ids(self):
        first = self.make_vehicle()
        second = self.make_vehicle(rego="TEST-002", make="Honda", model="Civic")
        self.assertEqual(first.vehicle_id, "VEH-0001")
        self.assertEqual(second.vehicle_id, "VEH-0002")

    def test_duplicate_registration_rejected(self):
        self.make_vehicle(rego="DUP-1")
        with self.assertRaises(ValueError) as ctx:
            self.make_vehicle(rego="dup-1", make="Honda", model="Civic")
        self.assertIn("already belongs to", str(ctx.exception))
        self.assertEqual(len(self.repo.vehicles), 1)

    def test_invalid_attributes_rejected(self):
        bad_cases = [
            dict(rego="OK-1", make="Toyota", model="Corolla", year=1800,
                 vehicle_type="car", fuel_type="petrol", seats=5, rate="50"),
            dict(rego="OK-2", make="Toyota", model="Corolla", year=2022,
                 vehicle_type="boat", fuel_type="petrol", seats=5, rate="50"),
            dict(rego="OK-3", make="Toyota", model="Corolla", year=2022,
                 vehicle_type="car", fuel_type="coal", seats=5, rate="50"),
            dict(rego="OK-4", make="Toyota", model="Corolla", year=2022,
                 vehicle_type="car", fuel_type="petrol", seats=0, rate="50"),
            dict(rego="OK-5", make="Toyota", model="Corolla", year=2022,
                 vehicle_type="car", fuel_type="petrol", seats=5, rate="-5"),
            dict(rego="OK-6", make="Toyota", model="Corolla", year=2022,
                 vehicle_type="car", fuel_type="petrol", seats=5, rate="0"),
        ]
        for kwargs in bad_cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.make_vehicle(**kwargs)
        self.assertEqual(self.repo.vehicles, {})

    def test_stores_all_attributes(self):
        vehicle = self.make_vehicle(vehicle_type="suv", fuel_type="diesel",
                                    seats=7, rate="75.50")
        self.assertEqual(vehicle.vehicle_type, "suv")
        self.assertEqual(vehicle.fuel_type, "diesel")
        self.assertEqual(vehicle.seating_capacity, 7)
        self.assertEqual(vehicle.daily_rate, 75.50)
        self.assertEqual(vehicle.status, VehicleStatus.ACTIVE)

    def test_availability_is_derived_not_stored(self):
        """No manually editable availability flag may exist on a vehicle."""
        vehicle = self.make_vehicle()
        self.assertFalse(hasattr(vehicle, "available"))
        self.assertFalse(hasattr(vehicle, "availability"))


class VehicleUpdateTests(RepoTestCase):
    def test_update_fields(self):
        vehicle = self.make_vehicle()
        updated = vehicle_service.update_vehicle(
            self.repo, vehicle.vehicle_id, daily_rate="60.00", model="Camry"
        )
        self.assertEqual(updated.daily_rate, 60.00)
        self.assertEqual(updated.model, "Camry")

    def test_update_registration_keeps_uniqueness(self):
        first = self.make_vehicle(rego="AAA-1")
        self.make_vehicle(rego="BBB-1", make="Honda", model="Civic")
        with self.assertRaises(ValueError):
            vehicle_service.update_vehicle(self.repo, first.vehicle_id,
                                           registration_number="BBB-1")

    def test_status_change(self):
        vehicle = self.make_vehicle()
        updated = vehicle_service.set_vehicle_status(
            self.repo, vehicle.vehicle_id, VehicleStatus.MAINTENANCE
        )
        self.assertEqual(updated.status, "Maintenance")

        with self.assertRaises(ValueError):  # no-op change reports clearly
            vehicle_service.set_vehicle_status(self.repo, vehicle.vehicle_id,
                                               "Maintenance")
        with self.assertRaises(ValueError):
            vehicle_service.set_vehicle_status(self.repo, vehicle.vehicle_id,
                                               "Bogus")

    def test_search_vehicles(self):
        self.make_vehicle(rego="FIND-1", make="Toyota", model="Corolla")
        self.make_vehicle(rego="FIND-2", make="Yamaha", model="MT-07",
                          vehicle_type="bike", seats=2, rate="35")
        found = vehicle_service.search_vehicles(self.repo, "yamaha")
        self.assertEqual([v.vehicle_id for v in found], ["VEH-0002"])
        self.assertEqual(vehicle_service.search_vehicles(self.repo, "VEH-0001")[0].make,
                         "Toyota")


class VehicleAvailabilityTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.customer = self.make_customer()
        self.free = self.make_vehicle(rego="FREE-1")
        self.booked = self.make_vehicle(rego="BOOK-1", make="Honda", model="CR-V")
        self.maintenance = self.make_vehicle(rego="FIX-1", make="Ford",
                                             model="Transit",
                                             status=VehicleStatus.MAINTENANCE)
        self.base = date.today() + timedelta(days=5)
        # booked vehicle is taken [base, base+3)
        booking_service.create_booking(
            self.repo, self.customer.customer_id, self.booked.vehicle_id,
            self.base, self.base + timedelta(days=3)
        )

    def test_available_vehicles_excludes_booked_and_maintenance(self):
        start, end = self.base + timedelta(days=1), self.base + timedelta(days=2)
        ids = [v.vehicle_id for v in
               vehicle_service.available_vehicles(self.repo, start, end)]
        self.assertEqual(ids, [self.free.vehicle_id])

    def test_overlap_is_half_open(self):
        # Ends exactly when the booking starts -> still available.
        start, end = self.base - timedelta(days=2), self.base
        ids = [v.vehicle_id for v in
               vehicle_service.available_vehicles(self.repo, start, end)]
        self.assertIn(self.booked.vehicle_id, ids)

        # Starts exactly when the booking ends -> also available.
        start, end = self.base + timedelta(days=3), self.base + timedelta(days=5)
        ids = [v.vehicle_id for v in
               vehicle_service.available_vehicles(self.repo, start, end)]
        self.assertIn(self.booked.vehicle_id, ids)

        # One minute... one day inside the range -> blocked.
        start, end = self.base + timedelta(days=2), self.base + timedelta(days=4)
        ids = [v.vehicle_id for v in
               vehicle_service.available_vehicles(self.repo, start, end)]
        self.assertNotIn(self.booked.vehicle_id, ids)

    def test_maintenance_vehicle_never_available(self):
        start, end = self.base - timedelta(days=10), self.base - timedelta(days=9)
        ids = [v.vehicle_id for v in
               vehicle_service.available_vehicles(self.repo, start, end)]
        self.assertNotIn(self.maintenance.vehicle_id, ids)
        self.assertFalse(
            vehicle_service.is_vehicle_available(
                self.repo, self.maintenance.vehicle_id, start, end
            )
        )

    def test_invalid_range_rejected(self):
        start, end = self.base, self.base + timedelta(days=2)
        with self.assertRaises(ValueError):
            vehicle_service.available_vehicles(self.repo, end, start)
        with self.assertRaises(ValueError):
            vehicle_service.available_vehicles(self.repo, start, start)


if __name__ == "__main__":
    __import__("unittest").main()
