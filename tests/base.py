"""Shared test scaffolding.

Every test case runs against a ``Repository`` backed by a fresh temporary
directory, so the real ``data/`` folder is never read or written by tests.
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
import tempfile
import unittest

# Make the project root importable when tests are run directly
# (e.g. ``python tests/test_customers.py``).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from models import VehicleStatus  # noqa: E402
from storage import Repository  # noqa: E402
from services import booking_service, customer_service, vehicle_service  # noqa: E402


class RepoTestCase(unittest.TestCase):
    """Base class providing an isolated repository and record factories."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name
        self.repo = Repository(data_dir=self.data_dir).load()

    def tearDown(self):
        self._tmp.cleanup()

    # ------------------------------------------------------------- utilities
    def reload(self) -> Repository:
        """Simulate an application restart by re-reading the pickle files."""
        return Repository(data_dir=self.data_dir).load()

    def make_customer(self, name="Test Person", phone="+1-555-0100",
                      email="test@example.com", address="1 Test Street",
                      license_number="DL-TEST1"):
        return customer_service.register_customer(
            self.repo, name, phone, email, address, license_number
        )

    def make_vehicle(self, rego="TEST-001", make="Toyota", model="Corolla",
                     year=2022, vehicle_type="car", fuel_type="petrol",
                     seats=5, rate="50.00", status=VehicleStatus.ACTIVE):
        return vehicle_service.add_vehicle(
            self.repo, rego, make, model, year, vehicle_type,
            fuel_type, seats, Decimal(rate), status
        )

    def future_range(self, offset=1, days=3):
        """A ``[start, end)`` range in the future: (start, start + days)."""
        start = date.today() + timedelta(days=offset)
        return start, start + timedelta(days=days)

    def make_booking(self, customer=None, vehicle=None, offset=1, days=3,
                     deposit="100.00"):
        customer = customer or self.make_customer()
        vehicle = vehicle or self.make_vehicle()
        start, end = self.future_range(offset, days)
        return booking_service.create_booking(
            self.repo, customer.customer_id, vehicle.vehicle_id,
            start, end, Decimal(deposit)
        )
