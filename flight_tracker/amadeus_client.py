"""Thin wrapper around the Amadeus SDK for fetching flight offers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from amadeus import Client, ResponseError

from .config import Route


@dataclass(frozen=True)
class FlightSegment:
    """Describes a single flight leg within an itinerary."""

    carrier_code: str
    number: str
    flight_number: str
    aircraft_code: str | None
    aircraft_name: str | None
    departure_airport: str
    arrival_airport: str
    departure_time: str
    arrival_time: str


@dataclass(frozen=True)
class FlightOption:
    route: Route
    travel_class: str
    price: float
    currency: str
    departure: str
    arrival: str
    duration: str | None
    stops: int
    segments: List[FlightSegment]


class AmadeusFlightClient:
    """Encapsulates queries to the Amadeus Flight Offers Search API."""

    def __init__(self, client_id: str, client_secret: str, environment: str) -> None:
        self._client = Client(
            client_id=client_id,
            client_secret=client_secret,
            hostname=environment,
        )

    def fetch_offers(
        self,
        *,
        route: Route,
        travel_class: str,
        departure_date: str,
        return_date: str | None,
        adults: int,
        children: int,
        currency: str,
        max_results: int = 50,
    ) -> List[FlightOption]:
        params = {
            "originLocationCode": route.origin,
            "destinationLocationCode": route.destination,
            "departureDate": departure_date,
            "travelClass": travel_class.upper(),
            "adults": adults,
            "max": max_results,
            "currencyCode": currency,
        }

        if children:
            params["children"] = children
        if return_date:
            params["returnDate"] = return_date

        try:
            response = self._client.shopping.flight_offers_search.get(**params)
        except ResponseError as exc:  # pragma: no cover - network exception path
            raise RuntimeError(f"Amadeus API error: {exc}") from exc

        offers = []
        for offer in response.data:
            price = offer.get("price", {})
            itineraries = offer.get("itineraries", [])
            raw_segments = itineraries[0]["segments"] if itineraries else []
            segments: List[FlightSegment] = []
            for seg in raw_segments:
                carrier = seg.get("carrierCode", "?")
                number = seg.get("number", "?")
                flight_number = f"{carrier}{number}" if carrier and number else carrier or number
                aircraft_info = seg.get("aircraft", {})
                aircraft_code = aircraft_info.get("code")
                aircraft_name = aircraft_info.get("name")
                departure_info = seg.get("departure", {})
                arrival_info = seg.get("arrival", {})
                segments.append(
                    FlightSegment(
                        carrier_code=carrier,
                        number=number,
                        flight_number=flight_number,
                        aircraft_code=aircraft_code,
                        aircraft_name=aircraft_name,
                        departure_airport=departure_info.get("iataCode", ""),
                        arrival_airport=arrival_info.get("iataCode", ""),
                        departure_time=departure_info.get("at", "?"),
                        arrival_time=arrival_info.get("at", "?"),
                    )
                )

            departure = segments[0].departure_time if segments else "?"
            arrival = segments[-1].arrival_time if segments else "?"
            stops = max(len(segments) - 1, 0)

            offers.append(
                FlightOption(
                    route=route,
                    travel_class=travel_class,
                    price=float(price.get("grandTotal", price.get("total", 0.0))),
                    currency=price.get("currency", "USD"),
                    departure=departure,
                    arrival=arrival,
                    duration=itineraries[0].get("duration") if itineraries else None,
                    stops=stops,
                    segments=segments,
                )
            )

        return offers
