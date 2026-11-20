#!/usr/bin/env bash
# Load generator for the AWS lab: POST /api/v1/purchases N times and summarize HTTP codes,
# latency and achieved rate. Usage and examples: aws/CARGA.md
#   -n N          requests (default 100)
#   -c C          parallel workers (default 1 = sequential)
#   -i MS         pause per worker after each request, ms (default 0)
#   -m MODE       distinct | same  (same = one externalReference repeated: idempotency test)
#   -f FAILURE    X-Lab-Failure header, e.g. cm:transient:2 (default none)
#   -u URL        base URL (default: CloudFront SiteUrl from stack micro-test-frontend)
set -euo pipefail
N=100 C=1 I=0 MODE=distinct FAILURE="" URL=""
while getopts "n:c:i:m:f:u:" o; do
  case $o in n) N=$OPTARG;; c) C=$OPTARG;; i) I=$OPTARG;; m) MODE=$OPTARG;; f) FAILURE=$OPTARG;; u) URL=$OPTARG;;
    *) sed -n '2,10p' "$0"; exit 2;; esac
done
[[ $MODE == distinct || $MODE == same ]] || { echo "-m distinct|same" >&2; exit 2; }
URL=${URL:-$(aws cloudformation describe-stacks --region us-east-1 --stack-name micro-test-frontend \
  --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text)}
[[ $URL == http* ]] || { echo "No URL (stack micro-test-frontend output SiteUrl)" >&2; exit 1; }

RUN=$(date +%H%M%S)
OUT=$(mktemp)
export URL RUN MODE FAILURE I
echo "POST $URL/api/v1/purchases  n=$N c=$C i=${I}ms mode=$MODE failure=${FAILURE:-none} run=$RUN"

one() {
  local ref="LOAD-$RUN-$1"; [[ $MODE == same ]] && ref="LOAD-$RUN-same"
  local hdr=(); [[ -n $FAILURE ]] && hdr=(-H "X-Lab-Failure: $FAILURE")
  local res; res=$(curl -s -m 30 -w $'\n%{http_code} %{time_total}' -X POST "$URL/api/v1/purchases" \
    -H 'Content-Type: application/json' ${hdr[@]+"${hdr[@]}"} \
    -d "{\"externalReference\":\"$ref\",\"customerName\":\"Load $1\",\"customerEmail\":\"load$1@example.com\",\"productCode\":\"AUTO-01\"}" || true)
  local sale; sale=$(sed -n 's/.*"saleId" *: *"\([^"]*\)".*/\1/p' <<<"$res" | head -1)
  echo "$(tail -1 <<<"$res") ${sale:--}"
  [[ $I -gt 0 ]] && sleep "$(awk "BEGIN{print $I/1000}")"
  return 0
}
export -f one

now() { python3 -c "import time; print(time.time())"; }
START=$(now)
seq 1 "$N" | xargs -P "$C" -I{} bash -c 'one {}' | tee "$OUT" | awk '{printf "%s ", $1} NR%20==0{print ""}'
ELAPSED=$(awk "BEGIN{print $(now) - $START}")
echo
echo "== Códigos HTTP"; awk '{print $1}' "$OUT" | sort | uniq -c
awk '{print $2}' "$OUT" | sort -n | awk -v e="$ELAPSED" -v n="$N" \
  '{a[NR]=$1; s+=$1} END{printf "== Latencia s: avg %.3f  p50 %.3f  p95 %.3f  max %.3f\n== %d req en %.1fs = %.1f req/s\n",
   s/NR, a[int(NR*.5)>0?int(NR*.5):1], a[int(NR*.95)>0?int(NR*.95):1], a[NR], n, e, n/e}'
echo "== saleIds distintos: $(awk '$3!="-"{print $3}' "$OUT" | sort -u | wc -l | tr -d ' ')  (refs LOAD-$RUN-*)"
echo "== Detalle por request: $OUT"
