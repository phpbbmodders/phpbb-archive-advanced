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

## Why deferred (historical — see "Verification status" below)

This was the original reason for deferral, kept as-written for context.
Every question it raises has since been verified — don't read this
section as still-open.

Caddy's placeholder for a single named query parameter (e.g. the `t` in
`?t=42`) and Traefik's regex-redirect middleware config both needed real
verification (docs research, and ideally testing against a real
Caddy/Traefik instance) before generating something a user might deploy
without noticing it's wrong — the same bar `apache`/`nginx` were held to,
including the live isolated-server testing done for both.

## Verification status

Every specific Caddy/Traefik technical claim in the reference doc that
could be checked against the current official docs has been — see
citations below. Every open runtime question after that (Caddy numeric
validation, Traefik router+`RedirectRegex` end to end, and both
servers' topic/post and forum pagination — exact post-ID membership,
exact `start` offsets, including a real high page number) has since
been confirmed against real binaries (`caddy` v2.11.4 and `traefik`
v3.7.12, both downloaded straight from their own GitHub releases) — see
"Confirmed by real instance test" below. **Docs research and live
single-instance verification are both fully done for both servers now,
pagination included.** What's left before implementation is
implementation-scale work — the shared redirect model, the actual
renderers, and the cross-server behavioral-equivalence test suite the
reference doc calls for — not open research questions.

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
- **Numeric validation confirmed working**, closing the one gap left
  above: Caddy's `expression` (CEL) matcher can enforce
  `^[0-9]+$`-equivalent validation directly —
  ```
  @valid_t_and_p expression `{http.request.uri.query.t}.matches("^[0-9]+$") && {http.request.uri.query.p}.matches("^[0-9]+$")`
  redir @valid_t_and_p "/topics/{http.request.uri.query.t}.html?p={http.request.uri.query.p}" permanent

  @valid_t expression `{http.request.uri.query.t}.matches("^[0-9]+$")`
  redir @valid_t /topics/{http.request.uri.query.t}.html permanent
  ```
  Tested live, every case behaves exactly like Apache/nginx: valid
  `t`+`p` (either order) redirects correctly; valid `t` alone redirects
  to the topic-only page; `t=abc` (non-numeric) and `p=5756` (no `t` at
  all — the bug case) both correctly produce **no redirect** (plain 200,
  no malformed destination); `t` numeric but `p` non-numeric correctly
  falls through to the topic-only redirect rather than either erroring
  or building a bad destination. This CEL-`expression` approach — not
  `query`'s own presence-only matching — is what a real Caddy renderer
  should use for the generic topic/post case.

**Confirmed by real instance test (`traefik` v3.7.12, official GitHub release binary):**

Tested with a file-provider dynamic config: a `Path` + `QueryRegexp`
router (numeric validation built into the rule itself, unlike Caddy's
`query` matcher) with explicit `priority`, chaining two `redirectRegex`
middlewares — one per `t`/`p` order, since `RedirectRegex` matches the
raw request URL with a plain regex and has no concept of parsed,
order-independent query args the way Caddy/nginx do. A middleware whose
own `regex` doesn't match the URL passes the request through to the
next one in the chain rather than erroring, which is what makes the
two-middleware-per-order approach work — mirroring the same "two rules,
one per argument order" shape Apache's `RewriteCond` already needed for
the same reason.

- Combined `t`+`p`, either order, plus an unrelated extra parameter, all
  correctly redirect to `/topics/<t>.html?p=<p>` with the right values —
  confirmed order-independence and extra-param tolerance, matching
  Apache/nginx/Caddy.
- The higher-priority combined-`t`+`p` router (priority 300) correctly
  outranks the lower-priority topic-only router (priority 200) when
  both would otherwise match — confirmed Traefik's explicit `priority`
  field actually governs router selection, not just rule length.
- `t=abc` (non-numeric) and `p=5756` with no `t` at all (the malformed-
  destination bug case) both correctly match **no router at all** (plain
  404, Traefik's own no-route-matched response) rather than building a
  bad destination — confirmed `QueryRegexp`'s own numeric validation in
  the router rule is sufficient gating, no separate CEL-equivalent
  needed on the Traefik side.
**Pagination — confirmed by real instance test, both Caddy and Traefik, using real topic 507's actual page-2 membership (post ids 5756, 5809, 6246, 6409, 6503, 6510) and real forum 125's actual `start` offsets (50→page2, 1000→page21, 2350→page48):**

- **Caddy topic/post exact membership**: `query`'s own multi-value OR
  semantics handles this directly — `query t=507 p=5756 p=5809 p=6246
  p=6409 p=6503 p=6510` matches any of those `p` values with that `t`,
  no CEL needed. Tested live: first post in the set (5756), last post in
  the set (6510), a post *outside* the set (9999 — correctly falls
  through to the generic topic+post rule, landing on page 1 as
  intended, not an error), reversed `p`/`t` order, and an extra `sid`
  param before/between/after the relevant ones — all correct.
- **Traefik topic/post exact membership**: `QueryRegexp(`p`,
  `^(5756|5809|6246|6409|6503|6510)$`)` combined with `Query(`t`,
  `507`)` in the router rule (priority above the generic topic+post
  router), with the same two-`RedirectRegex`-per-order chaining already
  proven for the generic case (the router doesn't expose values to the
  middleware — the middleware still needs its own regex over the raw
  URL, alternation and all). Tested live: identical results to Caddy for
  every case above, including the outside-the-set post falling through
  to the generic rule correctly.
- **Caddy forum pagination**: literal `query f=125 start=50` (etc.) per
  page — no alternation needed since a page's `start` is one exact
  value. Tested live: page 2, a middle page (21, `start=1000`), and the
  *highest* real page (48, `start=2350`) all correct — no small-page-
  count assumption; reversed `start`/`f` order and an extra param both
  correct; a non-numeric `f` produces no redirect (via the same
  `expression`/CEL numeric check already proven); a `start` that isn't
  an actual page boundary (13) correctly falls through to the generic
  forum rule rather than misfiring.
- **Traefik forum pagination**: `Query(`f`, `125`) && Query(`start`,
  `50`)` per page (again, one exact literal value, no alternation) at a
  priority above the generic forum router (`QueryRegexp(`f`,
  `^[0-9]+$`)`). Tested live: identical results to Caddy for every case
  above, including the high page number and the non-boundary-`start`
  fallthrough.

**What this closes out**: every case in the reference doc's own
acceptance-criteria list that's testable with a single-instance live
test — topic/post pagination (exact membership, both argument orders,
extra params, out-of-set fallthrough), forum pagination (exact offset,
high page numbers, both argument orders, extra params, invalid-value
fallthrough), numeric validation, and the original generic topic/post/
forum cases — has now been run live against real `caddy` and `traefik`
binaries, not inferred from docs or assumed to generalize from a
similar-looking case. What's left is implementation-scale work, not
open research questions: building the actual renderers against a shared
redirect model (reusing `multi_page_topics`/`multi_page_forums` as the
single source of truth, not recalculating pagination per renderer), and
the cross-server behavioral-equivalence test suite the reference doc
calls for (same input URL, same result, across all four targets).

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
