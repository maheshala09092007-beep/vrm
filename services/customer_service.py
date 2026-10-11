"""Customer management: registration, search, update.

Business rules enforced here:
* IDs are generated sequentially (``CUS-0001``) and are unique.
* Driving licence numbers are unique across all customers.
* Customers are never deleted, so records tied to bookings are preserved.
* All input passes through :mod:`validators` before it is stored.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from models import Customer
from storage import Repository
from validators import (
    validate_address,
    validate_email,
    validate_license,
    validate_name,
    validate_phone,
)


def register_customer(
    repo: Repository,
    name: str,
    phone: str,
    email: str,
    address: str,
    license_number: str,
) -> Customer:
    """Validate and store a new customer, then persist the collection."""
    clean_name = validate_name(name)
    clean_phone = validate_phone(phone)
    clean_email = validate_email(email)
    clean_address = validate_address(address)
    clean_license = validate_license(license_number)

    _assert_license_unique(repo, clean_license)

    customer = Customer(
        customer_id=Repository.next_id(repo.customers, "CUS"),
        name=clean_name,
        phone=clean_phone,
        email=clean_email,
        address=clean_address,
        license_number=clean_license,
    )
    repo.customers[customer.customer_id] = customer
    repo.save("customers")
    return customer


def _assert_license_unique(repo: Repository, license_number: str, exclude_id: str = "") -> None:
    for existing in repo.customers.values():
        if existing.customer_id != exclude_id and \
                existing.license_number.upper() == license_number.upper():
            raise ValueError(
                f"Driving licence {license_number} is already registered to "
                f"{existing.name} ({existing.customer_id})."
            )


def get_customer(repo: Repository, customer_id: str) -> Customer:
    key = (customer_id or "").strip().upper()
    customer = repo.customers.get(key)
    if customer is None:
        raise ValueError(f"No customer found with ID '{customer_id}'.")
    return customer


def list_customers(repo: Repository) -> List[Customer]:
    return sorted(repo.customers.values(), key=lambda c: c.customer_id)


def search_customers(repo: Repository, query: str) -> List[Customer]:
    """Search by ID, name, phone or licence number (case-insensitive)."""
    if not (query or "").strip():
        raise ValueError("Search term is required.")
    return [c for c in list_customers(repo) if c.matches(query)]


def update_customer(repo: Repository, customer_id: str, **fields) -> Customer:
    """Update the given fields of a customer.

    Only non-``None`` values are applied; empty strings are rejected by the
    validators. Unknown field names raise ``ValueError``.
    """
    allowed = {"name", "phone", "email", "address", "license_number"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown customer field(s): {', '.join(sorted(unknown))}.")

    customer = get_customer(repo, customer_id)

    cleaned: Dict[str, str] = {}
    if fields.get("name") is not None:
        cleaned["name"] = validate_name(fields["name"])
    if fields.get("phone") is not None:
        cleaned["phone"] = validate_phone(fields["phone"])
    if fields.get("email") is not None:
        cleaned["email"] = validate_email(fields["email"])
    if fields.get("address") is not None:
        cleaned["address"] = validate_address(fields["address"])
    if fields.get("license_number") is not None:
        new_license = validate_license(fields["license_number"])
        _assert_license_unique(repo, new_license, exclude_id=customer.customer_id)
        cleaned["license_number"] = new_license

    if not cleaned:
        raise ValueError("Nothing to update.")

    for key, value in cleaned.items():
        setattr(customer, key, value)
    repo.save("customers")
    return customer
