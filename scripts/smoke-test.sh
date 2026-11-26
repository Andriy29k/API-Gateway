#!/usr/bin/env bash
# Smoke test for a running gateway.
#
# Usage:  scripts/smoke-test.sh [base_url]
#         GATEWAY_URL=http://localhost:9090 scripts/smoke-test.sh
#
# Needs only bash and curl. Exit code 0 = all checks passed, 1 = at least one failed.

set -u

BASE_URL="${1:-${GATEWAY_URL:-http://localhost:8080}}"
BASE_URL="${BASE_URL%/}"

# The gateway allows 5 requests/s per client (burst 10), shared by all routes.
# Pausing between checks keeps the test under that limit, so running it twice in a row still passes.
PACE="${SMOKE_PACE:-0.25}"

pass=0
fail=0

# check <name> <expected status> <expected body substring or ""> <curl args...>
check() {
    local name="$1" want_status="$2" want_body="$3"
    shift 3
    local out status body
    sleep "$PACE"
    out="$(curl -sS --max-time 10 -w $'\n%{http_code}' "$@" 2>&1)"
    status="${out##*$'\n'}"
    body="${out%$'\n'*}"
    if [ "$status" = "$want_status" ] && { [ -z "$want_body" ] || [[ "$body" == *"$want_body"* ]]; }; then
        echo "PASS  $name"
        pass=$((pass + 1))
    else
        echo "FAIL  $name (expected status $want_status${want_body:+ and body containing '$want_body'}, got status $status)"
        echo "      body: ${body:0:200}"
        fail=$((fail + 1))
    fi
}

# check_header_count <name> <header> <expected count> <url>
check_header_count() {
    local name="$1" header="$2" want="$3" url="$4" got
    sleep "$PACE"
    got="$(curl -sS --max-time 10 -D - -o /dev/null "$url" | tr -d '\r' | grep -ic "^${header}:")"
    if [ "$got" = "$want" ]; then
        echo "PASS  $name"
        pass=$((pass + 1))
    else
        echo "FAIL  $name (expected $want '$header' header(s), got $got)"
        fail=$((fail + 1))
    fi
}

# check_location <name> <expected status> <expected Location> <url>
check_location() {
    local name="$1" want_status="$2" want_loc="$3" url="$4" headers status loc
    sleep "$PACE"
    headers="$(curl -sS --max-time 10 -D - -o /dev/null "$url" | tr -d '\r')"
    status="$(printf '%s\n' "$headers" | head -1 | awk '{print $2}')"
    loc="$(printf '%s\n' "$headers" | grep -i '^location:' | head -1 | cut -d' ' -f2-)"
    if [ "$status" = "$want_status" ] && [ "$loc" = "$want_loc" ]; then
        echo "PASS  $name"
        pass=$((pass + 1))
    else
        echo "FAIL  $name (expected $want_status -> '$want_loc', got $status -> '$loc')"
        fail=$((fail + 1))
    fi
}

echo "Smoke test against $BASE_URL"
echo

check "gateway health"                    200 '"status":"ok"'          "$BASE_URL/health"
check "service-a hello"                   200 '"service":"service-a"'  "$BASE_URL/api/service-a/hello"
check "service-a query string forwarded"  200 '"name":"Ann"'           "$BASE_URL/api/service-a/hello?name=Ann"
check "service-b hello"                   200 '"service":"service-b"'  "$BASE_URL/api/service-b/hello"
check "service-b POST body forwarded"     200 '"received":{"k":"v"}'   -X POST -H 'Content-Type: application/json' -d '{"k":"v"}' "$BASE_URL/api/service-b/echo"
check "service-c list with limit"         200 '"count":2'              "$BASE_URL/api/service-c/items?limit=2"
check "service-c item"                    200 '"name":"monitor"'       "$BASE_URL/api/service-c/items/3"
check "upstream 404 is passed through"    404 '"error":"Not Found"'    "$BASE_URL/api/service-c/items/999"
check "upstream 400 is passed through"    400 '"status":400'           "$BASE_URL/api/service-c/items?limit=abc"
check "unknown path returns gateway 404"  404 '"error":"not_found"'    "$BASE_URL/nope"
check "old route names are not served"    404 '"error":"not_found"'    "$BASE_URL/api/notes"

check_location "missing slash redirects (keeps query)" 308 "/api/service-a/?x=1" "$BASE_URL/api/service-a?x=1"
check_header_count "exactly one X-Request-ID header"    "X-Request-ID" 1 "$BASE_URL/api/service-a/hello"

echo
echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ]
