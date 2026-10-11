"""Input validation helpers shared by services and the CLI.

Every function raises ``ValueError`` with a human friendly message when the
input is invalid, so both the CLI and the tests can rely on the same rules.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from models import FUEL_TYPES, VEHICLE_TYPES, VehicleStatus

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,79}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 -]{6,19}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
LICENSE_RE = re.compile(r"^[A-Za-z0-9-]{3,30}$")
REGO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{1,19}$")
DATE_FMT = "%Y-%m-%d"

MIN_YEAR = 1900


def parse_date(value: str, field_name: str = "date") -> date:
    """Parse a ``YYYY-MM-DD`` string into a :class:`datetime.date`."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required (format YYYY-MM-DD).")
    try:
        parsed = datetime.strptime(text, DATE_FMT).date()
    except ValueError:
        raise ValueError(
            f"Invalid {field_name} '{text}'. Expected format YYYY-MM-DD."
        ) from None
    return parsed


def validate_date_range(pickup: date, ret: date, today: Optional[date] = None) -> None:
    """Pickup must not be in the past and return must be after pickup.

    Rental start date is inclusive, return date is exclusive, so the return
    date must be strictly later than the pickup date (minimum one day).
    """
    reference = today if today is not None else date.today()
    if pickup < reference:
        raise ValueError(f"Pickup date {pickup.isoformat()} cannot be in the past.")
    if ret <= pickup:
        raise ValueError("Expected return date must be after the pickup date.")


def validate_name(value: str, field_name: str = "Name") -> str:
    text = (value or "").strip()
    if not NAME_RE.match(text):
        raise ValueError(
            f"Invalid {field_name}. Use 2-80 letters, spaces, . ' or - "
            "(e.g. 'Mary A. O'Brien')."
        )
    return text


def validate_phone(value: str) -> str:
    text = (value or "").strip()
    digits = re.sub(r"[^0-9]", "", text)
    if not PHONE_RE.match(text) or not 7 <= len(digits) <= 15:
        raise ValueError("Invalid phone number. Use 7-15 digits, optional + or dashes.")
    return text


def validate_email(value: str) -> str:
    text = (value or "").strip()
    if not EMAIL_RE.match(text) or len(text) > 120:
        raise ValueError("Invalid email address (e.g. name@example.com).")
    return text


def validate_license(value: str) -> str:
    text = (value or "").strip().upper()
    if not LICENSE_RE.match(text):
        raise ValueError(
            "Invalid driving licence number (3-30 letters, digits or dashes)."
        )
    return text


def validate_address(value: str) -> str:
    text = (value or "").strip()
    if not 3 <= len(text) <= 200:
        raise ValueError("Address must be between 3 and 200 characters.")
    return text


def validate_registration(value: str) -> str:
    text = (value or "").strip().upper()
    if not REGO_RE.match(text):
        raise ValueError(
            "Invalid registration number (2-20 letters, digits, spaces or dashes)."
        )
    return text


def validate_year(value) -> int:
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ValueError("Manufacturing year must be a whole number.") from None
    current = date.today().year
    if not MIN_YEAR <= year <= current + 1:
        raise ValueError(f"Manufacturing year must be between {MIN_YEAR} and {current + 1}.")
    return year


def validate_positive_int(value, field_name: str, minimum: int = 1, maximum: int = 1000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a whole number.") from None
    if not minimum <= number <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}.")
    return number


def parse_decimal(value, field_name: str = "amount", allow_zero: bool = True) -> Decimal:
    """Parse a monetary string into ``Decimal`` (never float)."""
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        # Avoid binary float artefacts by going through str().
        number = Decimal(str(value))
    else:
        text = (str(value) if value is not None else "").strip().replace(",", "")
        if text.startswith("$"):
            text = text[1:]
        if not text:
            raise ValueError(f"{field_name} is required.")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"Invalid {field_name} '{value}'. Enter a number like 45.50.") from None
    if number.is_nan() or number.is_infinite():
        raise ValueError(f"Invalid {field_name}.")
    if number < 0 or (number == 0 and not allow_zero):
        word = "positive " if not allow_zero else ""
        raise ValueError(f"{field_name.capitalize()} must be a {word}number (no negatives).")
    return number.quantize(Decimal("0.01"))


def validate_vehicle_type(value: str) -> str:
    text = (value or "").strip().lower()
    if text not in VEHICLE_TYPES:
        raise ValueError(f"Vehicle type must be one of: {', '.join(VEHICLE_TYPES)}.")
    return text


def validate_fuel_type(value: str) -> str:
    text = (value or "").strip().lower()
    if text not in FUEL_TYPES:
        raise ValueError(f"Fuel type must be one of: {', '.join(FUEL_TYPES)}.")
    return text


def validate_vehicle_status(value: str) -> str:
    text = (value or "").strip()
    for status in VehicleStatus.ALL:
        if text.lower() == status.lower():
            return status
    raise ValueError(f"Status must be one of: {', '.join(VehicleStatus.ALL)}.")
