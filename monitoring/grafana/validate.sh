#!/usr/bin/env bash
# Validate Icarus Grafana dashboard JSON.
#
# Grafana JSON is config — no runtime tests. This script enforces the
# bare-minimum shape every dashboard must carry (uid + title + panels +
# the "icarus"/"v2" tags) so a malformed file fails the gate before it
# reaches a running Grafana that would silently skip it.
#
# Wired into harness/verify.sh under "── Grafana dashboards ──".
set -uo pipefail

cd "$(dirname "$0")"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not installed — install via 'brew install jq' or apt-get install jq" >&2
  exit 2
fi

FAIL=0
COUNT=0

for dash in dashboards/*.json; do
  [ -f "$dash" ] || continue
  COUNT=$((COUNT + 1))

  if ! jq empty "$dash" >/dev/null 2>&1; then
    echo "FAIL  $dash  (not valid JSON)"
    FAIL=$((FAIL + 1))
    continue
  fi

  uid=$(jq -r '.uid // empty' "$dash")
  title=$(jq -r '.title // empty' "$dash")
  panels=$(jq -r '.panels | length // 0' "$dash")
  has_icarus=$(jq -r '.tags | index("icarus") // empty' "$dash")
  has_v2=$(jq -r '.tags | index("v2") // empty' "$dash")

  if [ -z "$uid" ]; then
    echo "FAIL  $dash  (missing .uid)"
    FAIL=$((FAIL + 1))
    continue
  fi
  if [ -z "$title" ]; then
    echo "FAIL  $dash  (missing .title)"
    FAIL=$((FAIL + 1))
    continue
  fi
  if [ "$panels" = "0" ] || [ "$panels" = "null" ]; then
    echo "FAIL  $dash  (no panels)"
    FAIL=$((FAIL + 1))
    continue
  fi
  if [ -z "$has_icarus" ] || [ -z "$has_v2" ]; then
    echo "FAIL  $dash  (tags must include 'icarus' and 'v2')"
    FAIL=$((FAIL + 1))
    continue
  fi

  echo "OK    $dash  uid=$uid  panels=$panels  title=\"$title\""
done

# Provisioning sanity: YAMLs must be present.
for p in provisioning/datasources/postgres.yaml provisioning/dashboards/dashboard-provider.yaml; do
  if [ ! -f "$p" ]; then
    echo "FAIL  $p  (missing provisioning file)"
    FAIL=$((FAIL + 1))
  else
    echo "OK    $p"
  fi
done

echo
echo "$COUNT dashboards checked, $FAIL failed"

[ "$FAIL" -eq 0 ] || exit 1
exit 0
