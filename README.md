# phpbb-archive — static site generator for phpBB forums

*2026-03-21T01:35:59Z by Showboat dev*
<!-- showboat-id: b6c8bc4e-4e66-401b-a9ec-f90ddce627fe -->

phpbb-archive converts a phpBB MySQL dump into a self-contained static HTML archive. No server required — the output is plain files you can host anywhere (GitHub Pages, Neocities, nginx, etc.).

## What you need

From your phpBB server, collect the following into a `dump/` directory:

| File/dir | How to get it |
|----------|--------------|
| `*.sql` | `mysqldump -u USER -p DATABASE > dump/forum.sql` |
| `config.php` | Copy from your phpBB installation root |
| `files/` | Attachments directory (copy whole dir) |
| `images/` | Avatars, smilies, rank images (copy whole dir) |
| `styles/` | Theme CSS and images (copy whole dir) |

The generator auto-discovers the `.sql` file in `dump/` and reads the board name directly from the database — no flags needed.

## Quick start

Install the single dependency (Jinja2):

```bash
python3 -m venv .venv
.venv/bin/pip install jinja2
```

Then run the generator:

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/
```

That's it. Open `output/index.html` in a browser to browse the archive.

## Usage

```bash
.venv/bin/python -m generator.generate --help
```

```output
usage: python -m generator.generate [-h] [--dump DUMP] [--output OUTPUT]

Generate a static HTML archive from a phpBB MySQL dump

options:
  -h, --help       show this help message and exit
  --dump DUMP      Path to dump/ directory
  --output OUTPUT  Path to output/ directory
```

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

1. **Convert** the MySQL dump to SQLite (pure Python, no MySQL client needed)
2. **Read** the board name from `phpbb_config` — no manual title flag required
3. **Query** phpBB tables: forums, topics, posts, users, attachments, smilies, ranks
4. **Parse** phpBB's UID-annotated BBCode into HTML (custom parser — generic BBCode libraries don't handle phpBB's format)
5. **Copy** assets from `dump/` and rewrite CSS paths for static hosting
6. **Render** Jinja2 templates into static HTML

## Requirements

- Python 3.10+
- Jinja2 (`pip install jinja2`)
- A phpBB 3.x database dump and associated files
