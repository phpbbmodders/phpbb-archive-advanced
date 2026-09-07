#!/usr/bin/bash
set -Eeuo pipefail

# Regenerates the static archive with every accumulated fix/feature from
# today's session already wired in: igorw's avatar override, the
# phpbbmodders.net userbar/rank-image and rmcgirr83.org smilie mirrors,
# recovery of corrupted attachments from the dump/qustionabl/ backup,
# skipping known-dead hosts (known-dead-hosts.json) without a network
# attempt, excluding the Team category (config/exclude.json) from the
# archive entirely, a stylesheet (config/phpbbmodders-style.css)
# approximating the live board's own color scheme, a board-wide notice
# (config/announcement.txt) — empty by default, so nothing renders
# until it's filled in — a sitemap.xml/robots.txt for
# https://phpbbmodders.net/, full-text search (search.html, via
# Pagefind), the board's real logo/favicon fetched live from
# phpbbmodders.com/.net (neither lives anywhere in the SQL dump), and
# recognizing this board's own domains (config/board_hosts.json — the
# board has used .net/.com/.org at different times) so a post's own
# viewtopic.php links back to itself get rewritten into the archive
# regardless of which domain it used when it was written.
# --incremental keeps previously-downloaded attachments/avatars/external
# images across runs instead of re-fetching everything.
#
# Usage:
#   ./run.sh                 Full run using the config/ files below.
#   ./run.sh --no-incremental
#                             Force a clean re-fetch of everything.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

extra_args=("--incremental")
if [[ "${1:-}" == "--no-incremental" ]]; then
    extra_args=()
fi

./generate.sh \
    --avatar-overrides config/avatar_overrides.json \
    --url-mirrors config/url_mirrors.json \
    --attachment-recovery dump/qustionabl/files \
    --ignore-hosts known-dead-hosts.json \
    --exclude config/exclude.json \
    --style-css config/phpbbmodders-style.css \
    --announcement config/announcement.txt \
    --sitemap-url https://phpbbmodders.net/ \
    --search \
    --logo-url https://www.phpbbmodders.com/modders-cog.gif \
    --favicon-url https://phpbbmodders.net/favicon.ico \
    --board-hosts config/board_hosts.json \
    "${extra_args[@]}"
