# `--redirect-format {caddy,traefik}`

Extend the old-URL redirect generator (`render_redirects()` in
`generator/generate.py`, currently `apache`/`nginx` only) to also support
Caddy and Traefik.

Reference: [redirect-format-caddy-traefik-reference.md](redirect-format-caddy-traefik-reference.md)
— an external AI-drafted implementation spec, kept as research material.
Not verified claim-by-claim; treat every specific technical claim in it as
unverified until actually checked against real Caddy/Traefik docs and a
real instance of each, the same standard `apache`/`nginx` were held to.
One real bug it pointed at was independently verified and fixed already
(see `docs/CHANGES.md`, "Old-URL redirect fix: nginx `p`-without-`t`
malformed destination") — everything else in it is still just a proposal.

## Why deferred

Caddy's placeholder for a single named query parameter (e.g. the `t` in
`?t=42`) and Traefik's regex-redirect middleware config both need real
verification (docs research, and ideally testing against a real
Caddy/Traefik instance) before generating something a user might deploy
without noticing it's wrong — the same bar `apache`/`nginx` were held to,
including the live isolated-server testing done for both.

## Requirements whatever gets built must meet

- Same `old_prefix`/absolute-`base_url` parameterization `apache`/`nginx`
  already have (see `_apache_redirects`/`_nginx_redirects`) — the old
  board's install path and a redirect rule running on a different host
  than the archive are both format-agnostic requirements.
- Same per-page redirect blocks apache/nginx have for **topic**
  pagination (`_paginated_redirect_blocks_apache`/`_nginx`,
  `multi_page_topics` from `render_topics()`) — without them, a deep link
  to a specific post beyond page 1 of a paginated topic lands on page 1
  instead of its own actual page. Caddy/Traefik would need their own
  equivalent way to match an exact alternation of a page's real post ids
  — a numeric min/max range isn't safe with Apache's own lexicographic
  `RewriteCond` comparisons; verify whether Caddy/Traefik's own condition
  matching has the same limitation before assuming a range works there.
- Same per-page redirect blocks apache/nginx have for **forum**
  pagination (`_paginated_redirect_blocks_forums_apache`/`_nginx`,
  `multi_page_forums` from `render_forums()`) — a bookmarked
  `viewforum.php?f=<id>&start=<N>` beyond page 1 needs the same
  treatment. Simpler than the topic case: a forum page's `start` offset
  is an exact, deterministic multiple of `FORUM_PAGE_SIZE`, so a plain
  literal match per page is enough, no alternation needed.
- If nginx's own syntax is used as a reference, note that its `if`
  directive only accepts a bare variable as its left-hand operand — a
  compound expression like `$arg_f:$arg_start` inline never actually
  matches (a real, previously-shipped bug in this project's own nginx
  output, caught and fixed while adding forum pagination — see
  `docs/CHANGES.md`). Verify whether Caddy/Traefik's own config languages
  have an equivalent restriction before assuming a similar inline
  combination works there.
- The generic nginx `viewtopic.php` fallback's own `p`-without-`t` bug
  (fixed — see `docs/CHANGES.md`) is a useful worked example of the kind
  of malformed-destination case any new renderer needs to avoid: never
  build a destination path from an identifier that hasn't itself been
  validated as present and numeric.
