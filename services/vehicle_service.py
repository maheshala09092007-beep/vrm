"""Vehicle management: catalogue, search, status changes, availability.

Availability is *derived*: a vehicle is available for ``[start, end)`` when
its operational status is ``Active`` **and** no non-cancelled booking of that
vehicle overlaps the range. There is no manually editable availability flag.

Overlap convention (used everywhere in this project):
* rental start (pickup) date is **inclusive**,
* return date is **exclusive**,
* so a booking returned on day X leaves the vehicle free from day X on.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from models import Booking, BookingStatus, Vehicle, VehicleStatus
from storage import Repository
from validators import (
    parse_decimal,
    validate_fuel_type,
    validate_positive_int,
    validate_registration,
    validate_vehicle_status,
    validate_vehicle_type,
    validate_year,
)

# Far-future sentinel used for open-ended occupancy (vehicle picked up and
# not yet returned).
_OPEN_END = date.max

#: Minimum daily rate so zero/negative rates can never be stored.
MIN_DAILY_RATE = Decimal("0.01")


# ---------------------------------------------------------------------------
# Occupancy / availability
# ---------------------------------------------------------------------------

def occupancy_range(booking: Booking) -> Optional[Tuple[date, date]]:
    """Return the half-open range ``[start, end)`` a booking occupies.

    Returns ``None`` when the booking does not occupy the vehicle at all
    (cancelled bookings).
    """
    if booking.status == BookingStatus.CANCELLED:
        return None
    start = booking.pickup_date
    if booking.status == BookingStatus.PICKED_UP and booking.actual_return_date is None:
        # Still out with no return recorded: occupied until it comes back.
        return start, _OPEN_END
    end = booking.actual_return_date or booking.expected_return_date
    return start, end


def ranges_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Half-open interval overlap test: start inclusive, end exclusive."""
    return a_start < b_end and b_start < a_end


def find_conflict(
    repo: Repository,
    vehicle_id: str,
    start: date,
    end: date,
    exclude_booking_id: str = "",
) -> Optional[Booking]:
    """Return an existing booking of ``vehicle_id`` overlapping ``[start, end)``."""
    if end <= start:
        raise ValueError("End date must be after the start date.")
    for booking in repo.bookings.values():
        if booking.vehicle_id != vehicle_id:
            continue
        if exclude_booking_id and booking.booking_id == exclude_booking_id:
            continue
        occupied = occupancy_range(booking)
        if occupied is None:
            continue
        if ranges_overlap(start, end, occupied[0], occupied[1]):
            return booking
    return None


def is_vehicle_available(
    repo: Repository,
    vehicle_id: str,
    start: date,
    end: date,
    exclude_booking_id: str = "",
) -> bool:
    vehicle = get_vehicle(repo, vehicle_id)
    if vehicle.status != VehicleStatus.ACTIVE:
        return False
    return find_conflict(repo, vehicle_id, start, end, exclude_booking_id) is None


def available_vehicles(repo: Repository, start: date, end: date) -> List[Vehicle]:
    """Vehicles that are Active and free for the whole ``[start, end)`` range."""
    if end <= start:
        raise ValueError("End date must be after the start date.")
    result = []
    for vehicle in list_vehicles(repo):
        if vehicle.status != VehicleStatus.ACTIVE:
            continue
        if find_conflict(repo, vehicle.vehicle_id, start, end) is None:
            result.append(vehicle)
    return result


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_vehicle(
    repo: Repository,
    registration_number: str,
    make: str,
    model: str,
    year,
    vehicle_type: str,
    fuel_type: str,
    seating_capacity,
    daily_rate,
    status: str = VehicleStatus.ACTIVE,
) -> Vehicle:
    rego = validate_registration(registration_number)
    _assert_rego_unique(repo, rego)

    clean_make = _validate_text(make, "Make", 2, 40)
    clean_model = _validate_text(model, "Model", 1, 60)
    clean_year = validate_year(year)
    clean_type = validate_vehicle_type(vehicle_type)
    clean_fuel = validate_fuel_type(fuel_type)
    clean_seats = validate_positive_int(seating_capacity, "Seating capacity", 1, 100)
    clean_rate = parse_decimal(daily_rate, "Daily rental rate", allow_zero=False)
    if clean_rate < MIN_DAILY_RATE:
        raise ValueError(f"Daily rental rate must be at least {MIN_DAILY_RATE}.")
    clean_status = validate_vehicle_status(status)

    vehicle = Vehicle(
        vehicle_id=Repository.next_id(repo.vehicles, "VEH"),
        registration_number=rego,
        make=clean_make,
        model=clean_model,
        year=clean_year,
        vehicle_type=clean_type,
        fuel_type=clean_fuel,
        seating_capacity=clean_seats,
        daily_rate=clean_rate,
        status=clean_status,
    )
    repo.vehicles[vehicle.vehicle_id] = vehicle
    repo.save("vehicles")
    return vehicle


def _validate_text(value, field_name: str, min_len: int, max_len: int) -> str:
    text = (str(value) if value is not None else "").strip()
    if not min_len <= len(text) <= max_len:
        raise ValueError(
            f"{field_name} must be between {min_len} and {max_len} characters."
        )
    return text


def _assert_rego_unique(repo: Repository, registration_number: str, exclude_id: str = "") -> None:
    for existing in repo.vehicles.values():
        if existing.vehicle_id != exclude_id and \
                existing.registration_number.upper() == registration_number.upper():
            raise ValueError(
                f"Registration number {registration_number} already belongs to "
                f"{existing.label} ({existing.vehicle_id})."
            )


def get_vehicle(repo: Repository, vehicle_id: str) -> Vehicle:
    key = (vehicle_id or "").strip().upper()
    vehicle = repo.vehicles.get(key)
    if vehicle is None:
        raise ValueError(f"No vehicle found with ID '{vehicle_id}'.")
    return vehicle


def list_vehicles(repo: Repository) -> List[Vehicle]:
    return sorted(repo.vehicles.values(), key=lambda v: v.vehicle_id)


def search_vehicles(repo: Repository, query: str) -> List[Vehicle]:
    if not (query or "").strip():
        raise ValueError("Search term is required.")
    return [v for v in list_vehicles(repo) if v.matches(query)]


def update_vehicle(repo: Repository, vehicle_id: str, **fields) -> Vehicle:
    """Update vehicle attributes. Registration number stays unique."""
    allowed = {
        "registration_number", "make", "model", "year", "vehicle_type",
        "fuel_type", "seating_capacity", "daily_rate",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown vehicle field(s): {', '.join(sorted(unknown))}.")

    vehicle = get_vehicle(repo, vehicle_id)

    if fields.get("registration_number") is not None:
        rego = validate_registration(fields["registration_number"])
        _assert_rego_unique(repo, rego, exclude_id=vehicle.vehicle_id)
        vehicle.registration_number = rego
    if fields.get("make") is not None:
        vehicle.make = _validate_text(fields["make"], "Make", 2, 40)
    if fields.get("model") is not None:
        vehicle.model = _validate_text(fields["model"], "Model", 1, 60)
    if fields.get("year") is not None:
        vehicle.year = validate_year(fields["year"])
    if fields.get("vehicle_type") is not None:
        vehicle.vehicle_type = validate_vehicle_type(fields["vehicle_type"])
    if fields.get("fuel_type") is not None:
        vehicle.fuel_type = validate_fuel_type(fields["fuel_type"])
    if fields.get("seating_capacity") is not None:
        vehicle.seating_capacity = validate_positive_int(
            fields["seating_capacity"], "Seating capacity", 1, 100
        )
    if fields.get("daily_rate") is not None:
        rate = parse_decimal(fields["daily_rate"], "Daily rental rate", allow_zero=False)
        if rate < MIN_DAILY_RATE:
            raise ValueError(f"Daily rental rate must be at least {MIN_DAILY_RATE}.")
        vehicle.daily_rate = rate

    if not fields:
        raise ValueError("Nothing to update.")
    repo.save("vehicles")
    return vehicle


def set_vehicle_status(repo: Repository, vehicle_id: str, status: str) -> Vehicle:
    """Change the operational status (Active / Maintenance / Retired)."""
    vehicle = get_vehicle(repo, vehicle_id)
    clean = validate_vehicle_status(status)
    if vehicle.status == clean:
        raise ValueError(f"{vehicle.label} is already '{clean}'.")
    vehicle.status = clean
    repo.save("vehicles")
    return vehicle
