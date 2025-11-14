"""Pluggable notification channels for sharing flight offer summaries."""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
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


def _summarize_offers(offers: Sequence[FlightOption], limit: int = 5) -> str:
    top = list(offers[:limit])
    if not top:
        return "No offers were returned."

    lines = []
    for idx, offer in enumerate(top, 1):
        lines.append(
            (
                f"{idx}. {offer.currency} {offer.price:,.2f} | {offer.travel_class.title():8s} | "
                f"{offer.route.origin}->{offer.route.destination} | dep {_format_datetime(offer.departure)}"
            )
        )
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


def _summarize_permutations(offers: Sequence[FlightOption]) -> str:
    if not offers:
        return "No offers were returned."

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

    for key, offer in items:
        route = f"{offer.route.origin}->{offer.route.destination}"
        stop_label = _format_stop_label(offer.stops)
        flight_number = _lead_flight_number(offer)
        lines.append(
            f"- {route} | {offer.travel_class} | {stop_label} | flight {flight_number}"
            f" -> {offer.currency} {offer.price:,.2f}"
        )
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

        return cls(
            host=host,
            port=port,
            sender=sender,
            recipients=recipients,
            username=username,
            password=password,
            use_tls=use_tls,
            subject_prefix=subject_prefix,
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
        summary = _summarize_offers(offers)
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

    @classmethod
    def from_env(cls, prefix: str | None = None) -> "TelegramSettings":
        env_prefix = (prefix or "TELEGRAM").upper()
        token = os.getenv(f"{env_prefix}_BOT_TOKEN")
        chats_raw = os.getenv(f"{env_prefix}_CHAT_IDS") or os.getenv(f"{env_prefix}_CHAT_ID", "")
        if not token:
            raise NotificationError(f"{env_prefix}_BOT_TOKEN must be configured for telegram notifications")
        chat_ids = [chat.strip() for chat in chats_raw.split(",") if chat.strip()]
        if not chat_ids:
            raise NotificationError(f"{env_prefix}_CHAT_IDS must list at least one chat identifier")
        return cls(bot_token=token, chat_ids=chat_ids)


class TelegramNotificationChannel(NotificationChannel):
    name = "telegram"

    def __init__(self, settings: TelegramSettings) -> None:
        self._settings = settings

    @classmethod
    def from_env(cls, prefix: str | None = None) -> "TelegramNotificationChannel":
        settings = TelegramSettings.from_env(prefix)
        return cls(settings)

    def _build_message(self, run: RunContext, offers: Sequence[FlightOption]) -> str:
        summary = _summarize_offers(offers, limit=3)
        permutations = _summarize_permutations(offers)
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
            try:
                response = requests.post(
                    url,
                    timeout=10,
                    json={"chat_id": chat_id, "text": message},
                )
            except requests.RequestException as exc:  # pragma: no cover - network failures
                errors.append(f"{chat_id}: {exc}")
                continue

            if response.status_code >= 400:
                errors.append(f"{chat_id}: {response.status_code} {response.text}")

        if errors:
            raise NotificationError("; ".join(errors))
