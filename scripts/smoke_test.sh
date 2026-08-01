#!/usr/bin/env bash
#
# Smoke test for a deployed docparser instance.
#
# Usage:
#   ./scripts/smoke_test.sh <BASE_URL> [path/to/circular.pdf]
#
# Examples:
#   ./scripts/smoke_test.sh https://docparser-xxxx.onrender.com
#   ./scripts/smoke_test.sh https://docparser-xxxx.onrender.com ./sample_circular.pdf
#
# Runs free checks (health, reference data, security posture, validation paths) always.
# The real end-to-end check (upload -> classify -> query) only runs if a PDF path is
# given, since it spends one real LLM API call - see README "Testing the deployment".

set -uo pipefail

BASE_URL="${1:-}"
PDF_PATH="${2:-}"

if [ -z "$BASE_URL" ]; then
    echo "Usage: $0 <BASE_URL> [path/to/circular.pdf]"
    exit 1
fi
BASE_URL="${BASE_URL%/}"  # strip trailing slash if present

PASS=0
FAIL=0

check() {
    local description="$1"
    local expected_code="$2"
    local actual_code="$3"
    if [ "$actual_code" = "$expected_code" ]; then
        echo "  PASS  $description (got $actual_code)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $description (expected $expected_code, got $actual_code)"
        FAIL=$((FAIL + 1))
    fi
}

echo "== docparser smoke test =="
echo "Target: $BASE_URL"
echo

echo "-- 1. Liveness (first hit may take 30-50s on Render's free tier if the instance was asleep) --"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 60 "$BASE_URL/health")
check "GET /health" "200" "$code"
echo

echo "-- 2. Interactive docs reachable --"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE_URL/docs")
check "GET /docs" "200" "$code"
echo

echo "-- 3. Reference endpoints (no LLM call) --"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE_URL/reference/segments")
check "GET /reference/segments" "200" "$code"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE_URL/reference/impact-areas")
check "GET /reference/impact-areas" "200" "$code"
echo

echo "-- 4. Repo-suggestion is disabled on this instance (should be 403) --"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE_URL/repo-index/status")
check "GET /repo-index/status" "403" "$code"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 -X POST "$BASE_URL/repo-index/build?target=backend")
check "POST /repo-index/build" "403" "$code"
echo

echo "-- 5. Validation paths (fail before touching the LLM) --"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 -F "file=@$0;type=text/plain" "$BASE_URL/documents")
check "POST /documents (wrong content-type) -> 415" "415" "$code"

TMP_EMPTY=$(mktemp)
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 -F "file=@$TMP_EMPTY;type=application/pdf" "$BASE_URL/documents")
check "POST /documents (empty file) -> 400" "400" "$code"
rm -f "$TMP_EMPTY"
echo

if [ -z "$PDF_PATH" ]; then
    echo "-- 6. End-to-end classification: SKIPPED (no PDF path given as 2nd argument) --"
    echo "     Run again with a real circular PDF to exercise the actual LLM pipeline:"
    echo "       $0 $BASE_URL ./your_circular.pdf"
else
    echo "-- 6. End-to-end classification (spends one real LLM API call) --"
    if [ ! -f "$PDF_PATH" ]; then
        echo "  FAIL  PDF not found at $PDF_PATH"
        FAIL=$((FAIL + 1))
    else
        UPLOAD_RESPONSE=$(curl -s --max-time 30 -F "file=@$PDF_PATH;type=application/pdf" "$BASE_URL/documents")
        DOC_ID=$(echo "$UPLOAD_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

        if [ -z "$DOC_ID" ]; then
            echo "  FAIL  Upload did not return a document id. Response:"
            echo "        $UPLOAD_RESPONSE"
            FAIL=$((FAIL + 1))
        else
            echo "  Uploaded -> document id: $DOC_ID"
            echo "  Polling for classification to finish (up to 60s)..."

            STATUS="uploaded"
            for i in $(seq 1 20); do
                sleep 3
                DETAIL=$(curl -s --max-time 20 "$BASE_URL/documents/$DOC_ID")
                STATUS=$(echo "$DETAIL" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)
                echo "    [$i] status: $STATUS"
                if [ "$STATUS" = "complete" ] || [ "$STATUS" = "needs_review" ] || [ "$STATUS" = "failed" ]; then
                    break
                fi
            done

            case "$STATUS" in
                complete|needs_review)
                    echo "  PASS  Classification finished with status: $STATUS"
                    PASS=$((PASS + 1))
                    ;;
                failed)
                    echo "  FAIL  Classification failed. Full response:"
                    echo "        $DETAIL"
                    FAIL=$((FAIL + 1))
                    ;;
                *)
                    echo "  FAIL  Still '$STATUS' after 60s - check Render logs."
                    FAIL=$((FAIL + 1))
                    ;;
            esac

            if [ "$STATUS" = "needs_review" ]; then
                echo "  Review queue for this document:"
                curl -s --max-time 20 "$BASE_URL/review/queue" | head -c 500
                echo
            fi

            echo "  Querying it back:"
            code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE_URL/query/documents")
            check "GET /query/documents" "200" "$code"
        fi
    fi
fi

echo
echo "== Summary: $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
