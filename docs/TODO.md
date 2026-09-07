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

- **Custom profile fields (CPF)** on `user.html` — filter out `field_hide=1`/`field_active=0`/admin-only fields before rendering. Blocked on knowing what fields the real dump actually has (`phpbb_profile_fields`); the DB was mid-rebuild/locked the two times this came up before and it was never actually checked.
- **Topic view counts** (`topic_views`, reported ~23.2M total across the board) — not rendered anywhere on forum/topic pages.
- **Topic pagination**: flagged as deferred in an earlier session; the original scope notes (split long topics into multiple pages vs. one page per topic, page-size threshold, etc.) weren't captured anywhere, so this needs to be re-scoped from scratch with the user before implementing, not guessed at.

- **Renderer bug: `[img]`/`[/img]` sitting alone inside `<s>`/`<e>` XML syntax markers, with no outer `<IMG src>` wrapper, never renders as an image *or* a link.** Found while investigating security review finding 10 (image discovery), but it's a rendering bug, not a discovery one, so out of scope for that fix. `<s>[^<]*</s>`/`<e>[^<]*</e>` stripping in `_convert_xml_markup()` removes `[img]`/`[/img]` bracket text entirely when either sits alone as the marker's whole content (e.g. `<s>[img]</s>http://example.com/pic.png<e>[/img]</e>`) — by the time `_convert_xml_markup()`'s own `[img]`-handling regexes run, there's no bracket text left to match, and the bare URL just falls through every handler untouched, rendering as raw visible text in the post. Confirmed real: `http://highxp.eu/f/download/file.php?id=2429&amp;pixel.png` (14 real occurrences of this general shape) renders as literal visible URL text today, not an image. Needs a fix in `bbcode.py`, not `generate.py` — likely resolving the image inline during the `<s>`/`<e>` stripping pass itself, before the bracket text is lost.

- **Edit history** (`post_edit_time`/`post_edit_count`/`post_edit_reason`) — not shown on posts, though phpBB tracks it. Confirmed against real phpBB 3.3.x core (`viewtopic.php` + prosilver's `viewtopic_body.html`) that this is real, previously-public content, not an admin/internal-only field: an edited post gets a `<div class="notice">` reading "Last edited by *username* on *date*, edited *N* time(s) in total.", plus a `Reason: <text>` line when the editor filled one in. The "Last edited by..." line itself is gated by a board config setting (`display_last_edited`) — but if a reason was given, the whole notice shows regardless of that setting (`($post_edit_count && $config['display_last_edited']) || $post_edit_reason` is the actual core gate). Independently re-verified against phpbbmodders.net's own real dump: **503 real edited posts** (the earlier-reported ~452 was off), and `display_last_edited = 1` (enabled) — so the "last edited" line was publicly visible on the live board for every edit, not just ones with a reason. Real example with a reason, confirmed reachable in the archive (forum 125 isn't excluded): post 51982 in topic 11162, "removed zip at OP request".
