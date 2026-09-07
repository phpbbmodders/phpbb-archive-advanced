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
  agnostic requirements, not something specific to apache/nginx.

Reported from an earlier session's own notes (numbers below as reported, not yet independently re-verified against the current dump):

- **Topic pagination**: flagged as deferred in an earlier session; the original scope notes (split long topics into multiple pages vs. one page per topic, page-size threshold, etc.) weren't captured anywhere, so this needs to be re-scoped from scratch with the user before implementing, not guessed at.

