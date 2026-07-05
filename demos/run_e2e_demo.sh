#!/usr/bin/env bash
# Scripted end-to-end demo: upload -> process -> poll -> retrieval query
# -> grounded answer.
# Run against a live `docker compose up --wait` stack. Intended to be the
# fixed sequence recorded later (e.g. `asciinema rec -c demos/run_e2e_demo.sh
# demo.cast`) -- see README's "End-to-end demo flow" section.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-local-dev-key}"
SAMPLE_NOTE="$(dirname "$0")/sample_note.txt"

echo "=== 1. Upload ==="
DOC_ID=$(curl -s -X POST "$BASE_URL/documents" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@${SAMPLE_NOTE};type=text/plain" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "document_id: $DOC_ID"

echo "=== 2. Confirm registry entry ==="
curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/documents/$DOC_ID" | python3 -m json.tool

echo "=== 3. Enqueue for extraction + validation ==="
curl -s -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/documents/$DOC_ID/process" | python3 -m json.tool

echo "=== 4. Poll for result ==="
RESULT=""
for i in $(seq 1 30); do
  RESULT=$(curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/documents/$DOC_ID/result")
  STATUS=$(echo "$RESULT" | python3 -c "import sys,json;print(json.load(sys.stdin)['document']['status'])")
  echo "poll $i: status=$STATUS"
  if [ "$STATUS" != "uploaded" ] && [ "$STATUS" != "processing" ]; then
    break
  fi
  sleep 1
done
echo "$RESULT" | python3 -m json.tool

echo "=== 5. Retrieval query ==="
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "hypertension follow-up", "top_k": 5}' \
  "$BASE_URL/retrieval/query" | python3 -m json.tool

echo "=== 6. Grounded answer (ADR-0038) ==="
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"question": "What is the patient being treated for?"}' \
  "$BASE_URL/retrieval/answer" | python3 -m json.tool
