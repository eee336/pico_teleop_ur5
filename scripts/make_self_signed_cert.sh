#!/usr/bin/env bash
set -euo pipefail

HOSTNAME_OR_IP="${1:-localhost}"
OUT_DIR="${2:-certs}"

mkdir -p "$OUT_DIR"

if [[ "$HOSTNAME_OR_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  SAN="DNS:localhost,IP:127.0.0.1,IP:$HOSTNAME_OR_IP"
else
  SAN="DNS:localhost,DNS:$HOSTNAME_OR_IP,IP:127.0.0.1"
fi

openssl req -x509 -newkey rsa:2048 -sha256 -days 365 -nodes \
  -keyout "$OUT_DIR/quest-teleop.key" \
  -out "$OUT_DIR/quest-teleop.crt" \
  -subj "/CN=$HOSTNAME_OR_IP" \
  -addext "subjectAltName=$SAN"

echo "Wrote $OUT_DIR/quest-teleop.crt and $OUT_DIR/quest-teleop.key"
