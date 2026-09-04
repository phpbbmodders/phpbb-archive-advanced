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

## Corrupted and missing content

- **Attachment recovery**: `find_bad_image_attachments()` validates every image attachment with Pillow; anything missing or undecodable is dropped from rendered posts rather than left as a broken link. `--attachment-recovery` checks a secondary backup directory for a working copy before giving up on one.
- **Avatar recovery**: same validation applied to avatars (local uploads and remote). `download_remote_avatars()` fetches and caches remote avatars locally (with browser User-Agent + Referer spoofing — some hosts block the generator's plain requests as hotlinking); `--avatar-overrides` covers the ones that still can't be resolved (dead host, bot protection).
- **External images**: `[img]`/`<IMG>` URLs in posts, signatures, and forum descriptions are downloaded and cached locally (`download_external_images()`) so the archive stays self-contained, rather than linking out to URLs that will eventually rot.
- **Missing post/topic content**: if a topic's post rows are missing from the dump entirely, or just its opening post, `topic.html` says so explicitly instead of silently rendering an empty or truncated thread.
- **Attachment path flattening**: some dumps end up with attachments duplicated in stray nested subdirectories from how they were originally collected; `copy_assets()` flattens by basename, keeping the shallowest copy on a name collision.
- **Malformed `[img]` tags from a botched phpBB2→3.x migration**: a board that started on phpBB2 can end up with literal `[img]`/`[/img]` bracket text left in the stored XML markup around content a *different* auto-conversion pass already touched, instead of a proper `<IMG>` element — three shapes found on a real board (665/4/277 posts): `[img]<URL url="X">...</URL>[/img]` (sometimes with stray characters on either side — a trailing `.`, or a mangled `ttp://` fragment where the migration also dropped the leading `h`), `[img]<ATTACHMENT ...>...</ATTACHMENT>[/img]`, and a bare `[img]url[/img]` never converted at all, typically nested inside an already-correctly-converted `<URL>` link (phpBB's "clickable thumbnail" pattern). All three resolve the same way the existing `<IMG>` handling does; the bare-URL case is scoped to content that's *exactly* a URL, so it can't match posts that merely discuss `[img]` as text (verified against real posts quoting phpBB's own BBCode config array, and one describing an unrelated bug inside a `[code]` block).
- **Lazy-loaded images**: every post/attachment/avatar `<img>` is marked `loading="lazy"`, so a long thread with dozens of embedded images doesn't force the browser to fetch all of them up front.

## Private-forum exclusion

`--exclude` + `expand_exclusions_recursively()`: excluding a category excludes every descendant automatically — a forum can never end up orphaned (parent hidden, child still shown prominently at the top level), which is the wrong failure direction for a privacy feature. Excluded forums, their topics, and any attachment/external image used only inside them are never copied or downloaded, not just unlinked from the rendered HTML.

## Template rework

- Real `forum_type` handling: categories (0), forums (1), and external links (2) render correctly instead of everything being treated as a forum.
- Categories get their own page (description + sub-forum list) even though phpBB doesn't allow posting directly into one.
- Sub-forums render as their own section on a forum's page.
- `forum-description` box (a forum/category's configured description, shown at the top of its page) now has its own theme-aware CSS class (`.forum-description`) instead of hardcoded inline colors that broke contrast whenever a non-default `--style-css` used a dark or saturated background.

## Custom color scheme (`--style-css`)

The archive's own simple layout ships with a neutral default palette (`generator/static/style.css`). `--style-css FILE` swaps it for any stylesheet you point at. Every generated page `<link>`s `assets/style.css` rather than inlining it — the css is copied into `output/assets/` by `copy_assets()` on each run, and the same file can be dropped directly into an already-built archive's `assets/` to re-theme it without regenerating anything.

`docs/contrib/phpbbmodders-style.css.example` is a real-world example: colors approximating phpbbmodders.net's actual live `prosilver_se_revolution` child theme (dark charcoal page, brick-red frame and header/category bars) plus its base `prosilver` theme's link/text colors — pulled from the real CSS, not guessed, and verified against a full regeneration + browser render, not just visual inspection of the source.

## Board-wide announcement (`--announcement`)

`--announcement FILE` reads a plain-text BBCode file — written fresh for the archive itself (e.g. "this board is now a read-only archive"), not pulled from the dump — and renders it via the same BBCode parser used for post content (`uid=""`, since hand-written text has no phpBB UID annotation to strip). Shown on the index, every forum page, and every topic page. Omitting the flag, or leaving the file blank, renders nothing.

`docs/contrib/announcement.txt.example` is a worked example of the expected format.

## Sitemap and robots.txt (`--sitemap-url`)

Writes `output/sitemap.xml` (index, every forum, every topic, each with a `<lastmod>` from its most recent post) and `output/robots.txt` pointing at it. Requires an absolute base URL because sitemap entries must be absolute, unlike every other link the archive generates, which stays relative so the archive works at any path. Excluded forums/topics are already left out of `output/` entirely, so they're never in the sitemap either.

## Full-text search (`--search`)

Adds `search.html` (linked from every page's breadcrumb bar) and indexes every generated page with Pagefind, a static client-side search engine, via the `pagefind[bin]` Python package — a real compiled search binary installed through pip, no Node.js needed. Runs as a subprocess after every other page is written.

Result titles come from a `data-pagefind-meta="title:..."` attribute set on every page's `<body>` (via a new `body_attrs` template block in `base.html`) — without it, Pagefind defaults to each page's first `<h1>`, which on this archive is always just the site name, making every search result look identical. `search.html` itself is excluded from the index (`data-pagefind-ignore`) since it has no content of its own. The widget is re-themed via its own documented CSS custom properties (`--pagefind-ui-primary` etc.) rather than by fighting its markup — `docs/contrib/phpbbmodders-style.css.example`'s `#search` block is a real worked example.

## `docs/contrib/`

Real-world starting points for `--ignore-hosts`, `--style-css`, and `--announcement`, with its own README explaining what pairs with what. All files follow a `<name>.<ext>.example` naming convention. `known-dead-hosts.json.example` seeds a fixed live filename (`known-dead-hosts.json`, gitignored, conventionally kept at the repo root — see `.gitignore`); `phpbbmodders-style.css.example` and `announcement.txt.example` don't — `--style-css`/`--announcement` take an arbitrary path, so a live copy can go anywhere, or the example can be pointed at directly. `known-dead-hosts.json.example`'s hostnames (parked domains, shut-down image hosts, or previously misclassified ones corrected after direct verification) were confirmed dead against a real dump, not a generic guess. `run.sh.example` pairs with no single flag — it's phpbbmodders.net's real wrapper script, showing how every flag composes into one command.

## Operational fixes

- **Self-healing `.venv`**: `generate.sh` detects a broken virtualenv (its console-script wrappers hardcode an absolute shebang path back to the venv at creation time, so copying/moving the containing folder silently breaks `pip` etc. even though the `python` binary keeps working) and recreates it instead of failing.
- **`generate.sh` convenience wrapper**: sets up `.venv` if missing, installs dependencies, runs the generator — one command instead of three.

## README

Rewritten to document every flag above, keep the usage/options block synchronized with actual `--help` output (this also fixed a pre-existing gap: `--ignore-hosts` and `--attachment-recovery` had been implemented but never documented), and add worked examples for `--exclude`, `--url-mirrors`, `--style-css`, `--announcement`, `--sitemap-url`, and `--search`.
