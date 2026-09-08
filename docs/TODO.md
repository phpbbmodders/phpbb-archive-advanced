# TODO

Ideas not yet built, practical and speculative alike.

- **`--redirect-format {caddy,traefik}`**: extend the old-URL redirect
  generator (`render_redirects()` in `generator/generate.py`, currently
  `apache`/`nginx` only) to also support Caddy and Traefik. Deferred
  because their exact redirect-by-query-parameter syntax couldn't be
  confidently verified against current docs in the same session that
  added `apache`/`nginx` — Caddy's placeholder for a single named query
  parameter (e.g. the `t` in `?t=42`) and Traefik's regex-redirect
  middleware config both need real verification (docs research, and
  ideally testing against a real Caddy/Traefik instance) before
  generating something a user might deploy without noticing it's wrong.
  Whatever's added also needs the same `old_prefix`/absolute-`base_url`
  parameterization `apache`/`nginx` already have (see `_apache_redirects`/
  `_nginx_redirects`) — the old board's install path and a redirect
  rule running on a different host than the archive are both format-
  agnostic requirements, not something specific to apache/nginx. It also
  now needs the same per-page redirect blocks apache/nginx gained for
  topic pagination (`_paginated_redirect_blocks_apache`/`_nginx`,
  `multi_page_topics` from `render_topics()`) — without them, a deep link
  to a specific post beyond page 1 of a paginated topic would land on
  page 1 instead of its own actual page, the same real bug the apache/
  nginx fix addressed. Caddy/Traefik would need their own equivalent way
  to match an exact alternation of a page's real post ids (a numeric
  min/max range isn't safe with Apache's own lexicographic RewriteCond
  comparisons — verify whether Caddy/Traefik's own condition matching has
  the same limitation before assuming a range works there). Also now
  needs the equivalent forum-pagination redirect blocks
  (`_paginated_redirect_blocks_forums_apache`/`_nginx`, `multi_page_forums`
  from `render_forums()`) — a bookmarked `viewforum.php?f=<id>&start=<N>`
  beyond page 1 needs the same treatment. Simpler than the topic case: a
  forum page's `start` offset is an exact, deterministic multiple of
  `FORUM_PAGE_SIZE`, so a plain literal match per page is enough, no
  alternation needed. If nginx's own syntax is used as a reference,
  note that its `if` directive only accepts a bare variable as its
  left-hand operand — a compound expression like `$arg_f:$arg_start`
  inline never actually matches (a real, previously-shipped bug in this
  project's own nginx output, caught and fixed while adding forum
  pagination — see docs/CHANGES.md). Verify whether Caddy/Traefik's own
  config languages have an equivalent restriction before assuming a
  similar inline combination works there.
