"""Utilities for loading flight tracker configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from dotenv import load_dotenv


@dataclass(frozen=True)
class Route:
    """Represents a single flight route using IATA airport codes."""

    origin: str
    destination: str


@dataclass(frozen=True)
class AppConfig:
    """Holds runtime configuration loaded from the environment."""

    client_id: str
    client_secret: str
    environment: str
    currency: str
    departure_date: str
    return_date: str | None
    routes: List[Route]
    adults: int
    children: int
    travel_classes: List[str]


def parse_environment(value: str | None) -> str:
    allowed = {"test", "production"}
    normalized = (value or "production").strip().lower()
    if normalized not in allowed:
        raise ValueError("AMADEUS_ENV must be either 'test' or 'production'")
    return normalized


def parse_currency(value: str | None) -> str:
    currency = (value or "EUR").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("CURRENCY must be a 3-letter ISO code (e.g. PHP, USD, EUR)")
    return currency


def parse_routes(value: str | None) -> List[Route]:
    if not value:
        raise ValueError("ROUTES must contain at least one origin-destination pair")

    routes: List[Route] = []
    for raw_route in value.split(","):
        codes = [code.strip().upper() for code in raw_route.split("-") if code.strip()]
        if len(codes) != 2:
            raise ValueError(
                "Each route must be formatted as ORG-DST using IATA codes (e.g. MNL-TYO)"
            )
        routes.append(Route(origin=codes[0], destination=codes[1]))
    return routes


def parse_travel_classes(value: str | None) -> List[str]:
    allowed = {"economy", "business"}
    if not value or value.strip().lower() == "all":
        return sorted(allowed)

    parsed = []
    for option in value.split(","):
        normalized = option.strip().lower()
        if normalized not in allowed:
            raise ValueError("Only 'economy' and 'business' travel classes are supported")
        parsed.append(normalized)

    deduped = sorted({*parsed})
    if not deduped:
        raise ValueError("At least one travel class must be specified")
    return deduped


def _load_env_file(env_path: str | os.PathLike[str] | None) -> None:
    if env_path is None:
        load_dotenv()
        return

    path = Path(env_path)
    load_dotenv(path)


def load_config(env_path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load configuration from the provided .env file or the environment."""

    _load_env_file(env_path)

    client_id = os.getenv("AMADEUS_CLIENT_ID")
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
    departure_date = os.getenv("DEPARTURE_DATE")

    if not client_id or not client_secret:
        raise ValueError("Amadeus credentials (AMADEUS_CLIENT_ID/SECRET) are required")
    if not departure_date:
        raise ValueError("DEPARTURE_DATE is required (format YYYY-MM-DD)")

    return AppConfig(
        client_id=client_id,
        client_secret=client_secret,
        environment=parse_environment(os.getenv("AMADEUS_ENV")),
        currency=parse_currency(os.getenv("CURRENCY")),
        departure_date=departure_date,
        return_date=os.getenv("RETURN_DATE") or None,
        routes=parse_routes(os.getenv("ROUTES")),
        adults=int(os.getenv("ADULTS", "1")),
        children=int(os.getenv("CHILDREN", "0")),
        travel_classes=parse_travel_classes(os.getenv("TRAVEL_CLASSES")),
    )
