"""Pluggable storage backends for persisting flight offer runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence

from ..amadeus_client import FlightOption

__all__ = [
    "RunContext",
    "StorageBackend",
    "StorageError",
    "build_storage_backends",
]


class StorageError(RuntimeError):
    """Raised when a storage backend cannot persist results."""


@dataclass(frozen=True)
class RunContext:
    run_id: str
    timestamp: datetime
    environment: str
    currency: str
    departure_date: str
    return_date: str | None
    routes: List[str]
    travel_classes: List[str]
    adults: int
    children: int
    max_results: int

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": _isoformat(self.timestamp),
            "environment": self.environment,
            "currency": self.currency,
            "departure_date": self.departure_date,
            "return_date": self.return_date,
            "routes": self.routes,
            "travel_classes": self.travel_classes,
            "adults": self.adults,
            "children": self.children,
            "max_results": self.max_results,
        }


class StorageBackend:
    """Base class for storage implementations."""

    name = "base"

    def persist(self, run: RunContext, offers: Sequence[FlightOption]) -> Path | None:  # pragma: no cover - interface
        raise NotImplementedError


class JsonStorageBackend(StorageBackend):
    name = "json"

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def persist(self, run: RunContext, offers: Sequence[FlightOption]) -> Path:
        if not offers:
            raise StorageError("JsonStorageBackend received no offers to persist")

        timestamp = run.timestamp.astimezone(timezone.utc)
        date_dir = self._base_dir / timestamp.strftime("%Y-%m-%d")
        try:
            date_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Failed to create data directory {date_dir}: {exc}") from exc

        filename = f"flight-offers-{run.run_id}.json"
        path = date_dir / filename

        payload = {
            "run": run.to_dict(),
            "offers": [_serialize_offer(offer) for offer in offers],
        }

        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"Failed to write JSON data to {path}: {exc}") from exc

        return path


def build_storage_backends(specs: Sequence[str], data_dir: Path) -> List[StorageBackend]:
    backends: List[StorageBackend] = []
    normalized = [spec.strip() for spec in specs if spec and spec.strip()]
    if not normalized:
        normalized = ["json"]

    for spec in normalized:
        name, _, param = spec.partition(":")
        backend_name = name.lower()
        if backend_name == "json":
            base = Path(param).expanduser() if param else data_dir
            backends.append(JsonStorageBackend(base))
        else:
            raise StorageError(f"Unknown storage backend '{backend_name}'")

    return backends


def _serialize_offer(offer: FlightOption) -> dict:
    return {
        "route": {
            "origin": offer.route.origin,
            "destination": offer.route.destination,
        },
        "travel_class": offer.travel_class,
        "price": offer.price,
        "currency": offer.currency,
        "departure": offer.departure,
        "arrival": offer.arrival,
        "duration": offer.duration,
        "stops": offer.stops,
        "segments": [
            {
                "carrier_code": segment.carrier_code,
                "number": segment.number,
                "flight_number": segment.flight_number,
                "aircraft_code": segment.aircraft_code,
                "aircraft_name": segment.aircraft_name,
                "departure_airport": segment.departure_airport,
                "arrival_airport": segment.arrival_airport,
                "departure_time": segment.departure_time,
                "arrival_time": segment.arrival_time,
            }
            for segment in offer.segments
        ],
    }


def _isoformat(value: datetime) -> str:
    as_utc = value.astimezone(timezone.utc)
    return as_utc.isoformat().replace("+00:00", "Z")
