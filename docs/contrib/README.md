# Contributed examples

Real-world starting points for the generator's optional `--ignore-hosts`, `--style-css`, and `--announcement` flags — copy one, drop the `.example` suffix, and edit it for your own board. See the main [README](../../README.md) for full flag documentation.

| File | Pairs with | What it is |
|------|-----------|------------|
| [`known-dead-hosts.json.example`](known-dead-hosts.json.example) | `--ignore-hosts` | Image/avatar hosts that were confirmed dead (parked domains, shut-down services) during a real archive run — hostnames worth skipping without a network attempt on any board. |
| [`phpbbmodders-style.css.example`](phpbbmodders-style.css.example) | `--style-css` | A complete custom stylesheet, sourced from a live board's actual `prosilver` child theme rather than guessed — a worked example of what a `--style-css` file looks like end to end. |
| [`announcement.txt.example`](announcement.txt.example) | `--announcement` | A short BBCode notice ("this board is now a read-only archive") — a worked example of the plain-text-BBCode format `--announcement` expects. |
| [`run.sh.example`](run.sh.example) | all of them | phpbbmodders.net's actual wrapper script, wiring every flag above (plus `--avatar-overrides`, `--url-mirrors`, `--attachment-recovery`, `--exclude`, `--incremental`) into one command. Real paths from a real deployment, not a generic template — copy it and adjust the paths for your own board. |

Usage:

```bash
cp docs/contrib/known-dead-hosts.json.example known-dead-hosts.json
.venv/bin/python -m generator.generate --dump dump/ --output output/ --ignore-hosts known-dead-hosts.json
```

`--style-css` and `--announcement` don't require copying first — point them straight at the example, or your own edited copy:

```bash
.venv/bin/python -m generator.generate --dump dump/ --output output/ \
    --style-css docs/contrib/phpbbmodders-style.css.example \
    --announcement docs/contrib/announcement.txt.example
```
