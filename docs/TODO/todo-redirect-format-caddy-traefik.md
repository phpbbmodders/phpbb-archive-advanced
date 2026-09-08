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

## Verification status

Every specific Caddy/Traefik technical claim in the reference doc that
could be checked against the current official docs has been — see
citations below. The one claim docs alone couldn't settle (whether a
query parameter's *value* can actually be read back for use in a
redirect destination, not just matched) has now also been confirmed
against a real `caddy` binary (v2.11.4, downloaded straight from
`caddyserver/caddy`'s GitHub releases) — see "Confirmed by real instance
test" below. Traefik's `RedirectRegex` still hasn't been runtime-tested
the same way and should be before it's relied on.

**Confirmed accurate, verbatim or in substance, against current docs:**

- Caddy's named request matchers include `path`, `path_regexp`, `query`,
  `expression`, plus `header`, `method`, `host`, and others.
  ([Request matchers](https://caddyserver.com/docs/caddyfile/matchers))
- Caddy's `query` matcher: different keys are AND'ed, multiple values for
  the same key are OR'ed (e.g. `query sort=asc sort=desc` matches
  either) — confirmed exactly as the reference doc claimed. It does
  **not** support regex or capture groups; it's exact/wildcard match
  only. (Same page.)
- Caddy's `path_regexp` matcher *does* expose capture groups as
  placeholders: `{re.<name>.<group>}` (named) or `{re.<group>}`
  (unnamed, `0` = full match). No such capture mechanism exists for
  `query` — confirmed by its absence from the matchers doc. This matters
  directly: matching the `t=`/`p=` values via `query` alone gives no way
  to read *back* the matched value for use in a redirect destination.
- Caddy's `redir` directive: `redir [<matcher>] <to> [<code>]`,
  `permanent` = HTTP 301, confirmed exactly as claimed.
  ([redir directive](https://caddyserver.com/docs/caddyfile/directives/redir))
- Caddy's `handle` blocks: sibling `handle`s are mutually exclusive
  (only the first matching one runs), and a matcher-less `handle` acts
  as a fallback — confirmed exactly as claimed.
  ([handle directive](https://caddyserver.com/docs/caddyfile/directives/handle))
- Caddy directives do **not** run in the textual order they're written —
  Caddy sorts them by a hard-coded default order — and `route` is the
  documented way to force literal ordering instead, confirmed exactly as
  claimed. ([Directives](https://caddyserver.com/docs/caddyfile/directives))
- Traefik's HTTP routers support `Query("key", "value")` and
  `QueryRegexp("key", "regex")` rule matchers (also `Path`/`PathRegexp`),
  confirmed. Router-level matching and the RedirectRegex middleware's own
  regex/capture are two separate stages, as the reference doc's
  "router rule → redirect middleware" model implies — RedirectRegex does
  its own independent regex match against the request URL, it doesn't
  reuse whatever the router's `Rule` matched on.
  ([Rules and priority](https://raw.githubusercontent.com/traefik/traefik/master/docs/content/reference/routing-configuration/http/routing/rules-and-priority.md))
- Traefik router priority: sorted by rule length by default (longest =
  highest priority) unless an explicit `priority` is set (`0` is
  ignored; negative values allowed) — confirmed exactly as claimed.
  ([HTTP routers reference](https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/router/))
- Traefik's `RedirectRegex` middleware: `regex`/`replacement`/`permanent`
  fields, `${1}` (not `$1x`) for replacement captures, YAML needs
  backslashes doubled, Docker labels need dollar signs doubled — all
  confirmed exactly as claimed, including the exact wording ("`$1x` is
  equivalent to `${1x}`, not `${1}x`").
  ([RedirectRegex reference](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/redirectregex/))

**Confirmed by real instance test (`caddy` v2.11.4, official GitHub release binary):**

- `{http.request.uri.query.<name>}` is real and works exactly as hoped:
  it expands to the actual query parameter's value inside a `redir`
  destination. `redir /board/viewtopic.php /topics/{http.request.uri.query.t}.html permanent`
  against a live Caddy instance: `?t=507` → `Location: /topics/507.html`.
  This was the single most load-bearing unverified claim for the whole
  Caddy renderer, and it holds up.
- The combined-`t`-and-`p` case works with a named matcher using
  `query`'s own AND semantics (already confirmed from docs) plus the
  value placeholder for the destination — no CEL/`expression` needed:
  ```
  @has_t_and_p {
      query t=* p=*
  }
  redir @has_t_and_p "/topics/{http.request.uri.query.t}.html?p={http.request.uri.query.p}" permanent
  ```
  Tested live: `?t=507&p=5756` and the reversed `?p=5756&t=507` both
  produce the identical correct destination (query matching is
  inherently order-independent, as the docs claimed); an extra unrelated
  param (`?sid=abc&t=507&p=5756`) doesn't break the match either.
- **A real Caddy-side counterpart to the nginx `p`-without-`t` bug was
  found live, the same way the nginx one was**: a generic fallback rule
  keyed only on the *path* (`redir /board/viewtopic.php /topics/{http.request.uri.query.t}.html permanent`,
  no `query` guard) builds `/topics/.html` when `t` is absent — tested
  live with `?p=5756` (no `t` at all): `Location: /topics/.html`, the
  exact same empty-path-segment defect. **Any Caddy renderer must gate
  the generic topic-only fallback on `query t=*` too** (or equivalent),
  not just match on path — this is a required design constraint now,
  not a hypothetical.
- Numeric-vs-merely-present validation (matching Apache/nginx's
  `^[0-9]+$` requirement) was not yet tested — `query t=*` only checks
  *presence*, same gap the reference doc flagged for the generic
  renderer-neutral model. Needs a `expression`/CEL check or equivalent
  before a Caddy renderer matches Apache/nginx's actual guarantee.

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
