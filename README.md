# phpbb-archive — static site generator for phpBB forums

phpbb-archive converts a phpBB 3.x MySQL dump into a self-contained static HTML archive. No server required — the output is plain files you can host anywhere (GitHub Pages, Neocities, nginx, etc.).

This repo is a deployment of the tool for phpbbmodders.net — `./run.sh` wires every flag below to this board's own `config/` files in one command. See [`docs/SITE_SETUP.md`](docs/SITE_SETUP.md) for what each of those files is and does.

### What you need

From your phpBB server, collect the following into a `dump/` directory:

| File/dir | How to get it |
|----------|--------------|
| `*.sql` | `mysqldump -u USER -p DATABASE > forum.sql` |
| `config.php` | Copy from your phpBB installation root |
| `files/` | Attachments directory (copy whole dir) |
| `images/` | Avatars, smilies, rank images (copy whole dir) |
| `styles/` | Theme CSS and images (copy whole dir) |

The generator auto-discovers the `.sql` file in `dump/` and reads the board name directly from the database — no flags needed.

## Quick start

Install the dependencies (Jinja2 for templating, Pillow for validating image files):

```bash
python3 -m venv .venv
.venv/bin/pip install jinja2 pillow
```

Then run the generator:

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/
```

That's it. Open `output/index.html` in a browser to browse the archive. `generate.sh` wraps the same command and sets up `.venv` for you if it doesn't exist yet.

Attachments (images, zip, rar), avatars, and `[img]`-tagged images that are missing or fail to decode/validate are dropped from the archive rather than left as broken links. Attachments are served under their real original filename (not the on-disk physical hash), so "Save Image/Link As" gives back the actual filename. Attachments/avatars/external images that only exist as external URLs (remote avatars, hotlinked signature images) are downloaded once and cached locally so the archive stays self-contained — a URL that's genuinely dead just gets skipped, and is retried again on every future run (see `--incremental` below if that's not what you want, or `--regen-light` to skip the network for uncached images entirely when you're only iterating on a template/logic change against an `output/` you've already fully generated once). Every post/attachment/avatar image is marked `loading="lazy"`, so a long thread with dozens of embedded images doesn't force the browser to fetch all of them up front.

## Usage

```bash
.venv/bin/python -m generator.generate --help
```

```output
usage: generate.py [-h] [--dump DUMP] [--output OUTPUT]
                   [--avatar-overrides FILE] [-m] [--exclude FILE] [-l]
                   [--password-override FILE] [--url-mirrors FILE] [-i] [-c]
                   [--incremental] [--regen-light] [--ignore-hosts FILE]
                   [--attachment-recovery DIR] [--style-css FILE]
                   [--announcement FILE] [--sitemap-url URL] [--search]
                   [--profile-position {left,right}]
                   [--favicon FILE | --favicon-url URL]
                   [--logo FILE | --logo-url URL] [--logo-natural-size]
                   [--theme {light,dark}] [--board-hosts FILE]
                   [--redirect-format {apache,nginx}]
                   [--redirect-old-prefix PATH]

Generate a static HTML archive from a phpBB MySQL dump

options:
  -h, --help            show this help message and exit
  --dump DUMP           Path to dump/ directory
  --output OUTPUT       Path to output/ directory
  --avatar-overrides FILE
                        JSON file mapping user_id to a local avatar image, for
                        avatars the generator can't fetch or decode on its own
  -m, --missing-avatars
                        List users with a missing/broken avatar and write a
                        starter --avatar-overrides template, instead of
                        generating the archive
  --exclude FILE        JSON file of forum/category IDs to leave out of the
                        archive entirely: {"categories": [...], "forums":
                        [...]}. Excluding a category also excludes everything
                        under it, recursively — list just the top-level id(s)
                        you want hidden. Attachments and external images used
                        only in excluded forums are never copied or
                        downloaded. Use -l/--list-forums to find forum_id
                        values.
  -l, --list-forums     Print every forum/category with its forum_id (indented
                        to show nesting), to help build an --exclude file,
                        instead of generating the archive
  --password-override FILE
                        Every forum with a phpBB access password set is
                        excluded automatically, the same as --exclude — a
                        static archive has no login to gate access behind.
                        JSON file of forum IDs to include anyway: {"forums":
                        [...]}, for an archive owner who knows the real
                        password and consents to archiving that forum's
                        content.
  --url-mirrors FILE    JSON file of {"url_prefix": "local_dir"} mappings. Any
                        external [img]/avatar URL starting with a prefix is
                        looked up in the matching local directory first,
                        instead of being fetched over the network — useful
                        when a source site blocks the generator (e.g.
                        Cloudflare) but you have direct filesystem access to
                        its files.
  -i, --check-images    List external [img]/<IMG> URLs that fail to resolve
                        (via --url-mirrors or the network) and write them to
                        output/unresolved_images.json, instead of generating
                        the archive — use ahead of a full run to see what
                        needs mirroring
  -c, --check-links     Scan an already-generated output/ for internal
                        href/src links that don't resolve — either to a file
                        that doesn't exist, or (for a #anchor link) to an
                        id="..." that doesn't exist in the target file — and
                        write them to output/broken_links.json, instead of
                        generating the archive. External URLs aren't checked
                        here — see --ignore-hosts/--url-mirrors for those. Run
                        after a normal build.
  --incremental         Keep previously-downloaded
                        attachments/avatars/external images instead of re-
                        fetching everything — only failed URLs are retried.
                        Generated pages are still rebuilt fresh every run. Off
                        by default.
  --regen-light         Skip the network entirely for remote avatars/external
                        images not already cached in an existing output/
                        (instead of attempting and retrying failures like
                        --incremental does) — implies --incremental. For
                        quickly re-rendering HTML/template changes against
                        output/ you've already run a full generate on; the
                        exact set of resolved images doesn't change for that
                        run. Off by default.
  --ignore-hosts FILE   JSON array of hostnames (e.g. ["tinypic.com"]) to skip
                        entirely without a network attempt — matches
                        subdomains too. Useful for hosts you already know are
                        permanently gone, so --incremental doesn't keep paying
                        their timeout on every future run.
  --attachment-recovery DIR
                        Directory with the same layout as an attachment source
                        (physical_filename-named files) to check for a working
                        copy of any attachment that's missing or fails to
                        decode from the main dump — e.g. a separately-
                        collected backup that isn't affected by the same
                        corruption. A working copy found there replaces the
                        broken one instead of it being dropped.
  --style-css FILE      Replace the archive's built-in neutral stylesheet with
                        a custom one (e.g. colors approximating a specific
                        board's own theme). Every page links assets/style.css
                        rather than inlining it, so this file (or a hand-
                        edited assets/style.css copy) can also just be dropped
                        into an already-generated archive to re-theme it
                        without regenerating anything. Omit to keep the
                        default neutral palette.
  --announcement FILE   Plain text file of BBCode (e.g. "[b]This board is now
                        a read-only archive.[/b]") to show as a notice on the
                        index, every forum page, and every topic page. Not
                        pulled from the dump — written fresh for the archive
                        itself. Omit the flag, or leave the file blank, for no
                        announcement.
  --sitemap-url URL     Absolute base URL the archive will be hosted at (e.g.
                        https://archive.example.com/) — writes
                        output/sitemap.xml (index, every forum, every topic,
                        with a lastmod date from the most recent post) and
                        output/robots.txt pointing at it. Every other link the
                        archive generates is relative so it works at any path;
                        sitemap entries can't be, which is why this needs an
                        explicit absolute URL rather than being inferred. Omit
                        for no sitemap/robots.txt.
  --search              Add a dedicated search.html (linked from every page's
                        header) indexing every generated page with Pagefind, a
                        static client-side search engine — no server required,
                        same as the rest of the archive. Requires the
                        pagefind[bin] package (see generator/requirements.txt)
                        and runs it as a subprocess after every other page is
                        written. Off by default.
  --profile-position {left,right}
                        Which side of a post the poster's profile sidebar
                        (avatar, rank, post count) sits on in viewtopic.
                        Defaults to left, matching phpBB's own layout.
  --favicon FILE        Image file (ico/png/svg/...) used as the archive's
                        favicon. Kept in its original format, copied to
                        output/favicon.<ext>. Omit for no favicon.
  --favicon-url URL     Fetch the favicon from a live URL instead of a local
                        file (e.g. https://example.com/favicon.ico) — for a
                        board's real favicon, which doesn't live anywhere in a
                        bare SQL dump. A URL that can't be reached is skipped
                        with a warning, same as any other network fetch in
                        this generator, rather than failing the whole run.
  --logo FILE           Image file (png/svg/gif/...) shown in the header in
                        place of the plain site-name text, scaled via CSS to
                        fit (max-height: 50px in the default stylesheet). Kept
                        in its original format, copied to
                        output/assets/logo.<ext>. Omit to keep the plain text
                        site name.
  --logo-url URL        Fetch the logo from a live URL instead of a local file
                        — for a board's real logo, which doesn't live anywhere
                        in a bare SQL dump. A URL that can't be reached (dead
                        link, or blocked by something like a Cloudflare JS
                        challenge that this can't pass) is skipped with a
                        warning, same as any other network fetch in this
                        generator, rather than failing the whole run.
  --logo-natural-size   Show the logo at its own original size instead of
                        scaling it to fit the header (max-height: 50px in the
                        default stylesheet). Off by default.
  --theme {light,dark}  Which built-in neutral palette to use — light
                        (default) or dark. Has no effect when --style-css is
                        given, since a custom stylesheet is its own fixed
                        palette.
  --board-hosts FILE    JSON array of extra hostnames this board is also known
                        to have been reachable at (e.g. a former domain), in
                        addition to the dump's own phpbb_config.server_name.
                        Used to recognize a post's own viewtopic.php link back
                        to this board so it can be rewritten to a relative in-
                        archive link — without this, only a link that happens
                        to use the domain currently in the dump is recognized,
                        so a board that changed domains over its lifetime
                        needs its other domains listed here. A topic id alone
                        is not enough to tell: it's just a small integer, and
                        two unrelated phpBB installs (or the same board on an
                        old and current domain) can easily reuse the same one
                        for a completely different topic.
  --redirect-format {apache,nginx}
                        Write a server-config snippet that 301-redirects the
                        live board's old dynamic URLs
                        (viewtopic.php/viewforum.php/memberlist.php) to this
                        archive's own static pages — output/.htaccess for
                        apache, output/nginx-redirects.conf for nginx (include
                        it in your server {} block). A single generic rule per
                        old script, driven by whatever id is actually in the
                        incoming request, not a per-page list — an id that
                        isn't actually in this archive just 404s through it,
                        the same as it would without this. Omit for no
                        redirect file.
  --redirect-old-prefix PATH
                        Path the live board's scripts were actually installed
                        under (e.g. "board" if it was reachable at
                        .../board/viewtopic.php), so --redirect-format's rules
                        match the old server's real request paths. Auto-
                        detected from the dump's own phpbb_config.script_path
                        by default; only pass this to override that (e.g. the
                        board moved to a different path after the dump was
                        taken). Use "" to force no prefix. Has no effect
                        without --redirect-format.
```

### Redirecting the old board's dynamic URLs to this archive

Once the archive replaces the live board, old links (search results, bookmarks, forum posts elsewhere) still point at the dynamic URLs the live board used — `--redirect-format` writes a ready-to-use server-config snippet that 301-redirects them to the corresponding static page:

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/ --redirect-format nginx
```

The old install path (e.g. `/board/viewtopic.php` rather than a root-level `/viewtopic.php`) is auto-detected from the dump's own `phpbb_config.script_path`; pass `--redirect-old-prefix` only to override that. Add `--sitemap-url` if the redirect rule will run on a different host than wherever this archive itself ends up (e.g. the old board's own subdomain redirecting to a bare-domain archive) — without it, redirect targets are relative and only work when both are on the same host.

### Fixing avatars the generator can't fetch on its own

Some avatars are remote URLs the generator can't reach (dead host, bot protection). Run `-m`/`--missing-avatars` first to see who's affected and get a starter file to fill in:

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/ -m
```

This writes `output/avatar_overrides.json` — a list of `{"user_id": ..., "file": "username.ext"}` entries. Point each `"file"` at a local image (paths are relative to the overrides file itself), then pass it on a normal run:

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/ --avatar-overrides output/avatar_overrides.json
```

### Leaving specific forums out of the archive

`-l`/`--list-forums` prints every forum/category with its `forum_id`, indented to show nesting — use it to find the id(s) you want hidden:

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/ -l
```

Then list them in an exclude file (excluding a category pulls in everything under it automatically — no need to list its children too):

```json
{"categories": [52], "forums": []}
```

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/ --exclude exclude.json
```

Excluded forums, their topics, and any attachment or external image that's only ever used inside them are never written to `output/` at all — not just unlinked.

### Password-protected forums

Any forum with a phpBB access password set (`forum_password` in the dump) is excluded automatically, the same as `--exclude` above — a static archive has no login to gate access behind, so leaving it publicly readable would defeat the point of the password. Run `-l`/`--list-forums` to see which forum_id(s) got auto-excluded this way (they show up the same as any other forum; check the generator's log output for which ones were password-protected).

If you know the real password and want to archive that forum anyway, list its id(s) in a `--password-override` file:

```json
{"forums": [42]}
```

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/ --password-override password_override.json
```

### Mirroring blocked or dead external images

If a source site blocks the generator (Cloudflare, robots rules) but you have direct file access to it, `--url-mirrors` lets you point specific URL prefixes at a local directory instead of hitting the network:

```json
{"http://example.com/images/": "/path/to/local/mirror"}
```

Run `-i`/`--check-images` first to see which URLs currently fail to resolve, so you know what's worth mirroring, before committing to a full run.

### Checking for broken internal links

`-l`/`-m`/`-i` inspect the dump before you generate; `-c`/`--check-links` inspects the archive after — it needs a real `output/` to scan, so run it once you have a build:

```bash
./run.sh
.venv/bin/python -m generator.generate --output output/ -c
```

Walks every generated page's `href`/`src` attributes and flags two things: a link to a file that doesn't exist, and a `#anchor` link (e.g. `topics/106.html#p53377`, a permalink to one specific post) whose target file exists but doesn't actually contain that anchor — the more useful of the two, since a plain missing-page link is easy to spot by eye, but a stale anchor pointing at a post that got excluded or never recovered isn't. External URLs aren't touched here; that's `--ignore-hosts`/`--url-mirrors`'s job. Results go to `output/broken_links.json`. Running it against this deployment's own archive found nothing beyond a handful of pre-existing data quirks (a real but nonsensical `href` value inside a quoted phpBB source-code example, and a few spam posts missing a proper `http://` prefix) — no actual archive-navigation bugs.

### Recognizing this board across domain changes

A post linking back to phpbbmodders' own board only gets rewritten into a relative in-archive link when the link's own host is actually recognized as this board — a topic id alone isn't enough, since it's just a small integer that another phpBB install entirely (confirmed here: real links to `www.phpbb.com`, `rmcgirr83.org`, and dozens more) can just as easily reuse for a completely different topic. This board has really been reachable at three different domains over its lifetime, so `run.sh` passes [`config/board_hosts.json`](config/board_hosts.json) listing all three (`phpbbmodders.net`/`.com`/`.org`) alongside the dump's own `phpbb_config.server_name`.

rmcgirr83.org's own community later merged into this board, but its domain is deliberately *not* in that list even though its links are common in the dump: its own historical topic ids aren't confirmed to have survived the merge unchanged, so treating it as internal would risk rewriting a link to whatever unrelated topic now happens to share that id here. A link to it stays a normal (still working) external link.

### Custom color scheme

The archive's own layout ships with a neutral default palette. `--style-css` swaps it for any stylesheet you point at — this deployment uses [`config/phpbbmodders-style.css`](config/phpbbmodders-style.css), colors approximating the live board's own theme.

Every generated page links `assets/style.css` rather than inlining it, so re-theming an already-built archive later is just dropping a new `assets/style.css` into its `output/` (or wherever it's deployed) — no regeneration required.

`--theme dark` swaps the built-in neutral palette for a dark one without a custom `--style-css`; has no effect here since this deployment already uses one.

### Favicon and board logo

Neither phpBBModders' favicon nor its logo lives anywhere in the SQL dump, so `run.sh` fetches both live: `--favicon-url https://phpbbmodders.net/favicon.ico` and `--logo-url https://www.phpbbmodders.com/modders-cog.gif`. A URL that can't be reached is skipped with a warning rather than failing the whole run — this matters in practice here, since `phpbbmodders.net`'s own copy of the logo is blocked by a Cloudflare JS challenge, while the `.com` copy isn't.

The logo's on-page size uses this board's own configured `sitelogo_width`/`sitelogo_height` (84×80) rather than the source file's raw pixel dimensions (324×308) or a flat CSS cap — `--logo-natural-size` would show it unscaled at that raw size instead, which `run.sh` doesn't pass.

### Board-wide announcement

`--announcement` shows a notice on the index, every forum page, and every topic page — written fresh for the archive (e.g. "this board is now read-only"), not pulled from the dump. This deployment's text lives in [`config/announcement.txt`](config/announcement.txt); an empty file (the default) renders nothing.

### Sitemap and robots.txt

`--sitemap-url` writes `output/sitemap.xml` (index, every forum, every topic — each with a `<lastmod>` from its most recent post) and `output/robots.txt` pointing at it. This deployment uses `https://phpbbmodders.net/`, its real hosting URL.

Every other link the archive generates is relative, so it works at any path — but sitemap entries have to be absolute URLs, which is why this flag needs the full deployment URL rather than inferring it. Excluded forums/topics are already left out of `output/` entirely, so they're never in the sitemap either.

Every topic page also carries Open Graph and Twitter Card meta tags (title, description from the opening post, site name) unconditionally, so a shared link shows a real preview instead of nothing. `og:url` is the one tag that needs an absolute URL, so it only appears with `--sitemap-url` set (which `run.sh` already does) — everything else works regardless.

### Full-text search

`--search` adds `search.html` (linked from every page's header) and indexes every generated page with [Pagefind](https://pagefind.app/), a static client-side search engine — no server, no external service, same self-contained philosophy as the rest of the archive. This deployment's `run.sh` already passes it.

Requires the `pagefind[bin]` package (already in `generator/requirements.txt`) — it ships a real compiled search binary via pip, no Node.js needed. The generator runs it as a subprocess after every other page is written, so search results always reflect the current run.

Result titles come from a `data-pagefind-meta="title:..."` attribute the archive sets on every page's `<body>` — without it, Pagefind defaults to each page's first `<h1>`, which on this archive is always just the site name, making every search result look identical. `search.html` itself is excluded from the index (`data-pagefind-ignore`) since it has no content of its own, just the search widget. The widget's colors come from the same palette as everything else — see the `#search` block in [`config/phpbbmodders-style.css`](config/phpbbmodders-style.css).

**Testing locally, `search.html` must be served over `http://`/`https://`, not opened as a `file://` path.** Pagefind's engine can't fetch its own index under `file://` — the query box will accept input and show "Searching for…" but never return results, with no error shown anywhere. Any static file server works for testing, e.g. `python3 -m http.server` from inside `output/`; the real deployment at `https://phpbbmodders.net/` is served over HTTPS anyway, so this only matters when checking the archive locally before publishing it.

## What gets generated

```
output/
├── index.html          # Board index
├── forums/<id>.html    # One page per forum (topic list)
├── topics/<id>.html    # One page per thread (25 posts/page; page 2+ is <id>-pN.html)
├── users/<id>.html     # User profile pages
├── assets/              # CSS, images, smilies, avatars, attachments, logo
├── favicon.<ext>         # Only with --favicon/--favicon-url
├── sitemap.xml           # Only with --sitemap-url
├── robots.txt            # Only with --sitemap-url
├── search.html            # Only with --search
└── pagefind/               # Only with --search — search index and widget
```

All links are relative, so the archive works at any path — subdirectory, GitHub Pages project site, or offline from disk.

## How it works

- Converts the MySQL dump to SQLite (pure Python, no MySQL client needed)
- Reads the board name from `phpbb_config` — no manual title flag required
- Queries phpBB tables: forums, topics, posts, users, attachments, smilies, ranks
- Parses phpBB's UID-annotated BBCode into HTML (custom parser — generic BBCode libraries don't handle phpBB's format)
- Copies assets from `dump/` and rewrites CSS paths for static hosting
- Renders Jinja2 templates into static HTML
