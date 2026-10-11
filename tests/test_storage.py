"""Tests for pickle persistence, atomic saves and crash/corruption recovery."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import RepoTestCase

from storage import FILES, Repository, StorageError
from services import customer_service


class PersistenceTests(RepoTestCase):
    def test_records_survive_restart(self):
        customer = self.make_customer()
        vehicle = self.make_vehicle()

        restarted = self.reload()
        self.assertIn(customer.customer_id, restarted.customers)
        self.assertIn(vehicle.vehicle_id, restarted.vehicles)
        self.assertEqual(restarted.customers[customer.customer_id].name,
                         customer.name)
        self.assertEqual(restarted.vehicles[vehicle.vehicle_id].daily_rate,
                         vehicle.daily_rate)

    def test_all_five_files_are_written(self):
        from decimal import Decimal
        from services import billing_service
        customer = self.make_customer()
        vehicle = self.make_vehicle()
        booking = self.make_booking(customer=customer, vehicle=vehicle)
        bill = billing_service.generate_bill(self.repo, booking.booking_id)
        billing_service.process_payment(self.repo, bill.bill_id, Decimal("10.00"))
        for filename in FILES.values():
            path = os.path.join(self.data_dir, filename)
            self.assertTrue(os.path.exists(path), f"missing {filename}")
            self.assertGreater(os.path.getsize(path), 0)

    def test_missing_files_load_as_empty_and_are_created_later(self):
        empty_dir = os.path.join(self.data_dir, "fresh")
        repo = Repository(data_dir=empty_dir).load()
        self.assertEqual(repo.customers, {})
        self.assertEqual(repo.warnings, [])
        self.assertTrue(os.path.isdir(empty_dir))  # directory auto-created

        customer_service.register_customer(
            repo, "Fresh Person", "1234567", "fresh@example.com",
            "5 New Street", "DL-FRESH"
        )
        self.assertTrue(os.path.exists(os.path.join(empty_dir, "customers.pkl")))

    def test_saving_is_atomic_and_keeps_a_backup(self):
        self.make_customer()
        path = os.path.join(self.data_dir, "customers.pkl")
        self.assertFalse(os.path.exists(path + ".bak"))  # first write: nothing to back up
        self.assertFalse(any(name.startswith(".tmp-")
                             for name in os.listdir(self.data_dir)))

        self.make_customer(name="Second Person", phone="7654321",
                           email="two@example.com", address="6 Other Street",
                           license_number="DL-002")
        self.assertTrue(os.path.exists(path + ".bak"))
        # No temp files left behind.
        self.assertFalse(any(name.startswith(".tmp-")
                             for name in os.listdir(self.data_dir)))

        # Backup holds the previous state (one customer), current file has two.
        self.assertEqual(len(Repository(data_dir=self.data_dir).load().customers), 2)

    def test_unknown_entity_raises(self):
        with self.assertRaises(StorageError):
            self.repo.save("dragons")

    def test_corrupt_file_is_quarantined_not_overwritten(self):
        self.make_customer()
        path = os.path.join(self.data_dir, "customers.pkl")
        with open(path, "wb") as handle:
            handle.write(b"\x80\x05this is not a valid pickle payload")

        recovered = Repository(data_dir=self.data_dir).load()
        self.assertEqual(recovered.customers, {})          # graceful start
        self.assertEqual(len(recovered.warnings), 1)       # user is warned
        self.assertFalse(os.path.exists(path))             # moved aside...
        quarantined = [n for n in os.listdir(self.data_dir)
                       if n.endswith(".corrupt")]
        self.assertEqual(len(quarantined), 1)              # ...not destroyed
        with open(os.path.join(self.data_dir, quarantined[0]), "rb") as handle:
            self.assertEqual(handle.read(), b"\x80\x05this is not a valid pickle payload")

        # The application keeps working: a new save recreates the file.
        customer_service.register_customer(
            recovered, "Recovered Person", "1112223", "rec@example.com",
            "7 Recovery Road", "DL-REC"
        )
        self.assertEqual(len(Repository(data_dir=self.data_dir).load().customers), 1)

    def test_non_dict_payload_is_treated_as_corrupt(self):
        import pickle
        path = os.path.join(self.data_dir, "vehicles.pkl")
        with open(path, "wb") as handle:
            pickle.dump(["not", "a", "dict"], handle)
        repo = Repository(data_dir=self.data_dir).load()
        self.assertEqual(repo.vehicles, {})
        self.assertTrue(repo.warnings)

    def test_load_does_not_touch_unrelated_files(self):
        keep = os.path.join(self.data_dir, "notes.txt")
        with open(keep, "w", encoding="utf-8") as handle:
            handle.write("keep me")
        Repository(data_dir=self.data_dir).load()
        self.assertTrue(os.path.exists(keep))


class IdGenerationTests(RepoTestCase):
    def test_next_id_skips_non_matching_keys(self):
        existing = {"CUS-0001": 1, "CUS-0009": 1, "noise": 1, "CUS-abc": 1}
        self.assertEqual(Repository.next_id(existing, "CUS"), "CUS-0010")

    def test_next_id_on_empty_registry(self):
        self.assertEqual(Repository.next_id({}, "BKG"), "BKG-0001")

    def test_ids_do_not_collide_after_restart(self):
        self.make_customer()
        restarted = self.reload()
        second = customer_service.register_customer(
            restarted, "Next Person", "2223334", "next@example.com",
            "8 Next Lane", "DL-NEXT"
        )
        self.assertEqual(second.customer_id, "CUS-0002")


class SampleDataTests(RepoTestCase):
    def test_generator_creates_consistent_records(self):
        result = self.repo.generate_sample_data()
        self.assertTrue(result["created"])
        self.assertEqual(len(self.repo.customers), result["customers"])
        self.assertEqual(len(self.repo.vehicles), result["vehicles"])
        self.assertEqual(len(self.repo.bookings), result["bookings"])

        # IDs unique, relationships valid, files persisted.
        self.assertEqual(len(set(self.repo.customers)), len(self.repo.customers))
        for booking in self.repo.bookings.values():
            self.assertIn(booking.customer_id, self.repo.customers)
            self.assertIn(booking.vehicle_id, self.repo.vehicles)
        for bill in self.repo.bills.values():
            self.assertIn(bill.booking_id, self.repo.bookings)

        restarted = self.reload()
        self.assertEqual(len(restarted.customers), result["customers"])

    def test_generator_never_overwrites_existing_records(self):
        self.make_customer()
        before = dict(self.repo.customers)
        result = self.repo.generate_sample_data()
        self.assertFalse(result["created"])
        self.assertEqual(self.repo.customers, before)


if __name__ == "__main__":
    __import__("unittest").main()
