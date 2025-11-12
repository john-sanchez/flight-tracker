# flight-tracker

Small Python CLI that uses the Amadeus Flight Offers Search API to gather fares for
multiple MNL-based routes and output them with the cheapest options first.

## Setup

1. Create a virtual environment and install the dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Copy the `.env.example` file and fill in your Amadeus credentials plus the
   default search criteria that should be shared across all routes:

   ```bash
   cp .env.example .env
   # edit the new .env file
   ```

   Key fields:

   - `ROUTES` accepts multiple comma separated `ORIGIN-DESTINATION` airport
     codes. Example: `MNL-TYO,MNL-KIX`.
   - `ADULTS`/`CHILDREN` define the shared passenger counts (per Amadeus API
     conventions).
   - `TRAVEL_CLASSES` is either `economy`, `business`, a comma separated mix of
     both, or `all` to search each class.
   - `AMADEUS_ENV` toggles between the sandbox (`test`) and live (`production`)
     API hosts.
   - `CURRENCY` requests prices in any ISO 4217 currency supported by the
     Amadeus API (e.g. `PHP`, `USD`).

## Usage

Run the CLI via the module entry point. The tool loads configuration from the
`.env` file (or from env vars) and queries every requested route/class
combination, sorting the combined results in ascending order by total price.

```bash
python -m flight_tracker.cli --env-file .env
```

Useful overrides:

- `--routes MNL-TYO MNL-KIX` to provide ad-hoc routes without touching `.env`.
- `--travel-classes economy,business` (or `--travel-classes all`).
- `--departure-date 2024-08-01` and `--return-date 2024-08-12` to adjust the
  travel window.
- `--amadeus-env test` to explicitly hit the sandbox regardless of `.env`.
- `--currency PHP` to request results priced in another currency.
- `--debug` to print the resolved config plus all Amadeus request parameters and
  response counts (useful when diagnosing 401s or other API errors).

Each offer is rendered as a block with the first flight number/aircraft on the
summary line and, when connections exist, indented layover details showing the
next flight numbers, aircraft types, and local departure/arrival times.

All arguments fall back to the `.env` values, so only the credentials and base
search criteria need to be specified once. Any Amadeus API errors are surfaced
per route so you can quickly identify configuration issues.
