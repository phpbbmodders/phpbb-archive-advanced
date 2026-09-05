#!/usr/bin/bash
set -Eeuo pipefail

# Serves a generated archive's output/ over real HTTP, needed to actually
# test --search locally: Pagefind can't fetch its own index under a
# file:// path — the search box accepts a query and shows "Searching
# for..." but never returns results, with no error shown anywhere (see
# README.md's --search section). Any static file server works; this is
# just the one already on every machine with Python 3.
#
# Usage:
#   ./serve.sh              Serve ./output/ on http://localhost:8000
#   ./serve.sh 8080         Serve ./output/ on a different port

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
port="${1:-8000}"

cd "$script_dir/output"
python3 -m http.server "$port"
