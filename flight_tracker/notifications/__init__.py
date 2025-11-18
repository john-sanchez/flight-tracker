"""Pluggable notification channels for sharing flight offer summaries."""

from __future__ import annotations

import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from importlib import resources
from typing import Iterable, List, Sequence, Tuple

import requests

from ..amadeus_client import FlightOption
from ..storage import RunContext

__all__ = [
    "NotificationChannel",
    "NotificationError",
    "build_notification_channels",
]


class NotificationError(RuntimeError):
    """Raised when a notification channel cannot deliver a message."""


def _load_template(filename: str) -> str:
    template_pkg = f"{__name__}.templates"
    try:
        data = resources.files(template_pkg).joinpath(filename).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:  # pragma: no cover - defensive
        raise NotificationError(f"Template '{filename}' is missing: {exc}") from exc
    return data


EMAIL_SUBJECT_TEMPLATE = _load_template("email_subject.txt").strip()
EMAIL_BODY_TEMPLATE = _load_template("email_body.txt").rstrip() + "\n"
TELEGRAM_MESSAGE_TEMPLATE = _load_template("telegram_message.txt").strip()


class NotificationChannel:
    """Base class for notification implementations."""

    name = "base"

    def send(self, run: RunContext, offers: Sequence[FlightOption]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def build_notification_channels(specs: Sequence[str]) -> List[NotificationChannel]:
    channels: List[NotificationChannel] = []
    normalized = [spec.strip() for spec in specs if spec and spec.strip()]
    if not normalized:
        return channels

    for spec in normalized:
        name, _, param = spec.partition(":")
        channel_name = name.lower()
        if channel_name == "email":
            channels.append(EmailNotificationChannel.from_env(param or None))
        elif channel_name == "telegram":
            channels.append(TelegramNotificationChannel.from_env(param or None))
        else:
            raise NotificationError(f"Unknown notification channel '{channel_name}'")

    return channels


def _format_datetime(value: str | None) -> str:
    if not value or value == "?":
        return "n/a"
    try:
        return value.replace("T", " ").replace("Z", " UTC")
    except AttributeError:
        return str(value)


def _format_timestamp(value) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


_ISO_Z_REPLACEMENT = re.compile("Z$")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value or value == "?":
        return None
    iso_value = _ISO_Z_REPLACEMENT.sub("+00:00", value)
    try:
        return datetime.fromisoformat(iso_value)
    except ValueError:
        return None


DEFAULT_TOP_OFFER_LINE_TEMPLATE = (
    "- **{route}** | {travel_class_title} | {currency} {price:,.2f}\n"
    "  **Depart**: {departure} | **Stops**: {stop_label}\n"
    "  **Flights**: {outbound_flights}\n"
    "  **Return**: {return_flights}\n"
)


def _build_top_offer_context(offer: FlightOption, index: int) -> dict[str, object]:
    return_segments = getattr(offer, "return_segments", [])
    outbound_flights = _format_segment_paths(offer.segments)
    return_flights = _format_segment_paths(return_segments)
    return {
        "index": index,
        "origin": offer.route.origin,
        "destination": offer.route.destination,
        "route": f"{offer.route.origin}->{offer.route.destination}",
        "travel_class": offer.travel_class,
        "travel_class_title": offer.travel_class.title(),
        "currency": offer.currency,
        "price": offer.price,
        "departure": _format_datetime(offer.departure),
        "departure_raw": offer.departure,
        "arrival": _format_datetime(offer.arrival),
        "arrival_raw": offer.arrival,
        "stops": offer.stops,
        "stop_label": _format_stop_label(offer.stops),
        "duration": offer.duration or "n/a",
        "duration_raw": offer.duration,
        "flight_number": _lead_flight_number(offer),
        "flight_numbers": _all_flight_numbers(offer),
        "outbound_flights": outbound_flights,
        "return_flights": return_flights,
        "has_return": bool(return_segments),
        "return_stops": max(len(return_segments) - 1, 0) if return_segments else 0,
        "return_stop_label": _format_return_stop_label(return_segments),
        "outbound_flights_complete": _format_complete_flight(offer.segments, "Departing Flight"),
        "return_flights_complete": _format_complete_flight(return_segments, "Returning Flight"),
        "outbound_flights_compact": _format_compact_flight(offer.segments, "Departing Flight"),
        "return_flights_compact": _format_compact_flight(return_segments, "Returning Flight"),
    }


def _summarize_offers(
    offers: Sequence[FlightOption],
    limit: int = 5,
    line_template: str | None = None,
) -> str:
    top = list(offers[:limit])
    if not top:
        return "No offers were returned."

    template = line_template or DEFAULT_TOP_OFFER_LINE_TEMPLATE
    lines = []
    for idx, offer in enumerate(top, 1):
        context = _build_top_offer_context(offer, idx)
        try:
            lines.append(template.format(**context))
        except (KeyError, ValueError) as exc:
            raise NotificationError(f"Invalid top-offer template '{template}': {exc}") from exc
    return "\n".join(lines)


def _format_stop_label(stops: int) -> str:
    if stops <= 0:
        return "non-stop"
    label = "stop" if stops == 1 else "stops"
    return f"{stops} {label}"


def _lead_flight_number(offer: FlightOption) -> str:
    if offer.segments:
        return offer.segments[0].flight_number or "?"
    return "?"


def _all_flight_numbers(offer: FlightOption) -> str:
    numbers: List[str] = []
    for segment in offer.segments:
        numbers.append(segment.flight_number or "?")
    for segment in getattr(offer, "return_segments", []):
        numbers.append(segment.flight_number or "?")
    if not numbers:
        return "?"
    return ", ".join(numbers)


def _format_segment_paths(segments: Sequence) -> str:
    if not segments:
        return "n/a"
    parts = []
    for segment in segments:
        dep = segment.departure_airport or "?"
        arr = segment.arrival_airport or "?"
        parts.append(f"{segment.flight_number or '?'} {dep}->{arr}")
    return "; ".join(parts)


def _format_return_stop_label(segments: Sequence) -> str:
    if not segments:
        return "n/a"
    stops = max(len(segments) - 1, 0)
    return _format_stop_label(stops)


def _format_minutes_label(minutes: int | None) -> str:
    if minutes is None:
        return "unknown"
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)


def _minutes_between(start: str | None, end: str | None) -> int | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if not start_dt or not end_dt:
        return None
    delta = int((end_dt - start_dt).total_seconds() // 60)
    if delta < 0:
        return None
    return delta


def _format_date_no_year(value: str | None) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return "n/a"
    return parsed.strftime("%d %b")


def _format_time_with_reference(value: str | None, reference: str | None) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return value or "n/a"
    label = parsed.strftime("%H:%M")
    if reference:
        ref_dt = _parse_datetime(reference)
        if ref_dt:
            delta_days = (parsed.date() - ref_dt.date()).days
            if delta_days > 0:
                label += f" (+{delta_days})"
    return label


def _segment_duration_minutes(segment: FlightSegment) -> int | None:
    return _minutes_between(segment.departure_time, segment.arrival_time)


def _format_complete_flight(
    segments: Sequence[FlightSegment],
    heading_label: str,
) -> str:
    if not segments:
        return f"    {heading_label} - n/a"

    lines: List[str] = []
    first = segments[0]
    heading = f"    {heading_label} - {first.flight_number or '?'} {_format_date_no_year(first.departure_time)}"
    lines.append(heading)
    base_reference = first.departure_time

    for idx, segment in enumerate(segments):
        if idx > 0:
            lines.append(f"    {segment.flight_number or '?'}")
        depart_label = _format_time_with_reference(segment.departure_time, base_reference)
        depart_airport = segment.departure_airport or "?"
        lines.append(f"      {depart_label} {depart_airport}")
        duration_label = _format_minutes_label(_segment_duration_minutes(segment))
        lines.append(f"      Travel time: {duration_label}")
        arrival_label = _format_time_with_reference(segment.arrival_time, base_reference)
        arrival_airport = segment.arrival_airport or "?"
        lines.append(f"      {arrival_label} {arrival_airport}")
        if idx < len(segments) - 1:
            layover_label = _format_minutes_label(
                _minutes_between(segment.arrival_time, segments[idx + 1].departure_time)
            )
            lines.append(f"    {layover_label} layover")

    return "\n".join(lines)


def _format_compact_flight(
    segments: Sequence[FlightSegment],
    heading_label: str,
) -> str:
    if not segments:
        return f"    {heading_label} n/a"

    lines: List[str] = []
    first = segments[0]
    date_label = _format_date_no_year(first.departure_time)
    lines.append(f"    {heading_label} {date_label}")
    base_reference = first.departure_time

    for idx, segment in enumerate(segments):
        depart_label = _format_time_with_reference(segment.departure_time, base_reference)
        arrival_label = _format_time_with_reference(segment.arrival_time, base_reference)
        depart_airport = segment.departure_airport or "?"
        arrival_airport = segment.arrival_airport or "?"
        duration_label = _format_minutes_label(_segment_duration_minutes(segment))
        flight_number = segment.flight_number or "?"
        lines.append(
            (
                f"      {depart_label} {depart_airport} - {arrival_label} {arrival_airport}"
                f" ({duration_label}) | {flight_number}"
            )
        )
        if idx < len(segments) - 1:
            layover_label = _format_minutes_label(
                _minutes_between(segment.arrival_time, segments[idx + 1].departure_time)
            )
            lines.append(f"      {layover_label} layover")

    return "\n".join(lines)


DEFAULT_PERMUTATION_LINE_TEMPLATE = (
    "- **{route}** | {travel_class} | {stop_label}\n"
    "  **Outbound**: {outbound_flights}\n"
    "  **Return**: {return_flights}\n"
    "  **Fare**: {currency} {price:,.2f}\n"
)


def _summarize_permutations(
    offers: Sequence[FlightOption],
    line_template: str | None = None,
) -> str:
    if not offers:
        return "No offers were returned."

    template = line_template or DEFAULT_PERMUTATION_LINE_TEMPLATE
    best: dict[Tuple[str, str, str, int], FlightOption] = {}
    for offer in offers:
        key = (offer.route.origin, offer.route.destination, offer.travel_class, offer.stops)
        existing = best.get(key)
        if existing is None or offer.price < existing.price:
            best[key] = offer

    lines = []
    items = sorted(
        best.items(),
        key=lambda item: (
            item[1].price,
            item[0][0],
            item[0][1],
            item[0][2],
            item[0][3],
        ),
    )

    for idx, (key, offer) in enumerate(items, 1):
        route = f"{offer.route.origin}->{offer.route.destination}"
        stop_label = _format_stop_label(offer.stops)
        flight_number = _lead_flight_number(offer)
        flight_numbers = _all_flight_numbers(offer)
        return_segments = getattr(offer, "return_segments", [])
        outbound_flights = _format_segment_paths(offer.segments)
        return_flights = _format_segment_paths(return_segments)
        return_stop_label = _format_return_stop_label(return_segments)
        return_stops = max(len(return_segments) - 1, 0) if return_segments else 0
        context = {
            "index": idx,
            "route": route,
            "origin": offer.route.origin,
            "destination": offer.route.destination,
            "travel_class": offer.travel_class,
            "stops": offer.stops,
            "stop_label": stop_label,
            "flight_number": flight_number,
            "flight_numbers": flight_numbers,
            "outbound_flights": outbound_flights,
            "return_flights": return_flights,
            "return_stops": return_stops,
            "return_stop_label": return_stop_label,
            "has_return": bool(return_segments),
            "currency": offer.currency,
            "price": offer.price,
            "outbound_flights_complete": _format_complete_flight(offer.segments, "Departing Flight"),
            "return_flights_complete": _format_complete_flight(return_segments, "Returning Flight"),
            "outbound_flights_compact": _format_compact_flight(offer.segments, "Departing Flight"),
            "return_flights_compact": _format_compact_flight(return_segments, "Returning Flight"),
        }
        try:
            lines.append(template.format(**context))
        except (KeyError, ValueError) as exc:
            raise NotificationError(f"Invalid permutation template '{template}': {exc}") from exc
    return "\n".join(lines)


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    sender: str
    recipients: List[str]
    username: str | None
    password: str | None
    use_tls: bool
    subject_prefix: str | None = None
    top_offers_template: str | None = None
    top_offers_limit: int = 5

    @classmethod
    def from_env(cls, prefix: str | None = None) -> "EmailSettings":
        env_prefix = (prefix or "EMAIL").upper()
        host = os.getenv(f"{env_prefix}_HOST")
        port_value = os.getenv(f"{env_prefix}_PORT", "587")
        sender = os.getenv(f"{env_prefix}_FROM")
        recipients_raw = os.getenv(f"{env_prefix}_TO", "")
        username = os.getenv(f"{env_prefix}_USERNAME")
        password = os.getenv(f"{env_prefix}_PASSWORD")
        use_tls_value = os.getenv(f"{env_prefix}_USE_TLS", "true")
        subject_prefix = os.getenv(f"{env_prefix}_SUBJECT_PREFIX")
        top_offers_template_raw = os.getenv(f"{env_prefix}_TOP_OFFERS_TEMPLATE", "")
        top_offers_limit_raw = os.getenv(f"{env_prefix}_TOP_OFFERS_LIMIT", "").strip()

        if not host or not sender or not recipients_raw.strip():
            raise NotificationError(
                f"{env_prefix}_HOST, {env_prefix}_FROM, and {env_prefix}_TO must be configured for email notifications"
            )

        try:
            port = int(port_value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise NotificationError(f"{env_prefix}_PORT must be an integer") from exc

        recipients = [addr.strip() for addr in recipients_raw.split(",") if addr.strip()]
        if not recipients:
            raise NotificationError(f"{env_prefix}_TO must list at least one recipient email address")

        use_tls = use_tls_value.strip().lower() not in {"false", "0", "no"}
        top_offers_template = top_offers_template_raw.strip() or None
        top_offers_limit = 5
        if top_offers_limit_raw:
            try:
                top_offers_limit = int(top_offers_limit_raw)
            except ValueError as exc:  # pragma: no cover - defensive
                raise NotificationError(f"{env_prefix}_TOP_OFFERS_LIMIT must be an integer") from exc
            if top_offers_limit <= 0:
                raise NotificationError(f"{env_prefix}_TOP_OFFERS_LIMIT must be greater than zero")

        return cls(
            host=host,
            port=port,
            sender=sender,
            recipients=recipients,
            username=username,
            password=password,
            use_tls=use_tls,
            subject_prefix=subject_prefix,
            top_offers_template=top_offers_template,
            top_offers_limit=top_offers_limit,
        )


class EmailNotificationChannel(NotificationChannel):
    name = "email"

    def __init__(self, settings: EmailSettings) -> None:
        self._settings = settings

    @classmethod
    def from_env(cls, prefix: str | None = None) -> "EmailNotificationChannel":
        settings = EmailSettings.from_env(prefix)
        return cls(settings)

    def _build_subject(self, run: RunContext) -> str:
        routes = ", ".join(run.routes)
        subject = EMAIL_SUBJECT_TEMPLATE.format(routes=routes, departure_date=run.departure_date)
        if self._settings.subject_prefix:
            return f"{self._settings.subject_prefix.strip()} {subject}"
        return subject

    def _build_body(self, run: RunContext, offers: Sequence[FlightOption]) -> str:
        summary = _summarize_offers(
            offers,
            limit=self._settings.top_offers_limit,
            line_template=self._settings.top_offers_template,
        )
        permutations = _summarize_permutations(offers)
        return EMAIL_BODY_TEMPLATE.format(
            environment=run.environment,
            routes=", ".join(run.routes),
            travel_classes=", ".join(run.travel_classes),
            adults=run.adults,
            children=run.children,
            departure_date=run.departure_date,
            return_date=run.return_date or "n/a",
            currency=run.currency,
            offers_summary=summary,
            permutation_summary=permutations,
            timestamp=_format_timestamp(run.timestamp),
            run_id=run.run_id,
        )

    def send(self, run: RunContext, offers: Sequence[FlightOption]) -> None:
        message = EmailMessage()
        message["From"] = self._settings.sender
        message["To"] = ", ".join(self._settings.recipients)
        message["Subject"] = self._build_subject(run)
        message.set_content(self._build_body(run, offers))

        try:
            with smtplib.SMTP(self._settings.host, self._settings.port, timeout=15) as client:
                if self._settings.use_tls:
                    client.starttls()
                if self._settings.username and self._settings.password:
                    client.login(self._settings.username, self._settings.password)
                client.send_message(message)
        except OSError as exc:
            raise NotificationError(f"Failed to send email notification: {exc}") from exc


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_ids: List[str]
    permutation_template: str | None = None
    top_offers_template: str | None = None
    top_offers_limit: int = 3
    parse_mode: str | None = None

    @classmethod
    def from_env(cls, prefix: str | None = None) -> "TelegramSettings":
        env_prefix = (prefix or "TELEGRAM").upper()
        token = os.getenv(f"{env_prefix}_BOT_TOKEN")
        chats_raw = os.getenv(f"{env_prefix}_CHAT_IDS") or os.getenv(f"{env_prefix}_CHAT_ID", "")
        permutation_template_raw = os.getenv(f"{env_prefix}_PERMUTATION_TEMPLATE")
        top_offers_template_raw = os.getenv(f"{env_prefix}_TOP_OFFERS_TEMPLATE", "")
        top_offers_limit_raw = os.getenv(f"{env_prefix}_TOP_OFFERS_LIMIT", "").strip()
        parse_mode_raw = os.getenv(f"{env_prefix}_PARSE_MODE", "")
        if not token:
            raise NotificationError(f"{env_prefix}_BOT_TOKEN must be configured for telegram notifications")
        chat_ids = [chat.strip() for chat in chats_raw.split(",") if chat.strip()]
        if not chat_ids:
            raise NotificationError(f"{env_prefix}_CHAT_IDS must list at least one chat identifier")
        permutation_template = permutation_template_raw if (permutation_template_raw or "").strip() else None
        top_offers_template = top_offers_template_raw.strip() or None
        top_offers_limit = 3
        if top_offers_limit_raw:
            try:
                top_offers_limit = int(top_offers_limit_raw)
            except ValueError as exc:
                raise NotificationError(f"{env_prefix}_TOP_OFFERS_LIMIT must be an integer") from exc
            if top_offers_limit <= 0:
                raise NotificationError(f"{env_prefix}_TOP_OFFERS_LIMIT must be greater than zero")
        parse_mode = parse_mode_raw.strip() or None
        return cls(
            bot_token=token,
            chat_ids=chat_ids,
            permutation_template=permutation_template,
            top_offers_template=top_offers_template,
            top_offers_limit=top_offers_limit,
            parse_mode=parse_mode,
        )


class TelegramNotificationChannel(NotificationChannel):
    name = "telegram"

    def __init__(self, settings: TelegramSettings) -> None:
        self._settings = settings

    @classmethod
    def from_env(cls, prefix: str | None = None) -> "TelegramNotificationChannel":
        settings = TelegramSettings.from_env(prefix)
        return cls(settings)

    def _build_message(self, run: RunContext, offers: Sequence[FlightOption]) -> str:
        summary = _summarize_offers(
            offers,
            limit=self._settings.top_offers_limit,
            line_template=self._settings.top_offers_template,
        )
        permutations = _summarize_permutations(offers, self._settings.permutation_template)
        return TELEGRAM_MESSAGE_TEMPLATE.format(
            routes=", ".join(run.routes),
            travel_classes=", ".join(run.travel_classes),
            departure_date=run.departure_date,
            return_date=run.return_date or "n/a",
            offers_summary=summary,
            permutation_summary=permutations,
        )

    def send(self, run: RunContext, offers: Sequence[FlightOption]) -> None:
        message = self._build_message(run, offers)
        url = f"https://api.telegram.org/bot{self._settings.bot_token}/sendMessage"
        errors: List[str] = []
        for chat_id in self._settings.chat_ids:
            payload = {"chat_id": chat_id, "text": message}
            if self._settings.parse_mode:
                payload["parse_mode"] = self._settings.parse_mode
            try:
                response = requests.post(
                    url,
                    timeout=10,
                    json=payload,
                )
            except requests.RequestException as exc:  # pragma: no cover - network failures
                errors.append(f"{chat_id}: {exc}")
                continue

            if response.status_code >= 400:
                errors.append(f"{chat_id}: {response.status_code} {response.text}")

        if errors:
            raise NotificationError("; ".join(errors))
