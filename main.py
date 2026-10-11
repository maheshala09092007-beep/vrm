#!/usr/bin/env python3
"""Vehicle Rental Management System - command-line interface.

Run with::

    python main.py             # normal start, loads existing data
    python main.py --sample    # also load sample data if data dir is empty
    python main.py --data-dir /some/path   # use a different data directory

Phase 1 scope: Python + pickle + CLI only (no GUI / web frontend).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from typing import Iterable, List, Optional

from models import (
    BookingStatus,
    FUEL_TYPES,
    PaymentKind,
    VEHICLE_TYPES,
    VehicleStatus,
)
from storage import DEFAULT_DATA_DIR, Repository, StorageError
from validators import parse_date, parse_decimal, validate_positive_int
from services import (
    billing_service,
    booking_service,
    customer_service,
    report_service,
    vehicle_service,
)

PAYMENT_METHODS = ("cash", "card", "transfer", "mobile")


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _read(prompt_text: str) -> str:
    """Single line of input; EOF/Interrupt exits the program cleanly."""
    try:
        return input(prompt_text)
    except (EOFError, KeyboardInterrupt):
        print("\n\nGoodbye.")
        raise SystemExit(0)


def prompt(label: str, default: Optional[str] = None, required: bool = True) -> str:
    """Ask for a value in a loop until it is acceptable."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        raw = _read(f"{label}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        if not required:
            return ""
        print("  Input is required.")


def prompt_date(label: str, default: Optional[date] = None) -> date:
    default_text = default.isoformat() if default else None
    while True:
        raw = prompt(label, default=default_text)
        try:
            return parse_date(raw, label)
        except ValueError as exc:
            print(f"  {exc}")


def prompt_decimal(label: str, default: Optional[str] = None, allow_zero: bool = True) -> Decimal:
    while True:
        raw = prompt(label, default=default)
        try:
            return parse_decimal(raw, label, allow_zero=allow_zero)
        except ValueError as exc:
            print(f"  {exc}")


def prompt_int(label: str, default: Optional[str] = None,
               minimum: int = 1, maximum: int = 100000) -> int:
    while True:
        raw = prompt(label, default=default)
        try:
            return validate_positive_int(raw, label, minimum, maximum)
        except ValueError as exc:
            print(f"  {exc}")


def prompt_choice(label: str, choices: Iterable[str]) -> str:
    choices = list(choices)
    shown = "/".join(choices)
    while True:
        raw = _read(f"{label} ({shown}): ").strip().lower()
        for choice in choices:
            if raw == choice.lower():
                return choice
        print(f"  Choose one of: {shown}.")


def pause() -> None:
    _read("\nPress Enter to continue...")


def money(value) -> str:
    return f"{value:,.2f}"


def print_table(headers: List[str], rows: List[List]) -> None:
    if not rows:
        print("  (no records)")
        return
    text = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(headers[i]), max(len(row[i]) for row in text))
        for i in range(len(headers))
    ]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print("  " + line)
    print("  " + "  ".join("-" * w for w in widths))
    for row in text:
        print("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def run_action(action) -> None:
    """Run a menu operation, printing friendly errors instead of crashing."""
    try:
        action()
    except ValueError as exc:
        print(f"\nError: {exc}")
    except StorageError as exc:
        print(f"\nStorage error: {exc}")
    pause()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def show_customer(repo: Repository, customer) -> None:
    bookings = [b for b in repo.bookings.values() if b.customer_id == customer.customer_id]
    print(f"\nCustomer {customer.customer_id}")
    print(f"  Name            : {customer.name}")
    print(f"  Phone           : {customer.phone}")
    print(f"  Email           : {customer.email}")
    print(f"  Address         : {customer.address}")
    print(f"  Driving licence : {customer.license_number}")
    print(f"  Registered on   : {customer.created_at}")
    print(f"  Bookings        : {len(bookings)}")


def customer_rows(customers) -> List[List]:
    return [
        [c.customer_id, c.name, c.phone, c.email, c.license_number]
        for c in customers
    ]


def show_vehicle(repo: Repository, vehicle) -> None:
    conflicts = 0
    for booking in repo.bookings.values():
        if booking.vehicle_id == vehicle.vehicle_id and booking.status in (
            BookingStatus.BOOKED, BookingStatus.PICKED_UP, BookingStatus.RETURNED
        ):
            conflicts += 1
    print(f"\nVehicle {vehicle.vehicle_id}")
    print(f"  Registration : {vehicle.registration_number}")
    print(f"  Make / model : {vehicle.make} {vehicle.model}")
    print(f"  Year         : {vehicle.year}")
    print(f"  Type / fuel  : {vehicle.vehicle_type} / {vehicle.fuel_type}")
    print(f"  Seats        : {vehicle.seating_capacity}")
    print(f"  Daily rate   : {money(vehicle.daily_rate)}")
    print(f"  Status       : {vehicle.status}")
    print(f"  Bookings     : {conflicts}")


def vehicle_rows(vehicles) -> List[List]:
    return [
        [v.vehicle_id, v.registration_number, f"{v.make} {v.model}", v.year,
         v.vehicle_type, v.fuel_type, v.seating_capacity, money(v.daily_rate), v.status]
        for v in vehicles
    ]


def booking_rows(repo: Repository, bookings) -> List[List]:
    rows = []
    for b in bookings:
        customer = repo.customers.get(b.customer_id)
        vehicle = repo.vehicles.get(b.vehicle_id)
        rows.append([
            b.booking_id,
            customer.name if customer else b.customer_id,
            vehicle.registration_number if vehicle else b.vehicle_id,
            b.pickup_date.isoformat(),
            b.expected_return_date.isoformat(),
            (b.actual_return_date.isoformat() if b.actual_return_date else "-"),
            b.status,
            money(b.estimated_cost),
        ])
    return rows


def show_booking(repo: Repository, booking) -> None:
    customer = repo.customers.get(booking.customer_id)
    vehicle = repo.vehicles.get(booking.vehicle_id)
    bill = billing_service.find_bill_for_booking(repo, booking.booking_id)
    print(f"\nBooking {booking.booking_id}")
    print(f"  Customer   : {customer.name if customer else booking.customer_id} "
          f"({booking.customer_id})")
    print(f"  Vehicle    : {vehicle.label if vehicle else booking.vehicle_id} "
          f"({booking.vehicle_id})")
    print(f"  Booked on  : {booking.booking_date.isoformat()}")
    print(f"  Pickup     : {booking.pickup_date.isoformat()} (inclusive)")
    print(f"  Return     : {booking.expected_return_date.isoformat()} (exclusive)")
    print(f"  Actual rtn : {booking.actual_return_date.isoformat() if booking.actual_return_date else '-'}")
    print(f"  Rate/day   : {money(booking.daily_rate)}   Deposit: {money(booking.security_deposit)}")
    print(f"  Status     : {booking.status}")
    print(f"  Rental days: {booking.rental_days}   Estimated/actual cost: {money(booking.estimated_cost)}")
    if booking.late_days:
        print(f"  Late days  : {booking.late_days}   Late charge: {money(booking.late_charge)}")
    print(f"  Bill       : {bill.bill_id if bill else 'not generated'}")


def show_bill(repo: Repository, bill_id: str) -> None:
    summary = billing_service.bill_summary(repo, bill_id)
    bill = summary["bill"]
    booking = repo.bookings.get(bill.booking_id)
    print(f"\nBill {bill.bill_id}  (booking {bill.booking_id}, issued {bill.issue_date.isoformat()})")
    if booking:
        customer = repo.customers.get(booking.customer_id)
        vehicle = repo.vehicles.get(booking.vehicle_id)
        print(f"  Customer   : {customer.name if customer else booking.customer_id}")
        print(f"  Vehicle    : {vehicle.label if vehicle else booking.vehicle_id}")
        print(f"  Period     : {booking.pickup_date.isoformat()} -> "
              f"{(booking.actual_return_date or booking.expected_return_date).isoformat()} "
              f"({booking.rental_days} day(s))")
    print("  --- Charges ---")
    print(f"  Rental charge        : {money(bill.rental_charge)}")
    print(f"  Late charge          : {money(bill.late_charge)}")
    print(f"  Additional charges   : {money(bill.additional_charges)}")
    print(f"  Discount             : -{money(bill.discount)}")
    print(f"  TOTAL                : {money(bill.total)}")
    print("  --- Payments ---")
    print(f"  Paid                 : {money(summary['paid'])}")
    print(f"  Outstanding          : {money(summary['outstanding'])}")
    print(f"  Status               : {summary['status']}")
    print("  --- Security deposit ---")
    print(f"  Held                 : {money(summary['deposit_held'])}")
    print(f"  Deducted             : {money(summary['deposit_deducted'])}")
    print(f"  Refunded             : {money(summary['deposit_refunded'])}")
    if summary["payments"]:
        print("  --- Payment history ---")
        print_table(
            ["ID", "Date", "Amount", "Kind", "Method", "Note"],
            [[p.payment_id, p.payment_date.isoformat(), money(p.amount),
              ("refund (deposit)" if p.is_deposit else p.kind), p.method, p.note]
             for p in summary["payments"]],
        )


def resolve_bill_id(repo: Repository, raw: str) -> str:
    """Accept a bill ID, or fall back to a booking ID for convenience."""
    key = (raw or "").strip().upper()
    if key in repo.bills:
        return key
    bill = billing_service.find_bill_for_booking(repo, key)
    if bill is not None:
        return bill.bill_id
    raise ValueError(f"No bill found with ID '{raw}' (tried bill and booking IDs).")


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

def customer_menu(repo: Repository) -> None:
    while True:
        print(
            "\n=== Customer Management ===\n"
            "  1. Register new customer\n"
            "  2. View all customers\n"
            "  3. Search customers\n"
            "  4. View customer details\n"
            "  5. Update customer details\n"
            "  0. Back to main menu"
        )
        choice = _read("Choose an option: ").strip()
        if choice == "1":
            def action():
                name = prompt("Name")
                phone = prompt("Phone number")
                email = prompt("Email")
                address = prompt("Address")
                licence = prompt("Driving licence number")
                customer = customer_service.register_customer(
                    repo, name, phone, email, address, licence
                )
                print(f"\nCustomer registered: {customer.customer_id} - {customer.name}")
            run_action(action)
        elif choice == "2":
            def action():
                customers = customer_service.list_customers(repo)
                print(f"\n{len(customers)} customer(s):")
                print_table(["ID", "Name", "Phone", "Email", "Licence"],
                            customer_rows(customers))
            run_action(action)
        elif choice == "3":
            def action():
                query = prompt("Search (ID / name / phone / licence)")
                found = customer_service.search_customers(repo, query)
                print(f"\n{len(found)} match(es):")
                print_table(["ID", "Name", "Phone", "Email", "Licence"],
                            customer_rows(found))
            run_action(action)
        elif choice == "4":
            def action():
                customer = customer_service.get_customer(
                    repo, prompt("Customer ID")
                )
                show_customer(repo, customer)
            run_action(action)
        elif choice == "5":
            def action():
                customer = customer_service.get_customer(
                    repo, prompt("Customer ID")
                )
                show_customer(repo, customer)
                fields = {}
                labels = {
                    "name": "New name", "phone": "New phone",
                    "email": "New email", "address": "New address",
                    "license_number": "New licence number",
                }
                for field_name, label in labels.items():
                    value = prompt(f"{label} (Enter to keep '{getattr(customer, field_name)}')",
                                   required=False)
                    if value:
                        fields[field_name] = value
                if not fields:
                    print("\nNothing to update.")
                    return
                updated = customer_service.update_customer(repo, customer.customer_id, **fields)
                print(f"\nUpdated {updated.customer_id}.")
            run_action(action)
        elif choice == "0":
            return
        else:
            print("Invalid option - choose 0-5.")


def vehicle_menu(repo: Repository) -> None:
    while True:
        print(
            "\n=== Vehicle Management ===\n"
            "  1. Add a vehicle\n"
            "  2. List all vehicles\n"
            "  3. Search vehicles\n"
            "  4. Update vehicle information\n"
            "  5. Change operational status\n"
            "  6. Available vehicles for a date range\n"
            "  0. Back to main menu"
        )
        choice = _read("Choose an option: ").strip()
        if choice == "1":
            def action():
                rego = prompt("Registration number")
                make = prompt("Make")
                model = prompt("Model")
                year = prompt_int("Manufacturing year", minimum=1900,
                                  maximum=date.today().year + 1)
                vtype = prompt_choice("Vehicle type", VEHICLE_TYPES)
                fuel = prompt_choice("Fuel type", FUEL_TYPES)
                seats = prompt_int("Seating capacity", minimum=1, maximum=100)
                rate = prompt_decimal("Daily rental rate", allow_zero=False)
                vehicle = vehicle_service.add_vehicle(
                    repo, rego, make, model, year, vtype, fuel, seats, rate
                )
                print(f"\nVehicle added: {vehicle.vehicle_id} - {vehicle.label}")
            run_action(action)
        elif choice == "2":
            def action():
                vehicles = vehicle_service.list_vehicles(repo)
                print(f"\n{len(vehicles)} vehicle(s):")
                print_table(["ID", "Rego", "Make/Model", "Year", "Type", "Fuel",
                             "Seats", "Rate/day", "Status"], vehicle_rows(vehicles))
            run_action(action)
        elif choice == "3":
            def action():
                query = prompt("Search (ID / rego / make / model / type)")
                found = vehicle_service.search_vehicles(repo, query)
                print(f"\n{len(found)} match(es):")
                print_table(["ID", "Rego", "Make/Model", "Year", "Type", "Fuel",
                             "Seats", "Rate/day", "Status"], vehicle_rows(found))
            run_action(action)
        elif choice == "4":
            def action():
                vehicle = vehicle_service.get_vehicle(repo, prompt("Vehicle ID"))
                show_vehicle(repo, vehicle)
                fields = {}
                simple = {
                    "registration_number": "New registration",
                    "make": "New make", "model": "New model",
                }
                for field_name, label in simple.items():
                    value = prompt(f"{label} (Enter to keep '{getattr(vehicle, field_name)}')",
                                   required=False)
                    if value:
                        fields[field_name] = value
                value = prompt(f"New year (Enter to keep '{vehicle.year}')", required=False)
                if value:
                    fields["year"] = value
                value = prompt(f"New type {'/'.join(VEHICLE_TYPES)} (Enter to keep "
                               f"'{vehicle.vehicle_type}')", required=False)
                if value:
                    fields["vehicle_type"] = value
                value = prompt(f"New fuel {'/'.join(FUEL_TYPES)} (Enter to keep "
                               f"'{vehicle.fuel_type}')", required=False)
                if value:
                    fields["fuel_type"] = value
                value = prompt(f"New seats (Enter to keep '{vehicle.seating_capacity}')",
                               required=False)
                if value:
                    fields["seating_capacity"] = value
                value = prompt(f"New daily rate (Enter to keep '{money(vehicle.daily_rate)}')",
                               required=False)
                if value:
                    fields["daily_rate"] = value
                if not fields:
                    print("\nNothing to update.")
                    return
                updated = vehicle_service.update_vehicle(repo, vehicle.vehicle_id, **fields)
                print(f"\nUpdated {updated.vehicle_id}.")
            run_action(action)
        elif choice == "5":
            def action():
                vehicle = vehicle_service.get_vehicle(repo, prompt("Vehicle ID"))
                print(f"{vehicle.vehicle_id} is currently '{vehicle.status}'.")
                status = prompt_choice("New status", VehicleStatus.ALL)
                updated = vehicle_service.set_vehicle_status(repo, vehicle.vehicle_id, status)
                print(f"\n{updated.vehicle_id} status is now '{updated.status}'.")
            run_action(action)
        elif choice == "6":
            def action():
                start = prompt_date("Start date (YYYY-MM-DD)")
                end = prompt_date("End date (YYYY-MM-DD)", default=start)
                found = report_service.available_vehicles_report(repo, start, end)
                print(f"\nAvailable vehicles for {start.isoformat()} -> {end.isoformat()} "
                      f"(start inclusive, end exclusive): {len(found)}")
                print_table(["ID", "Rego", "Make/Model", "Year", "Type", "Fuel",
                             "Seats", "Rate/day", "Status"], vehicle_rows(found))
            run_action(action)
        elif choice == "0":
            return
        else:
            print("Invalid option - choose 0-6.")


def booking_menu(repo: Repository) -> None:
    while True:
        print(
            "\n=== Booking and Rental Management ===\n"
            "  1. Create a booking\n"
            "  2. List all bookings\n"
            "  3. Search bookings\n"
            "  4. View booking details\n"
            "  5. Cancel a booking\n"
            "  6. Record vehicle pickup\n"
            "  7. Record vehicle return\n"
            "  0. Back to main menu"
        )
        choice = _read("Choose an option: ").strip()
        if choice == "1":
            def action():
                customer_id = prompt("Customer ID")
                vehicle_id = prompt("Vehicle ID")
                customer = customer_service.get_customer(repo, customer_id)
                vehicle = vehicle_service.get_vehicle(repo, vehicle_id)
                print(f"Booking for {customer.name} / {vehicle.label} "
                      f"at {money(vehicle.daily_rate)}/day.")
                pickup = prompt_date("Pickup date (YYYY-MM-DD)")
                ret = prompt_date("Expected return date (YYYY-MM-DD)")
                default_deposit = (vehicle.daily_rate * 2).quantize(Decimal("0.01"))
                deposit = prompt_decimal(
                    f"Security deposit (default {money(default_deposit)})",
                    default=money(default_deposit),
                )
                booking = booking_service.create_booking(
                    repo, customer.customer_id, vehicle.vehicle_id,
                    pickup, ret, deposit,
                )
                print(f"\nBooking created: {booking.booking_id} | "
                      f"{booking.pickup_date.isoformat()} -> {booking.expected_return_date.isoformat()} | "
                      f"estimated cost {money(booking.estimated_cost)}")
            run_action(action)
        elif choice == "2":
            def action():
                bookings = booking_service.list_bookings(repo)
                print(f"\n{len(bookings)} booking(s):")
                print_table(["ID", "Customer", "Vehicle", "Pickup", "Return",
                             "Actual", "Status", "Est. cost"],
                            booking_rows(repo, bookings))
            run_action(action)
        elif choice == "3":
            def action():
                query = prompt("Search (booking / customer / vehicle ID / status)")
                found = booking_service.search_bookings(repo, query)
                print(f"\n{len(found)} match(es):")
                print_table(["ID", "Customer", "Vehicle", "Pickup", "Return",
                             "Actual", "Status", "Est. cost"],
                            booking_rows(repo, found))
            run_action(action)
        elif choice == "4":
            def action():
                booking = booking_service.get_booking(repo, prompt("Booking ID"))
                show_booking(repo, booking)
            run_action(action)
        elif choice == "5":
            def action():
                booking = booking_service.cancel_booking(repo, prompt("Booking ID"))
                print(f"\nBooking {booking.booking_id} cancelled. "
                      "History is preserved and the vehicle is free again.")
            run_action(action)
        elif choice == "6":
            def action():
                booking = booking_service.record_pickup(repo, prompt("Booking ID"))
                show_booking(repo, booking)
                print("Pickup recorded.")
            run_action(action)
        elif choice == "7":
            def action():
                booking_id = prompt("Booking ID")
                booking = booking_service.get_booking(repo, booking_id)
                default_date = booking.actual_return_date or date.today()
                ret = prompt_date(f"Actual return date (default {default_date.isoformat()})",
                                  default=default_date)
                booking = booking_service.record_return(repo, booking.booking_id, ret)
                show_booking(repo, booking)
                bill = billing_service.find_bill_for_booking(repo, booking.booking_id)
                if bill:
                    print(f"Bill {bill.bill_id} recalculated "
                          f"(total {money(bill.total)}).")
            run_action(action)
        elif choice == "0":
            return
        else:
            print("Invalid option - choose 0-7.")


def billing_menu(repo: Repository) -> None:
    while True:
        print(
            "\n=== Billing and Payments ===\n"
            "  1. Generate a bill for a booking\n"
            "  2. View a bill (and its payment history)\n"
            "  3. List all bills\n"
            "  4. Add an additional charge\n"
            "  5. Apply a discount\n"
            "  6. Process a payment\n"
            "  7. Refund a payment\n"
            "  8. Settle the security deposit\n"
            "  9. Payment history for a bill\n"
            "  0. Back to main menu"
        )
        choice = _read("Choose an option: ").strip()
        if choice == "1":
            def action():
                booking_id = prompt("Booking ID")
                bill = billing_service.generate_bill(repo, booking_id)
                print(f"\nBill generated: {bill.bill_id} | total {money(bill.total)}")
                show_bill(repo, bill.bill_id)
            run_action(action)
        elif choice == "2":
            def action():
                bill_id = resolve_bill_id(repo, prompt("Bill ID (or booking ID)"))
                show_bill(repo, bill_id)
            run_action(action)
        elif choice == "3":
            def action():
                bills = billing_service.list_bills(repo)
                rows = []
                for bill in bills:
                    rows.append([
                        bill.bill_id, bill.booking_id, money(bill.total),
                        money(billing_service.paid_total(repo, bill.bill_id)),
                        money(billing_service.outstanding(repo, bill.bill_id)),
                        billing_service.payment_status(repo, bill.bill_id),
                    ])
                print(f"\n{len(bills)} bill(s):")
                print_table(["Bill", "Booking", "Total", "Paid", "Outstanding", "Status"],
                            rows)
            run_action(action)
        elif choice == "4":
            def action():
                bill_id = resolve_bill_id(repo, prompt("Bill ID (or booking ID)"))
                amount = prompt_decimal("Additional charge amount", allow_zero=False)
                note = prompt("Note (optional)", required=False)
                bill = billing_service.add_additional_charge(repo, bill_id, amount, note)
                print(f"\nCharge added. New total: {money(bill.total)}")
            run_action(action)
        elif choice == "5":
            def action():
                bill_id = resolve_bill_id(repo, prompt("Bill ID (or booking ID)"))
                amount = prompt_decimal("Discount amount", allow_zero=False)
                bill = billing_service.apply_discount(repo, bill_id, amount)
                print(f"\nDiscount applied. New total: {money(bill.total)}")
            run_action(action)
        elif choice == "6":
            def action():
                bill_id = resolve_bill_id(repo, prompt("Bill ID (or booking ID)"))
                due = billing_service.outstanding(repo, bill_id)
                print(f"Outstanding balance: {money(due)}")
                amount = prompt_decimal("Payment amount", allow_zero=False)
                method = prompt_choice("Payment method", PAYMENT_METHODS)
                payment = billing_service.process_payment(
                    repo, bill_id, amount, method=method, kind=PaymentKind.PAYMENT
                )
                print(f"\nPayment {payment.payment_id} recorded. "
                      f"Outstanding now: "
                      f"{money(billing_service.outstanding(repo, bill_id))} "
                      f"({billing_service.payment_status(repo, bill_id)})")
            run_action(action)
        elif choice == "7":
            def action():
                bill_id = resolve_bill_id(repo, prompt("Bill ID (or booking ID)"))
                payments = billing_service.payments_for_bill(repo, bill_id)
                print_table(
                    ["ID", "Date", "Amount", "Kind", "Method", "Note"],
                    [[p.payment_id, p.payment_date.isoformat(), money(p.amount),
                      ("refund (deposit)" if p.is_deposit else p.kind), p.method, p.note]
                     for p in payments],
                )
                payment_id = prompt("Payment ID to refund")
                note = prompt("Reason (optional)", required=False)
                refund = billing_service.refund_payment(repo, payment_id, note)
                print(f"\nRefund {refund.payment_id} recorded for {money(refund.amount)}.")
            run_action(action)
        elif choice == "8":
            def action():
                bill_id = resolve_bill_id(repo, prompt("Bill ID (or booking ID)"))
                deducted = prompt_decimal(
                    "Deposit amount to deduct (0 to refund in full)", allow_zero=True
                )
                result = billing_service.settle_deposit(repo, bill_id, deducted)
                print(f"\nDeposit held {money(result['held'])} | "
                      f"deducted {money(result['deducted'])} | "
                      f"refunded {money(result['refunded'])}")
            run_action(action)
        elif choice == "9":
            def action():
                bill_id = resolve_bill_id(repo, prompt("Bill ID (or booking ID)"))
                payments = billing_service.payments_for_bill(repo, bill_id)
                print(f"\n{len(payments)} payment record(s) for {bill_id}:")
                print_table(
                    ["ID", "Date", "Amount", "Kind", "Method", "Note"],
                    [[p.payment_id, p.payment_date.isoformat(), money(p.amount),
                      ("refund (deposit)" if p.is_deposit else p.kind), p.method, p.note]
                     for p in payments],
                )
            run_action(action)
        elif choice == "0":
            return
        else:
            print("Invalid option - choose 0-9.")


def report_menu(repo: Repository) -> None:
    while True:
        print(
            "\n=== Reports ===\n"
            "  1. Available vehicles for a date range\n"
            "  2. Active and upcoming bookings\n"
            "  3. Booking history for a customer\n"
            "  4. Payments collected during a date range\n"
            "  5. Outstanding bills\n"
            "  6. Most frequently rented vehicles\n"
            "  7. Vehicles under maintenance\n"
            "  0. Back to main menu"
        )
        choice = _read("Choose an option: ").strip()
        if choice == "1":
            def action():
                start = prompt_date("Start date (YYYY-MM-DD)")
                end = prompt_date("End date (YYYY-MM-DD)", default=start)
                found = report_service.available_vehicles_report(repo, start, end)
                print(f"\n{len(found)} vehicle(s) available for "
                      f"{start.isoformat()} -> {end.isoformat()}:")
                print_table(["ID", "Rego", "Make/Model", "Rate/day", "Status"],
                            [[v.vehicle_id, v.registration_number,
                              f"{v.make} {v.model}", money(v.daily_rate), v.status]
                             for v in found])
            run_action(action)
        elif choice == "2":
            def action():
                groups = report_service.active_and_upcoming_bookings(repo)
                print(f"\nActive bookings (vehicles out now): {len(groups['active'])}")
                print_table(["ID", "Customer", "Vehicle", "Pickup", "Return",
                             "Actual", "Status", "Est. cost"],
                            booking_rows(repo, groups["active"]))
                print(f"\nUpcoming bookings: {len(groups['upcoming'])}")
                print_table(["ID", "Customer", "Vehicle", "Pickup", "Return",
                             "Actual", "Status", "Est. cost"],
                            booking_rows(repo, groups["upcoming"]))
            run_action(action)
        elif choice == "3":
            def action():
                customer_id = prompt("Customer ID")
                customer = customer_service.get_customer(repo, customer_id)
                history = report_service.customer_booking_history(repo, customer.customer_id)
                print(f"\nBooking history for {customer.name}: {len(history)} booking(s)")
                print_table(["ID", "Customer", "Vehicle", "Pickup", "Return",
                             "Actual", "Status", "Est. cost"],
                            booking_rows(repo, history))
            run_action(action)
        elif choice == "4":
            def action():
                start = prompt_date("From date (YYYY-MM-DD)")
                end = prompt_date("To date, exclusive (YYYY-MM-DD)", default=start)
                stats = report_service.payments_collected(repo, start, end)
                print(f"\nMoney report for {start.isoformat()} -> {end.isoformat()} "
                      "(end exclusive):")
                print(f"  Collected (payments received) : {money(stats['collected'])}")
                print(f"  Refunded (non-deposit)        : -{money(stats['refunded'])}")
                print(f"  Deposit refunds               : -{money(stats['deposit_refunded'])}")
                print(f"  Net cash movement             :  {money(stats['net'])}")
                print(f"  Billed (invoices issued)      :  {money(stats['billed'])}  "
                      "<- money merely billed, not collected")
            run_action(action)
        elif choice == "5":
            def action():
                rows = report_service.outstanding_bills(repo)
                print(f"\n{len(rows)} bill(s) with an outstanding balance:")
                print_table(["Bill", "Booking", "Total", "Paid", "Outstanding", "Status"],
                            [[r["bill"].bill_id, r["bill"].booking_id,
                              money(r["bill"].total), money(r["paid"]),
                              money(r["outstanding"]), r["status"]] for r in rows])
            run_action(action)
        elif choice == "6":
            def action():
                rows = report_service.most_rented_vehicles(repo, limit=5)
                print("\nMost frequently rented vehicles:")
                print_table(["#", "ID", "Vehicle", "Rego", "Bookings"],
                            [[i, r["vehicle"].vehicle_id,
                              f"{r['vehicle'].make} {r['vehicle'].model}",
                              r["vehicle"].registration_number, r["bookings"]]
                             for i, r in enumerate(rows, start=1)])
            run_action(action)
        elif choice == "7":
            def action():
                vehicles = report_service.vehicles_under_maintenance(repo)
                print(f"\n{len(vehicles)} vehicle(s) under maintenance:")
                print_table(["ID", "Rego", "Make/Model", "Year", "Rate/day", "Status"],
                            vehicle_rows(vehicles))
            run_action(action)
        elif choice == "0":
            return
        else:
            print("Invalid option - choose 0-7.")


def main_menu(repo: Repository) -> None:
    sections = {
        "1": customer_menu,
        "2": vehicle_menu,
        "3": booking_menu,
        "4": billing_menu,
        "5": report_menu,
    }
    while True:
        print(
            "\n======== Vehicle Rental Management System ========\n"
            "  1. Customer Management\n"
            "  2. Vehicle Management\n"
            "  3. Booking and Rental Management\n"
            "  4. Billing and Payments\n"
            "  5. Reports\n"
            "  6. Exit"
        )
        choice = _read("Choose an option: ").strip()
        if choice == "6":
            print("Goodbye.")
            return
        handler = sections.get(choice)
        if handler is None:
            print("Invalid option - choose 1-6.")
            continue
        handler(repo)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vehicle Rental Management System (Python + pickle + CLI)."
    )
    parser.add_argument(
        "--data-dir", default=DEFAULT_DATA_DIR,
        help="Directory holding the .pkl data files (default: ./data)",
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Load sample data if the data directory has no customers yet.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    repo = Repository(data_dir=args.data_dir).load()
    for warning in repo.warnings:
        print(f"Warning: {warning}")
    if args.sample:
        result = repo.generate_sample_data()
        if result.get("created"):
            print("Sample data loaded: "
                  f"{result['customers']} customers, {result['vehicles']} vehicles, "
                  f"{result['bookings']} bookings, {result['bills']} bills.")
        else:
            print(result.get("reason", "Sample data not loaded."))

    counts = (len(repo.customers), len(repo.vehicles), len(repo.bookings),
              len(repo.bills), len(repo.payments))
    print("Vehicle Rental Management System")
    print(f"Data directory: {repo.data_dir}")
    print(f"Loaded: {counts[0]} customer(s), {counts[1]} vehicle(s), "
          f"{counts[2]} booking(s), {counts[3]} bill(s), {counts[4]} payment(s).")

    try:
        main_menu(repo)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n\nGoodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
