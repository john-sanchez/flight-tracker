"""Command line entry point for querying flight offers via Amadeus."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence
from uuid import uuid4

from .amadeus_client import AmadeusFlightClient, FlightOption, FlightSegment
from .config import (
    AppConfig,
    Route,
    load_config,
    parse_currency,
    parse_environment,
    parse_notification_channels,
    parse_routes,
    parse_travel_classes,
)
from .notifications import NotificationError, build_notification_channels
from .storage import RunContext, StorageError, build_storage_backends


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetches flight offers for configured routes via the Amadeus API",
    )
    parser.add_argument(
        "--env-file",
        dest="env_file",
        help="Optional path to the .env file (defaults to .env in the working directory)",
    )
    parser.add_argument(
        "--routes",
        nargs="+",
        help="Override ROUTES config. Provide space separated ORG-DST pairs (e.g. MNL-TYO MNL-KIX).",
    )
    parser.add_argument("--adults", type=int, help="Override ADULTS value (default 1)")
    parser.add_argument(
        "--children",
        type=int,
        help="Override CHILDREN value (default 0)",
    )
    parser.add_argument(
        "--travel-classes",
        dest="travel_classes",
        help="Comma separated list drawn from economy,business (use 'all' for both)",
    )
    parser.add_argument(
        "--departure-date",
        dest="departure_date",
        help="Override DEPARTURE_DATE (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--return-date",
        dest="return_date",
        help="Override RETURN_DATE if round-trip is needed",
    )
    parser.add_argument(
        "--amadeus-env",
        dest="amadeus_env",
        choices=["test", "production"],
        help="Use the Amadeus test or production environment",
    )
    parser.add_argument(
        "--currency",
        dest="currency",
        help="Override the ISO currency code used for pricing (e.g. PHP, USD)",
    )
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        help="Directory where storage backends should write artifacts (default .data)",
    )
    parser.add_argument(
        "--storage-backends",
        dest="storage_backends",
        help="Comma separated list of storage backends to use (default json)",
    )
    parser.add_argument(
        "--notification-channels",
        dest="notification_channels",
        help="Comma separated list of notification channels to use (e.g. email,telegram)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print verbose diagnostics about config and Amadeus requests",
    )
    parser.add_argument(
        "--max-results",
        dest="max_results",
        type=int,
        default=20,
        help="Max results per route/class combination (default 20)",
    )
    return parser


def _merge_config(args: argparse.Namespace, config: AppConfig) -> AppConfig:
    routes = parse_routes(",".join(args.routes)) if args.routes else config.routes
    travel_classes = (
        parse_travel_classes(args.travel_classes)
        if args.travel_classes
        else config.travel_classes
    )
    environment = (
        parse_environment(args.amadeus_env)
        if args.amadeus_env
        else config.environment
    )
    currency = parse_currency(args.currency) if args.currency else config.currency
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else config.data_dir
    storage_backends = (
        [entry.strip().lower() for entry in args.storage_backends.split(",") if entry.strip()]
        if args.storage_backends
        else config.storage_backends
    )
    notification_channels = (
        parse_notification_channels(args.notification_channels)
        if args.notification_channels
        else config.notification_channels
    )

    return AppConfig(
        client_id=config.client_id,
        client_secret=config.client_secret,
        environment=environment,
        currency=currency,
        departure_date=args.departure_date or config.departure_date,
        return_date=args.return_date if args.return_date is not None else config.return_date,
        routes=routes,
        adults=args.adults if args.adults is not None else config.adults,
        children=args.children if args.children is not None else config.children,
        travel_classes=travel_classes,
        data_dir=data_dir,
        storage_backends=storage_backends,
        notification_channels=notification_channels,
    )


def _mask_secret(value: str | None) -> str:
    if not value:
        return "n/a"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _print_debug_config(config: AppConfig) -> None:
    routes = ", ".join(f"{route.origin}-{route.destination}" for route in config.routes)
    travel_classes = ", ".join(config.travel_classes)
    print(
        (
            "DEBUG: config -> client_id=%s client_secret=%s env=%s currency=%s departure=%s return=%s "
            "adults=%s children=%s routes=[%s] travel_classes=[%s] data_dir=%s storage_backends=%s"
        )
        % (
            _mask_secret(config.client_id),
            _mask_secret(config.client_secret),
            config.environment,
            config.currency,
            config.departure_date,
            config.return_date or "n/a",
            config.adults,
            config.children,
            routes,
            travel_classes,
            str(config.data_dir),
            ",".join(config.storage_backends),
        ),
        file=sys.stderr,
    )


_ISO_Z_REPLACEMENT = re.compile("Z$")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value or value == "?":
        return None
    iso_value = _ISO_Z_REPLACEMENT.sub("+00:00", value)
    try:
        return datetime.fromisoformat(iso_value)
    except ValueError:
        return None


def _format_datetime(value: str | None) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return value or "n/a"
    return parsed.strftime("%d %b %Y %H:%M")


def _format_duration(value: str | None) -> str:
    if not value:
        return "n/a"

    hours = minutes = 0
    number = ""
    for char in value:
        if char.isdigit():
            number += char
        elif char == "H" and number:
            hours = int(number)
            number = ""
        elif char == "M" and number:
            minutes = int(number)
            number = ""

    parts: List[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_stops(stops: int) -> str:
    if stops <= 0:
        return "non-stop"
    label = "stop" if stops == 1 else "stops"
    return f"{stops} {label}"


def _format_timedelta(delta_minutes: int | None) -> str:
    if delta_minutes is None:
        return "unknown"
    hours, minutes = divmod(delta_minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _layover_minutes(prev_segment: FlightSegment, next_segment: FlightSegment) -> int | None:
    arrive = _parse_datetime(prev_segment.arrival_time)
    depart = _parse_datetime(next_segment.departure_time)
    if not arrive or not depart:
        return None
    delta = int((depart - arrive).total_seconds() // 60)
    if delta < 0:
        return None
    return delta


def _format_aircraft(segment: FlightSegment | None) -> str:
    if not segment:
        return "n/a"
    code = segment.aircraft_code or "n/a"
    name = segment.aircraft_name
    return f"{code} ({name})" if name else code


def _format_segment(segment: FlightSegment) -> str:
    departure = _format_datetime(segment.departure_time)
    arrival = _format_datetime(segment.arrival_time)
    dep_airport = segment.departure_airport or "?"
    arr_airport = segment.arrival_airport or "?"
    aircraft = _format_aircraft(segment)
    return (
        f"        | {segment.flight_number or '?'}"
        f" | {aircraft}"
        f" | {dep_airport} {departure}"
        f" | {arr_airport} {arrival}"
    )


def _format_offer(offer: FlightOption) -> str:
    duration = _format_duration(offer.duration)
    departure = _format_datetime(offer.departure)
    arrival = _format_datetime(offer.arrival)
    stops = _format_stops(offer.stops)
    first_segment = offer.segments[0] if offer.segments else None
    first_flight = first_segment.flight_number if first_segment else "?"
    first_aircraft = _format_aircraft(first_segment)
    first_depart_airport = first_segment.departure_airport if first_segment else offer.route.origin
    final_arrival_airport = (
        offer.segments[-1].arrival_airport if offer.segments else offer.route.destination
    )

    lines = [
        "---",
        (
            f"{offer.route.origin}->{offer.route.destination}"
            f" | {offer.travel_class.capitalize():9s}"
            f" | {offer.currency} {offer.price:>8.2f}"
            f" | {first_flight}"
            f" | {first_aircraft}"
            f" | {first_depart_airport or '?'} {departure}"
            f" | {final_arrival_airport or '?'} {arrival}"
            f" | duration {duration}"
            f" | {stops}"
        ),
    ]

    if len(offer.segments) > 1:
        lines.append(_format_segment(offer.segments[0]))
        previous = offer.segments[0]
        for segment in offer.segments[1:]:
            layover = _format_timedelta(_layover_minutes(previous, segment))
            lines.append(f"        | layover {layover}")
            lines.append(_format_segment(segment))
            previous = segment

    return "\n".join(lines)


def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.env_file)
    except ValueError as exc:
        parser.error(str(exc))

    merged_config = _merge_config(args, config)

    debug_enabled = args.debug
    if debug_enabled:
        _print_debug_config(merged_config)

    client = AmadeusFlightClient(
        client_id=merged_config.client_id,
        client_secret=merged_config.client_secret,
        environment=merged_config.environment,
        debug=debug_enabled,
    )

    offers: List[FlightOption] = []
    for route in merged_config.routes:
        for travel_class in merged_config.travel_classes:
            try:
                offers.extend(
                    client.fetch_offers(
                        route=route,
                        travel_class=travel_class,
                        departure_date=merged_config.departure_date,
                        return_date=merged_config.return_date,
                        adults=merged_config.adults,
                        children=merged_config.children,
                        currency=merged_config.currency,
                        debug=debug_enabled,
                        max_results=args.max_results,
                    )
                )
            except RuntimeError as exc:
                print(f"Failed to fetch offers for {route.origin}-{route.destination}: {exc}", file=sys.stderr)

    if not offers:
        print("No flight offers were returned. Adjust your search criteria and try again.")
        return 0

    offers.sort(key=lambda offer: offer.price)

    run_context = RunContext(
        run_id=uuid4().hex,
        timestamp=datetime.now(timezone.utc),
        environment=merged_config.environment,
        currency=merged_config.currency,
        departure_date=merged_config.departure_date,
        return_date=merged_config.return_date,
        routes=[f"{route.origin}-{route.destination}" for route in merged_config.routes],
        travel_classes=merged_config.travel_classes,
        adults=merged_config.adults,
        children=merged_config.children,
        max_results=args.max_results,
    )

    try:
        storage_backends = build_storage_backends(
            merged_config.storage_backends,
            merged_config.data_dir,
        )
    except StorageError as exc:
        parser.error(str(exc))

    for backend in storage_backends:
        try:
            artifact = backend.persist(run_context, offers)
            if debug_enabled:
                print(
                    f"DEBUG: storage backend {backend.name} wrote {artifact}",
                    file=sys.stderr,
                )
        except StorageError as exc:
            print(f"Storage backend '{backend.name}' failed: {exc}", file=sys.stderr)

    try:
        notification_channels = build_notification_channels(merged_config.notification_channels)
    except NotificationError as exc:
        parser.error(str(exc))

    for channel in notification_channels:
        try:
            channel.send(run_context, offers)
            if debug_enabled:
                print(f"DEBUG: notification channel {channel.name} sent alert", file=sys.stderr)
        except NotificationError as exc:
            print(f"Notification channel '{channel.name}' failed: {exc}", file=sys.stderr)

    for row in offers:
        print(_format_offer(row))

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
