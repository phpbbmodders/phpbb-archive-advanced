#!/usr/bin/bash
set -Eeuo pipefail

# Generates the static HTML archive from dump/ using phpbb-archive's own
# generator (generator/generate.py), creating and reusing a local .venv
# for its dependencies rather than installing them system-wide.
#
# Usage:
#   generate.sh
#       Set up .venv if needed, then generate output/ from dump/.
#
#   generate.sh --dump DIR --output DIR
#       Forwarded to `python -m generator.generate`; overrides the
#       default dump/ and output/ locations.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

venv_dir="$script_dir/.venv"
requirements_file="$script_dir/generator/requirements.txt"
dump_dir="$script_dir/dump"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: required command not found: python3" >&2
    exit 1
fi

if [[ ! -d "$dump_dir" ]]; then
    echo "ERROR: dump directory not found:" >&2
    echo "  $dump_dir" >&2
    echo "See README.md for what to collect into dump/." >&2
    exit 1
fi

if [[ -x "$venv_dir/bin/python" ]] && ! "$venv_dir/bin/pip" --version >/dev/null 2>&1; then
    # A venv's console-script wrappers (pip, etc.) hardcode an absolute
    # shebang path back to the venv itself at creation time, so copying or
    # moving the containing folder silently breaks them even though the
    # python binary/symlink keeps working. Recreate rather than fail.
    echo "Existing virtual environment is broken (likely moved/copied from elsewhere) — recreating: $venv_dir"
    rm -rf "$venv_dir"
fi

if [[ ! -x "$venv_dir/bin/python" ]]; then
    echo "Creating virtual environment: $venv_dir"
    python3 -m venv "$venv_dir"
fi

echo "Installing dependencies from generator/requirements.txt"
"$venv_dir/bin/pip" install --quiet --requirement "$requirements_file"

echo "Generating archive..."
"$venv_dir/bin/python" -m generator.generate \
    --dump dump/ \
    --output output/ \
    "$@"

echo
echo "Done. Open in a browser:"
echo "  $script_dir/output/index.html"
