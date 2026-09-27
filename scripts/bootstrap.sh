#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
ref=bef8680d5f9714f3e040e6f9cbc88d7888439b6d
mkdir -p "$root/vendor"
if [ ! -d "$root/vendor/srsRAN_4G/.git" ]; then
  git clone https://github.com/srsran/srsRAN_4G.git "$root/vendor/srsRAN_4G"
  git -C "$root/vendor/srsRAN_4G" checkout "$ref"
  git -C "$root/vendor/srsRAN_4G" apply "$root/patches/srsran-x2.patch"
fi
if [ "$(git -C "$root/vendor/srsRAN_4G" rev-parse HEAD)" != "$ref" ]; then
  echo 'srsRAN checkout differs from required source revision' >&2; exit 1
fi
if ! git -C "$root/vendor/srsRAN_4G" apply --reverse --check "$root/patches/srsran-x2.patch"; then
  echo 'Existing source checkout does not match the shipped fork patch; review it before replacing it' >&2
  exit 1
fi
python3 "$root/scripts/generate.py"
