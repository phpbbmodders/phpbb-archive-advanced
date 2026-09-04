# phpbb-archive — static site generator for phpBB forums

phpbb-archive converts a phpBB 3.x MySQL dump into a self-contained static HTML archive. No server required — the output is plain files you can host anywhere (GitHub Pages, Neocities, nginx, etc.).

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

Attachments, avatars, and `[img]`-tagged images that are missing or fail to decode are dropped from the archive rather than left as broken links. Attachments/avatars/external images that only exist as external URLs (remote avatars, hotlinked signature images) are downloaded once and cached locally so the archive stays self-contained — a URL that's genuinely dead just gets skipped, and is retried again on every future run (see `--incremental` below if that's not what you want).

## Usage

```bash
.venv/bin/python -m generator.generate --help
```

```output
usage: generate.py [-h] [--dump DUMP] [--output OUTPUT]
                   [--avatar-overrides FILE] [-m] [--exclude FILE] [-l]
                   [--url-mirrors FILE] [-i] [--incremental]

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
  --url-mirrors FILE    JSON file of {"url_prefix": "local_dir"} mappings. Any
                        external [img]/avatar URL starting with a prefix is
                        looked up in the matching local directory first,
                        instead of being fetched over the network — useful
                        when a source site blocks the generator (e.g.
                        Cloudflare) but you have direct filesystem access to
                        its files.
  -i, --check-images    List external [img]/<IMG> URLs that fail to resolve
                        (via --url-mirrors or the network), instead of
                        generating the archive — use ahead of a full run to
                        see what needs mirroring
  --incremental         Keep previously-downloaded
                        attachments/avatars/external images instead of re-
                        fetching everything — only failed URLs are retried.
                        Generated pages are still rebuilt fresh every run. Off
                        by default.
```

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

### Mirroring blocked or dead external images

If a source site blocks the generator (Cloudflare, robots rules) but you have direct file access to it, `--url-mirrors` lets you point specific URL prefixes at a local directory instead of hitting the network:

```json
{"http://example.com/images/": "/path/to/local/mirror"}
```

Run `-i`/`--check-images` first to see which URLs currently fail to resolve, so you know what's worth mirroring, before committing to a full run.

## What gets generated

```
output/
├── index.html          # Board index
├── forums/<id>.html    # One page per forum (topic list)
├── topics/<id>.html    # One page per thread (all posts)
├── users/<id>.html     # User profile pages
└── assets/             # CSS, images, smilies, avatars, attachments
```

All links are relative, so the archive works at any path — subdirectory, GitHub Pages project site, or offline from disk.

## How it works

- Converts the MySQL dump to SQLite (pure Python, no MySQL client needed)
- Reads the board name from `phpbb_config` — no manual title flag required
- Queries phpBB tables: forums, topics, posts, users, attachments, smilies, ranks
- Parses phpBB's UID-annotated BBCode into HTML (custom parser — generic BBCode libraries don't handle phpBB's format)
- Copies assets from `dump/` and rewrite CSS paths for static hosting
- Renders Jinja2 templates into static HTML
