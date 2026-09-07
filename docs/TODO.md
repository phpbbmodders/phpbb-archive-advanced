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
- **Edit history** (`post_edit_time`/`post_edit_count`/`post_edit_reason`, reported 452 real edited posts) — not shown on posts, though phpBB tracks it.
- **Moved topics** (`topic_moved_id`, reported 18 real rows) — no redirect/notice for a topic that was moved; overlaps with the still-open "Moved-topic pointers are treated as ordinary topics with missing content" item from the original security review (finding 15).
- **Attachment file-type icons**: Bootstrap Icons vs. Tabler Icons was discussed (both MIT-licensed, both use `currentColor`) but no decision was made and nothing was built — attachment badges are still plain CSS text badges (see "Attachment-type badges" in `docs/CHANGES.md`).
- **Topic pagination**: flagged as deferred in an earlier session; the original scope notes (split long topics into multiple pages vs. one page per topic, page-size threshold, etc.) weren't captured anywhere, so this needs to be re-scoped from scratch with the user before implementing, not guessed at.

- **Renderer bug: `[img]`/`[/img]` sitting alone inside `<s>`/`<e>` XML syntax markers, with no outer `<IMG src>` wrapper, never renders as an image *or* a link.** Found while investigating security review finding 10 (image discovery), but it's a rendering bug, not a discovery one, so out of scope for that fix. `<s>[^<]*</s>`/`<e>[^<]*</e>` stripping in `_convert_xml_markup()` removes `[img]`/`[/img]` bracket text entirely when either sits alone as the marker's whole content (e.g. `<s>[img]</s>http://example.com/pic.png<e>[/img]</e>`) — by the time `_convert_xml_markup()`'s own `[img]`-handling regexes run, there's no bracket text left to match, and the bare URL just falls through every handler untouched, rendering as raw visible text in the post. Confirmed real: `http://highxp.eu/f/download/file.php?id=2429&amp;pixel.png` (14 real occurrences of this general shape) renders as literal visible URL text today, not an image. Needs a fix in `bbcode.py`, not `generate.py` — likely resolving the image inline during the `<s>`/`<e>` stripping pass itself, before the bracket text is lost.

- **Sticky/announcement topics aren't actually pinned/reordered to the top of a forum listing, only badged.** `db.get_topics()` sorts every topic in a forum with a single `ORDER BY topic_last_post_time DESC`, with no case for `topic_type` at all — confirmed real: the existing "Sticky/announcement/locked topic badges" feature (see `docs/CHANGES.md`) shows the badge, but a sticky/announcement topic still sits wherever its own last-post-time would normally place it, interleaved with ordinary topics, instead of pinned above them. phpBB's own real convention: announcements (`topic_type=2`) sort above stickies (`topic_type=1`), which sort above ordinary topics (`topic_type=0`) — within each group, by the forum's normal order (here, last-post-time descending, unchanged). Needs an `ORDER BY` change in `get_topics()` (e.g. `ORDER BY CASE topic_type WHEN 2 THEN 0 WHEN 1 THEN 1 ELSE 2 END, topic_last_post_time DESC`) — not yet verified against a real forum with actual sticky/announcement topics.
