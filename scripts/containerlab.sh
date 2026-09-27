#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
expected_hash=$(python3 - "$root/mobility.clab.yml" <<'PY'
import hashlib,pathlib,sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)
# Colima's shared mount can briefly expose an old file length after host edits.
# Parse only after the Linux-side bytes match the completed host file exactly.
exec docker run --rm --entrypoint sh --privileged --network host --pid host \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$root:$root" -v /var/run/netns:/var/run/netns \
  -w "$root" -e LAB_HANDOVER_MODE="${LAB_HANDOVER_MODE:-x2}" \
  ghcr.io/srl-labs/clab:0.79.0@sha256:4b836d22c04fc0e9315f99648b7399e4859b71208c63eea00af488086d95fe12 \
  -ec 'topology_path=$1; expected=$2; shift 2; attempt=0
       while [ "$attempt" -lt 50 ]; do
         actual=$(sha256sum "$topology_path" | cut -d " " -f 1)
         if [ "$actual" = "$expected" ]; then exec containerlab "$@"; fi
         attempt=$((attempt+1)); sleep .1
       done
       echo "Shared topology file did not converge to host content" >&2; exit 1' \
  -- "$root/mobility.clab.yml" "$expected_hash" "$@"
