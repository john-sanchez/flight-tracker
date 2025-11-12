#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file '$ENV_FILE' was not found" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

: "${AMADEUS_CLIENT_ID:?AMADEUS_CLIENT_ID is required}"
: "${AMADEUS_CLIENT_SECRET:?AMADEUS_CLIENT_SECRET is required}"
: "${AMADEUS_ENV:?AMADEUS_ENV is required}"
: "${CURRENCY:?CURRENCY is required}"
: "${ROUTES:?ROUTES is required}"
: "${DEPARTURE_DATE:?DEPARTURE_DATE is required}"
: "${ADULTS:?ADULTS is required}"
: "${CHILDREN:?CHILDREN is required}"
: "${TRAVEL_CLASSES:?TRAVEL_CLASSES is required}"

BASE_URL="https://api.amadeus.com"
if [[ "${AMADEUS_ENV}" == "test" ]]; then
  BASE_URL="https://test.api.amadeus.com"
fi

echo "Using env: $AMADEUS_ENV ($BASE_URL)" >&2

TOKEN_RESPONSE=$(curl -sS -X POST "$BASE_URL/v1/security/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=${AMADEUS_CLIENT_ID}" \
  -d "client_secret=${AMADEUS_CLIENT_SECRET}")

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for this script. Please install jq and retry." >&2
  exit 1
fi

if ! ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token'); then
  echo "Failed to parse access token from response:" >&2
  echo "$TOKEN_RESPONSE" >&2
  exit 1
fi

if [[ "$ACCESS_TOKEN" == "null" || -z "$ACCESS_TOKEN" ]]; then
  echo "Access token missing in response:" >&2
  echo "$TOKEN_RESPONSE" >&2
  exit 1
fi

echo "Obtained access token" >&2

echo "TOKEN RESPONSE: " $TOKEN_RESPONSE
echo "ACCESS_TOKEN: " $ACCESS_TOKEN

IFS=',' read -ra ROUTE_LIST <<< "$ROUTES"

for raw_route in "${ROUTE_LIST[@]}"; do
  route_clean=$(echo "$raw_route" | tr -d ' ')
  origin=${route_clean%%-*}
  destination=${route_clean##*-}

  for class in ${TRAVEL_CLASSES//,/ }; do
    echo "----" >&2
    echo "Requesting $origin->$destination class=$class" >&2
    response=$(curl -sS -vvv -w "\nHTTP_STATUS:%{http_code}\n" -X GET "$BASE_URL/v2/shopping/flight-offers" \
      -H "Authorization: Bearer $ACCESS_TOKEN" \
      -G --data-urlencode "originLocationCode=$origin" \
      --data-urlencode "destinationLocationCode=$destination" \
      --data-urlencode "departureDate=$DEPARTURE_DATE" \
      ${RETURN_DATE:+--data-urlencode "returnDate=$RETURN_DATE"} \
      --data-urlencode "travelClass=${class^^}" \
      --data-urlencode "adults=$ADULTS" \
      --data-urlencode "children=$CHILDREN" \
      --data-urlencode "currencyCode=$CURRENCY" \
      --data-urlencode "max=5")

    echo "$response"
  done
done
