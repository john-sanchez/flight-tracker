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
   - `DATA_DIR` controls where run artifacts are written (default `.data`).
   - `STORAGE_BACKENDS` configures one or more persistence targets (defaults to
     `json`). Each backend entry is comma separated and can optionally provide a
     custom path such as `json:/tmp/flight-data`.

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
- `--data-dir /path/to/data` or `--storage-backends json` to override where the
  archived JSON payloads land.
- `--debug` to print the resolved config plus all Amadeus request parameters and
  response counts (useful when diagnosing 401s or other API errors).

Each offer is rendered as a block with the first flight number/aircraft on the
summary line and, when connections exist, indented layover details showing the
next flight numbers, aircraft types, and local departure/arrival times. After a
successful run the tool also writes the raw JSON payload (routes + offers) under
`.data/<YYYY-MM-DD>/flight-offers-<run_id>.json`, which makes it easy to store
daily snapshots or plug in future storage backends.

## Storage backends

Flight data can be persisted through pluggable backends. The default backend is
`json`, which writes each run as a structured JSON file beneath `.data`. You can
configure additional backends via the `STORAGE_BACKENDS` environment variable or
the `--storage-backends` flag. Example:

```
STORAGE_BACKENDS="json:/var/flight-data"
```

### Implementing your own backend

Custom persistence targets just need to implement the `StorageBackend` protocol
defined in `flight_tracker/storage/__init__.py`:

```python
from flight_tracker.storage import RunContext, StorageBackend

class MyBackend(StorageBackend):
    name = "mybackend"

    def __init__(self, config_value: str | None = None) -> None:
        ...

    def persist(self, run: RunContext, offers: list[FlightOption]) -> Path:
        # write offers + metadata wherever you like
        return Path("/path/to/artifact")
```

After registering the backend (e.g., by adding it to `build_storage_backends` or
exposing it via an entry point in your package), users can enable it with
`STORAGE_BACKENDS=mybackend`. Backends should raise `StorageError` on failures so
the CLI can surface clear diagnostics without aborting the entire run.

All arguments fall back to the `.env` values, so only the credentials and base
search criteria need to be specified once. Any Amadeus API errors are surfaced
per route so you can quickly identify configuration issues.
