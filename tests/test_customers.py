"""Tests for customer creation, validation, search and updates."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import RepoTestCase

from services import booking_service, customer_service


class CustomerCreationTests(RepoTestCase):
    def test_registration_assigns_sequential_unique_ids(self):
        first = self.make_customer(license_number="DL-001")
        second = self.make_customer(name="Second Person", phone="+1-555-0111",
                                    email="second@example.com",
                                    address="2 Other Street",
                                    license_number="DL-002")
        self.assertEqual(first.customer_id, "CUS-0001")
        self.assertEqual(second.customer_id, "CUS-0002")
        self.assertEqual(len(self.repo.customers), 2)

    def test_all_fields_are_stored(self):
        customer = self.make_customer()
        self.assertEqual(customer.name, "Test Person")
        self.assertEqual(customer.phone, "+1-555-0100")
        self.assertEqual(customer.email, "test@example.com")
        self.assertEqual(customer.address, "1 Test Street")
        self.assertEqual(customer.license_number, "DL-TEST1")

    def test_invalid_inputs_are_rejected(self):
        cases = [
            dict(name="", phone="1", email="bad", address="x",
                 license_number="DL-1"),                      # bad name/phone/email
            dict(name="Valid Name", phone="abc", email="a@b.co",
                 address="1 Street", license_number="DL-1"),  # bad phone
            dict(name="Valid Name", phone="1234567", email="not-an-email",
                 address="1 Street", license_number="DL-1"),  # bad email
            dict(name="Valid Name", phone="1234567", email="a@b.co",
                 address="", license_number="DL-1"),          # bad address
            dict(name="Valid Name", phone="1234567", email="a@b.co",
                 address="1 Street", license_number="!!"),    # bad licence
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.make_customer(**kwargs)
        self.assertEqual(self.repo.customers, {})

    def test_duplicate_driving_licence_rejected(self):
        self.make_customer(license_number="DL-DUP")
        with self.assertRaises(ValueError) as ctx:
            self.make_customer(name="Clone Person", phone="+1-555-0102",
                               email="clone@example.com", address="9 Clone Road",
                               license_number="DL-DUP")
        self.assertIn("already registered", str(ctx.exception))
        self.assertEqual(len(self.repo.customers), 1)


class CustomerSearchUpdateTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.make_customer(name="Alice Morgan", phone="+1-555-0101",
                                        email="alice@example.com",
                                        address="12 River Street",
                                        license_number="DL-ALICE")
        self.bob = self.make_customer(name="Bob Smith", phone="+1-555-0102",
                                      email="bob@example.com",
                                      address="34 Hill Avenue",
                                      license_number="DL-BOB")

    def test_search_by_id_name_phone_and_licence(self):
        by_id = customer_service.search_customers(self.repo, "CUS-0002")
        self.assertEqual([c.customer_id for c in by_id], ["CUS-0002"])

        by_name = customer_service.search_customers(self.repo, "alice")
        self.assertEqual([c.customer_id for c in by_name], ["CUS-0001"])

        by_phone = customer_service.search_customers(self.repo, "555-0102")
        self.assertEqual([c.customer_id for c in by_phone], ["CUS-0002"])

        by_licence = customer_service.search_customers(self.repo, "dl-alice")
        self.assertEqual([c.customer_id for c in by_licence], ["CUS-0001"])

        self.assertEqual(customer_service.search_customers(self.repo, "nobody"), [])

    def test_search_requires_a_query(self):
        with self.assertRaises(ValueError):
            customer_service.search_customers(self.repo, "   ")

    def test_update_changes_only_given_fields(self):
        updated = customer_service.update_customer(
            self.repo, self.alice.customer_id, phone="+1-555-9999"
        )
        self.assertEqual(updated.phone, "+1-555-9999")
        self.assertEqual(updated.name, "Alice Morgan")  # untouched

        reloaded = self.reload().customers[self.alice.customer_id]
        self.assertEqual(reloaded.phone, "+1-555-9999")

    def test_update_to_duplicate_licence_rejected(self):
        with self.assertRaises(ValueError):
            customer_service.update_customer(
                self.repo, self.alice.customer_id,
                license_number=self.bob.license_number,
            )

    def test_update_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            customer_service.update_customer(self.repo, self.alice.customer_id,
                                             nickname="Ali")

    def test_invalid_updated_values_rejected(self):
        with self.assertRaises(ValueError):
            customer_service.update_customer(self.repo, self.alice.customer_id,
                                             email="broken-email")

    def test_unknown_customer_id_raises(self):
        with self.assertRaises(ValueError):
            customer_service.get_customer(self.repo, "CUS-9999")
        with self.assertRaises(ValueError):
            customer_service.update_customer(self.repo, "CUS-9999", name="X Y")


class CustomerPersistenceTests(RepoTestCase):
    def test_customers_survive_restart_and_stay_linked_to_bookings(self):
        customer = self.make_customer()
        booking = self.make_booking(customer=customer)

        restarted = self.reload()
        self.assertIn(customer.customer_id, restarted.customers)
        stored_booking = restarted.bookings[booking.booking_id]
        self.assertEqual(stored_booking.customer_id, customer.customer_id)

        history = booking_service.booking_history(restarted, customer.customer_id)
        self.assertEqual([b.booking_id for b in history], [booking.booking_id])

    def test_no_delete_operation_exists_for_customers(self):
        self.assertFalse(hasattr(customer_service, "delete_customer"))


if __name__ == "__main__":
    unittest_main = __import__("unittest").main
    unittest_main()
