# Structural changes and features since the original

Everything below was added on top of matildepark's original 4 commits (`b079b39`..`8b627fa`) while archiving a real, large phpBB 3.3.x board (phpbbmodders.net — 4300+ topics, 26800+ posts, 8100+ users).

## New CLI flags

| Flag | Does |
|------|------|
| `-m`/`--missing-avatars` | Diagnostic mode: lists users whose avatar can't be fetched/decoded, writes a starter `--avatar-overrides` template |
| `--avatar-overrides FILE` | JSON mapping `user_id` → local image, for avatars the generator can't resolve on its own |
| `-l`/`--list-forums` | Diagnostic mode: prints every forum/category with its `forum_id`, indented to show nesting |
| `--exclude FILE` | JSON `{"categories": [...], "forums": [...]}` — recursively excludes forums/categories, and any attachment or external image used only inside them, from the archive entirely |
| `-i`/`--check-images` | Diagnostic mode: lists external `[img]`/`<IMG>` URLs that fail to resolve, writes `output/unresolved_images.json` |
| `--url-mirrors FILE` | JSON `{"url_prefix": "local_dir"}` — resolves external images from a local directory instead of the network, for hosts that block the generator |
| `--incremental` | Keeps previously-downloaded attachments/avatars/external images across runs; only failed URLs retry. Generated HTML pages are still rebuilt fresh every run |
| `--ignore-hosts FILE` | JSON array of hostnames to skip entirely without a network attempt — for hosts already confirmed permanently dead |
| `--attachment-recovery DIR` | A separately-collected backup (same layout as an attachment source) checked for a working copy of any attachment missing/corrupted in the main dump |
| `--style-css FILE` | Replaces the built-in neutral stylesheet with a custom one — see below |
| `--announcement FILE` | Shows a notice on index/forum/topic pages — see below |
| `--sitemap-url URL` | Writes `sitemap.xml`/`robots.txt` for that absolute base URL — see below |
| `--search` | Adds `search.html`, indexed with Pagefind — see below |
| `-c`/`--check-links` | Diagnostic mode: scans an already-generated `output/` for internal links that don't resolve, writes `output/broken_links.json` — see below |
| `--profile-position {left,right}` | Which side of a post the poster's profile sidebar sits on in viewtopic. Defaults to left, matching phpBB's own layout |

## Corrupted and missing content

- **Attachment recovery**: `find_bad_image_attachments()` validates every image attachment with Pillow; anything missing or undecodable is dropped from rendered posts rather than left as a broken link. `--attachment-recovery` checks a secondary backup directory for a working copy before giving up on one.
- **Avatar recovery**: same validation applied to avatars (local uploads and remote). `download_remote_avatars()` fetches and caches remote avatars locally (with browser User-Agent + Referer spoofing — some hosts block the generator's plain requests as hotlinking); `--avatar-overrides` covers the ones that still can't be resolved (dead host, bot protection).
- **External images**: `[img]`/`<IMG>` URLs in posts, signatures, and forum descriptions are downloaded and cached locally (`download_external_images()`) so the archive stays self-contained, rather than linking out to URLs that will eventually rot.
- **`postimg.org` → `postimg.cc` recovery**: `postimg.org` itself is a parked/dead domain (confirmed by fetching it directly — it now shows a for-sale page), but old `postimg.org` links still resolve once rewritten to `postimg.cc`, the same service's current domain, keeping the same subdomain and path (`http://s25.postimg.org/…` → `http://s25.postimg.cc/…`, verified against real links from this dump). `_fetch_image()` retries once against the rewritten host before giving up, and whatever resolves is cached locally exactly like any other external image — no separate mechanism.
- **Missing post/topic content**: if a topic's post rows are missing from the dump entirely, or just its opening post, `topic.html` says so explicitly instead of silently rendering an empty or truncated thread.
- **Attachment path flattening**: some dumps end up with attachments duplicated in stray nested subdirectories from how they were originally collected; `copy_assets()` flattens by basename, keeping the shallowest copy on a name collision.
- **Malformed `[img]` tags from a botched phpBB2→3.x migration**: a board that started on phpBB2 can end up with literal `[img]`/`[/img]` bracket text left in the stored XML markup around content a *different* auto-conversion pass already touched, instead of a proper `<IMG>` element — three shapes found on a real board (665/4/277 posts): `[img]<URL url="X">...</URL>[/img]` (sometimes with stray characters on either side — a trailing `.`, or a mangled `ttp://` fragment where the migration also dropped the leading `h`), `[img]<ATTACHMENT ...>...</ATTACHMENT>[/img]`, and a bare `[img]url[/img]` never converted at all, typically nested inside an already-correctly-converted `<URL>` link (phpBB's "clickable thumbnail" pattern). All three resolve the same way the existing `<IMG>` handling does; the bare-URL case is scoped to content that's *exactly* a URL, so it can't match posts that merely discuss `[img]` as text (verified against real posts quoting phpBB's own BBCode config array, and one describing an unrelated bug inside a `[code]` block).
- **Lazy-loaded images**: every post/attachment/avatar `<img>` is marked `loading="lazy"`, so a long thread with dozens of embedded images doesn't force the browser to fetch all of them up front.
- **XML-format smilies (`<E>code</E>`)**: phpBB 3.2+'s XML post storage represents a smiley as `<E>:code:</E>` rather than the older `<!-- s... --><img .../><!-- s... -->` HTML-comment form `_convert_smilies()` already handled. Without a specific handler, the generic "strip unknown XML tags" fallback removed the `<E>` tags but kept the raw code as visible text (`:ugeek:`, `:P`, etc., never converted to the actual smiley image). `_convert_xml_markup()` now resolves `<E>` content against the same `phpbb_smilies` code → filename map; an unrecognized code is left as its raw text rather than dropped.
- **Smiley sizing**: neither smiley path set a `width`/`height` attribute, so a pack whose source image files are larger than their intended display size (a real board's `icon_e_ugeek.png`: 202×214px on disk, `phpbb_smilies.smiley_width`/`smiley_height` says 17×18) rendered huge instead of icon-sized. Both paths now read the dump's own display-size columns — real per-smiley metadata, not a guessed constant — and set the attributes when present; a code with 0×0 (unset in some dumps) falls back to the old unscaled behavior.
- **Downloadable attachment filenames**: a non-image attachment's `<a href>` pointed straight at its `physical_filename` (a hash-like name on disk), so saving it gave a meaningless filename instead of the original one. The link now carries `download="<real_filename>"`, so a browser save keeps the name it actually had.
- **Attachment-type badges**: a non-image attachment was a bare filename link with no visual cue about what it was. A small CSS badge (e.g. "ZIP") now shows the file's own extension — no per-filetype icon set exists in a bare SQL dump to draw a real icon from, so this is a text badge rather than a fabricated one.
- **Internal cross-topic links**: a post linking to another topic on the *same* board (`[url=http://.../viewtopic.php?f=16&t=11434]...[/url]`, or the XML `<URL url="...">` equivalent) pointed at the original site rather than the corresponding page in this archive. Both the XML and old-BBCode link paths now check the linked `t=` topic id against the set of topic ids actually in this archive and rewrite it to a relative `topics/N.html` link (preserving a `#pNNNN` post anchor) when it matches; a link to a topic that's excluded, or on an entirely different board, is left as a normal external link rather than becoming a broken one.
- **Linked last-poster names**: the "last post by X" name shown on the index (per forum) and on a forum page (per topic) was plain text, unlike the topic-starter name next to it, which was already a link. Both now link to `users/<id>.html` using the dump's own `forum_last_poster_id`/`topic_last_poster_id` columns, falling back to plain text only when that id is 0 (no poster recorded).
- **Post permalinks**: every post already carried an `id="pN"` anchor (used by the internal cross-topic link rewrite above), but there was no visible way to get a link to one specific post — a viewer would have to know to hand-craft the `#pN` fragment. The post's own "Posted: ..." timestamp is now a link to its own anchor, so right-click → copy link (or just clicking through) gives the exact permalink.

## Private-forum exclusion

`--exclude` + `expand_exclusions_recursively()`: excluding a category excludes every descendant automatically — a forum can never end up orphaned (parent hidden, child still shown prominently at the top level), which is the wrong failure direction for a privacy feature. Excluded forums, their topics, and any attachment/external image used only inside them are never copied or downloaded, not just unlinked from the rendered HTML.

## Template rework

- Real `forum_type` handling: categories (0), forums (1), and external links (2) render correctly instead of everything being treated as a forum.
- Categories get their own page (description + sub-forum list) even though phpBB doesn't allow posting directly into one.
- Sub-forums render as their own section on a forum's page.
- `forum-description` box (a forum/category's configured description, shown at the top of its page) now has its own theme-aware CSS class (`.forum-description`) instead of hardcoded inline colors that broke contrast whenever a non-default `--style-css` used a dark or saturated background.
- Mobile reflow: the fixed-width `.postprofile` sidebar and the fixed-width lastpost column didn't adapt below ~600px, crowding a phone-size viewport. A media query stacks the post layout vertically and shrinks the lastpost column on narrow screens.
- Poll results: a topic's poll (title, options, vote counts/percentages, total) is rendered above its posts when the dump has one — previously not rendered anywhere, silently dropping real content.
- `--profile-position {left,right}` (default `left`, matching phpBB's own layout): which side of a post the poster's profile sidebar sits on in viewtopic — a CSS class flip (`post-wrap--right`) plus a matching border-side swap, gated so the default archive is unchanged.

## Custom color scheme (`--style-css`)

The archive's own simple layout ships with a neutral default palette (`generator/static/style.css`). `--style-css FILE` swaps it for any stylesheet you point at. Every generated page `<link>`s `assets/style.css` rather than inlining it — the css is copied into `output/assets/` by `copy_assets()` on each run, and the same file can be dropped directly into an already-built archive's `assets/` to re-theme it without regenerating anything.

`docs/contrib/phpbbmodders-style.css.example` is a real-world example: colors approximating phpbbmodders.net's actual live `prosilver_se_revolution` child theme (dark charcoal page, brick-red frame and header/category bars) plus its base `prosilver` theme's link/text colors — pulled from the real CSS, not guessed, and verified against a full regeneration + browser render, not just visual inspection of the source.

The stylesheet link carries a `?v=<hash>` of `assets/style.css`'s own content (`env.globals["style_version"]`), so a re-themed or re-regenerated archive is picked up immediately instead of a browser serving a stale cached copy — `http.server` (and some real static hosts) sends only `Last-Modified`, no `Cache-Control`/`ETag`, so a browser can skip revalidation entirely. Doesn't affect the "drop a new `assets/style.css` into an already-built archive" workflow above: that leaves the HTML, and its query string, untouched either way.

## Board-wide announcement (`--announcement`)

`--announcement FILE` reads a plain-text BBCode file — written fresh for the archive itself (e.g. "this board is now a read-only archive"), not pulled from the dump — and renders it via the same BBCode parser used for post content (`uid=""`, since hand-written text has no phpBB UID annotation to strip). Shown on the index, every forum page, and every topic page. Omitting the flag, or leaving the file blank, renders nothing.

`docs/contrib/announcement.txt.example` is a worked example of the expected format.

## Sitemap and robots.txt (`--sitemap-url`)

Writes `output/sitemap.xml` (index, every forum, every topic, each with a `<lastmod>` from its most recent post) and `output/robots.txt` pointing at it. Requires an absolute base URL because sitemap entries must be absolute, unlike every other link the archive generates, which stays relative so the archive works at any path. Excluded forums/topics are already left out of `output/` entirely, so they're never in the sitemap either.

## Open Graph / Twitter Card meta tags

Every topic page carries `og:title`, `og:type`, `og:site_name`, `og:description` (the opening post's text, stripped of HTML and truncated via Jinja2's built-in `striptags`/`truncate` filters — no new Python code needed), and matching `twitter:*` tags, unconditionally — so a shared topic link shows a real title/preview instead of nothing. `og:description`/`twitter:description` are omitted gracefully when a topic's post content is missing from the dump. `og:url` is the one tag that needs an absolute URL; it reuses `--sitemap-url`'s value rather than introducing a second "what's my base URL" flag, and is simply omitted when that flag isn't set.

## Full-text search (`--search`)

Adds `search.html`, linked from every page's header (next to the "Static archive" tagline, styled like the site-title link) only when `--search` is actually used (`env.globals["search_enabled"]`, checked in `base.html` — the link isn't shown, and no dead link is generated, on a run without the flag), and indexes every generated page with Pagefind, a static client-side search engine, via the `pagefind[bin]` Python package — a real compiled search binary installed through pip, no Node.js needed. Runs as a subprocess after every other page is written.

The link originally lived in the breadcrumb bar, where it read as a fake breadcrumb level ("Board index · Search") rather than a utility link — moved to the header, out of the navigation trail entirely.

Result titles come from a `data-pagefind-meta="title:..."` attribute set on every page's `<body>` (via a new `body_attrs` template block in `base.html`) — without it, Pagefind defaults to each page's first `<h1>`, which on this archive is always just the site name, making every search result look identical. `search.html` itself is excluded from the index (`data-pagefind-ignore`) since it has no content of its own. The widget is re-themed via its own documented CSS custom properties (`--pagefind-ui-primary` etc.) rather than by fighting its markup — `docs/contrib/phpbbmodders-style.css.example`'s `#search` block is a real worked example.

Pagefind can't fetch its own index under `file://` — confirmed against Pagefind's own docs and by reproducing it: opening `search.html` directly as a file accepts a query and shows "Searching for…" but never returns results, with no error surfaced anywhere. Needs any real static file server (even a local one) to work at all.

## Broken-internal-link checker (`-c`/`--check-links`)

Unlike `-l`/`-m`/`-i`, which inspect the dump *before* generating, this inspects an already-generated `output/` *after* — it only makes sense to run once a real build exists. Walks every generated page's `href`/`src` attributes and flags a link to a file that doesn't exist, or a `#anchor` link whose target file exists but doesn't contain that anchor (e.g. `topics/106.html#p53377`, a permalink to one specific post that got excluded or was never recovered) — the anchor case is the more useful of the two, since it's not something you'd notice just by browsing. External URLs are out of scope; that's `--ignore-hosts`/`--url-mirrors`'s job. No new dependency — a regex over each file's text, not an HTML parser.

The regex is anchored to an actual opening tag (`<a ...href="..."` / `<img ...src="..."`), not a bare `href="..."` match anywhere in the file — found the hard way: a first pass against a real board's dump returned 6,351 "broken links," almost all of them phpBB template source code (`{U_FEED}`, `href="{T_THEME_PATH}/print.css"`, etc.) that someone had pasted inside a `[code]` block. Its `<`/`>` were correctly HTML-escaped so it couldn't be interpreted as a real tag, but the literal `"` characters in `href="..."` weren't, so a bare-substring regex matched it as if it were one. Anchoring to a real unescaped `<tagname` before the attribute fixed it — re-running against the same dump dropped the count to 19, and those remaining were pre-existing data quirks (a real `<a href="%27,%27">` from that same quoted source-array text, and a few spam posts missing a proper `http://` prefix), not archive bugs.

## `docs/contrib/`

Real-world starting points for `--ignore-hosts`, `--style-css`, and `--announcement`, with its own README explaining what pairs with what. Config-shaped files follow a `<name>.<ext>.example` naming convention; scripts don't, since they're run directly rather than edited into a live config. `known-dead-hosts.json.example` seeds a fixed live filename (`known-dead-hosts.json`, gitignored, conventionally kept at the repo root — see `.gitignore`); `phpbbmodders-style.css.example` and `announcement.txt.example` don't — `--style-css`/`--announcement` take an arbitrary path, so a live copy can go anywhere, or the example can be pointed at directly. `known-dead-hosts.json.example`'s hostnames (parked domains, shut-down image hosts, or previously misclassified ones corrected after direct verification) were confirmed dead against a real dump, not a generic guess. `run.sh` pairs with no single flag — it's phpbbmodders.net's real wrapper script, showing how every flag composes into one command. `serve.sh` serves a generated `output/` over real HTTP with Python's built-in server, needed to actually test `--search` locally since Pagefind can't fetch its own index under a `file://` path.

## Operational fixes

- **Self-healing `.venv`**: `generate.sh` detects a broken virtualenv (its console-script wrappers hardcode an absolute shebang path back to the venv at creation time, so copying/moving the containing folder silently breaks `pip` etc. even though the `python` binary keeps working) and recreates it instead of failing.
- **`generate.sh` convenience wrapper**: sets up `.venv` if missing, installs dependencies, runs the generator — one command instead of three.

## README

Rewritten to document every flag above, keep the usage/options block synchronized with actual `--help` output (this also fixed a pre-existing gap: `--ignore-hosts` and `--attachment-recovery` had been implemented but never documented), and add worked examples for `--exclude`, `--url-mirrors`, `--style-css`, `--announcement`, `--sitemap-url`, and `--search`.
