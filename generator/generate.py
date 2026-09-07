#!/usr/bin/env python3
"""phpBB static site generator.

Converts a phpBB MySQL dump into a self-contained static HTML archive.

Usage:
    .venv/bin/python -m generator.generate --dump dump/ --output output/
"""

import argparse
import datetime
import html
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import jinja2
from PIL import Image

from generator.db import import_mysql_dump, PhpbbDatabase
from generator.bbcode import PhpbbBBCodeParser, IMAGE_EXTENSIONS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_timestamp(ts: int | None) -> str:
    """Format a Unix timestamp as a human-readable date string (UTC)."""
    if not ts:
        return ""
    dt = datetime.datetime.utcfromtimestamp(ts)
    return dt.strftime("%a %b %d, %Y %H:%M UTC")


def read_table_prefix(config_path: Path) -> str:
    """Extract $table_prefix from phpBB's config.php."""
    try:
        content = config_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"\\\$table_prefix\s*=\s*'([^']+)'|\\$table_prefix\s*=\s*'([^']+)'", content)
        if not match:
            # Try without backslash escaping
            match = re.search(r"""\$table_prefix\s*=\s*['"]([^'"]+)['"]""", content)
        if match:
            return next(g for g in match.groups() if g) if match.lastindex and match.lastindex > 1 else match.group(1)
    except FileNotFoundError:
        pass
    logger.warning("Could not read table prefix from config.php, using 'phpbb_'")
    return "phpbb_"


def _read_style_cfg(style_dir: Path) -> dict[str, str]:
    """Parse a phpBB style.cfg file into a dict of key -> value, ignoring
    comments and blank lines."""
    cfg_path = style_dir / "style.cfg"
    result: dict[str, str] = {}
    if not cfg_path.exists():
        return result
    for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("'\"")
    return result


def resolve_default_style(db: PhpbbDatabase, dump: Path) -> dict:
    """Read phpbb_config.default_style, resolve it to a style_path under
    dump/styles/, and walk its style.cfg "parent" chain to determine
    whether it descends from prosilver. Informational only — does not
    change what CSS/templates get used."""
    info: dict = {"style_id": None, "style_path": None, "name": None,
                  "chain": [], "is_prosilver_family": False}

    style_id_raw = db.get_config("default_style")
    info["style_id"] = style_id_raw
    if not style_id_raw:
        logger.warning("No default_style found in phpbb_config")
        return info

    style_path = db.get_style_path(int(style_id_raw))
    info["style_path"] = style_path
    if not style_path:
        logger.warning("default_style id %s has no matching row in phpbb_styles", style_id_raw)
        return info

    seen: set[str] = set()
    current = style_path
    while current and current not in seen:
        seen.add(current)
        cfg = _read_style_cfg(dump / "styles" / current)
        name = cfg.get("name", current)
        parent = cfg.get("parent", "")
        info["chain"].append({"path": current, "name": name, "parent": parent})
        if not info["name"]:
            info["name"] = name
        if not parent or parent == current:
            break  # base style — no real parent (phpBB convention: parent == own name/path)
        current = parent

    info["is_prosilver_family"] = any(step["path"] == "prosilver" for step in info["chain"])

    logger.info(
        "Default style: %s (%s) — %s",
        info["name"], info["style_path"],
        "descends from prosilver" if info["is_prosilver_family"] else "NOT a prosilver descendant",
    )
    for step in info["chain"]:
        logger.info("  %s -> parent: %s", step["path"], step["parent"] or "(none)")

    return info


def load_exclusions(path: Path) -> set[int]:
    """Load a JSON file of forum/category IDs to exclude entirely from the
    archive: {"categories": [...], "forums": [...]}. This is just the seed
    set — expand_exclusions_recursively() pulls in every descendant too, so
    only the top-level thing you want hidden needs to be listed here."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(i) for i in data.get("categories", [])} | {int(i) for i in data.get("forums", [])}


def expand_exclusions_recursively(seed_ids: set[int], all_forums: list[dict]) -> set[int]:
    """Expand a set of forum/category IDs to include every descendant,
    recursively. Excluding a category always excludes everything under it —
    a forum can never end up "orphaned" (parent hidden, child still shown
    prominently at the top level), which is the wrong direction to fail in
    for a privacy feature."""
    children_by_parent: dict[int, list[int]] = {}
    for f in all_forums:
        children_by_parent.setdefault(f["parent_id"], []).append(f["forum_id"])

    result = set(seed_ids)
    stack = list(seed_ids)
    while stack:
        current = stack.pop()
        for child_id in children_by_parent.get(current, []):
            if child_id not in result:
                result.add(child_id)
                stack.append(child_id)
    return result


def avatar_url(user: dict, assets_prefix: str, bad_avatars: set[str] | None = None,
                remote_avatar_exts: dict[int, str] | None = None,
                avatar_overrides: dict[int, str] | None = None) -> str | None:
    """Return a relative URL for the user's avatar, or None if none (or if
    the file is missing/corrupted — see find_bad_avatars / download_remote_avatars).

    Remote avatars are downloaded and cached locally by download_remote_avatars()
    rather than linked externally, so the archive stays self-contained; a dead
    or undecodable remote URL is treated the same as having no avatar.

    avatar_overrides (see load_avatar_overrides/apply_avatar_overrides) takes
    priority over everything else — a manually supplied local image for a
    user whose real avatar the generator can't fetch or decode on its own
    (e.g. blocked by Cloudflare, or a dead host).
    """
    ext = (avatar_overrides or {}).get(user.get("user_id"))
    if ext:
        return f"{assets_prefix}/avatars/{user['user_id']}.{ext}"

    avatar = user.get("user_avatar", "")
    kind = user.get("user_avatar_type", "")
    if not avatar or avatar == "noavatar":
        return None
    # IPB migration artifact (e.g. "upload:av-26.png?1715977904") — can't recover file
    if avatar.startswith("upload:"):
        return None
    # Locally uploaded file: DB stores "{userid}_{timestamp}.ext", we copy as "{userid}.ext"
    if kind == "avatar.driver.upload":
        m = re.match(r"(\d+)_\d+\.(\w+)$", avatar)
        if m:
            name = f"{m.group(1)}.{m.group(2)}"
            if bad_avatars and name in bad_avatars:
                return None
            return f"{assets_prefix}/avatars/{name}"
        return None
    if kind == "avatar.driver.remote":
        ext = (remote_avatar_exts or {}).get(user.get("user_id"))
        if not ext:
            return None
        return f"{assets_prefix}/avatars/{user['user_id']}.{ext}"
    if kind == "avatar.driver.local":
        # DB stores the gallery-relative path directly, e.g. "phpbb/gear_red.png"
        if bad_avatars and avatar in bad_avatars:
            return None
        return f"{assets_prefix}/avatars/gallery/{avatar}"
    return None


def _rewrite_css_imports(css_path: Path) -> None:
    """Strip ?hash=... query strings from @import URLs for static hosting."""
    content = css_path.read_text(encoding="utf-8", errors="replace")
    # url("file.css?hash=abc") → url("file.css")
    content = re.sub(r'(url\(["\']?[^"\')]+?)\?[^"\')]*(["\']?\))', r'\1\2', content)
    css_path.write_text(content, encoding="utf-8")


def copy_avatars(dump_dir: Path, output_dir: Path) -> None:
    """Copy uploaded avatars and the avatar gallery into output/assets/avatars/.
    Split out of copy_assets() so a diagnostic mode that only needs to check
    avatar resolution (-m/--missing-avatars) doesn't also have to copy — and
    so risk silently overwriting — everything else copy_assets() touches
    (style.css, favicon, logo, theme CSS, smilies, ranks, attachments) in an
    --output directory that may already hold a real, customized deployment.
    See phpbb-archive security review, finding 14."""
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    # Disk: {hash}_{userid}.ext  DB: {userid}_{timestamp}.ext  Target: {userid}.ext
    avatars_src = dump_dir / "images" / "avatars" / "upload"
    if avatars_src.exists():
        dest = assets / "avatars"
        dest.mkdir(exist_ok=True)
        for f in avatars_src.iterdir():
            if not f.is_file():
                continue
            parts = f.stem.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                shutil.copy2(f, dest / f"{parts[1]}{f.suffix}")
        logger.info("Copied avatars")

    # Avatar gallery (avatar.driver.local) — DB stores the path relative to
    # this directory, e.g. "phpbb/gear_red.png".
    gallery_src = dump_dir / "images" / "avatars" / "gallery"
    if gallery_src.exists():
        dest = assets / "avatars" / "gallery"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(gallery_src, dest, dirs_exist_ok=True)
        logger.info("Copied avatar gallery")


def copy_assets(dump_dir: Path, output_dir: Path, excluded_physical_filenames: set[str] | None = None,
                 style_css_path: str | None = None,
                 physical_to_real: dict[str, str] | None = None,
                 favicon_path: str | None = None, logo_path: str | None = None,
                 theme: str = "light") -> None:
    """Copy CSS, images, smilies, avatars, and attachments into output/assets/.
    Attachments in excluded_physical_filenames (see load_exclusions) are
    skipped entirely rather than copied and left unlinked."""
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    # --- Archive's own stylesheet ---
    # Every page links assets/style.css rather than inlining it, so
    # re-theming an already-built archive is a matter of dropping in a
    # new assets/style.css — no regeneration required. Defaults to the
    # tool's neutral built-in palette (light, or dark via --theme);
    # --style-css overrides both with a site-specific one (e.g. colors
    # approximating a live board's own theme) without changing anyone
    # else's default output — --theme has no effect once --style-css is
    # given, since a custom stylesheet is its own fixed palette.
    if style_css_path:
        own_style = Path(style_css_path)
    else:
        filename = "style-dark.css" if theme == "dark" else "style.css"
        own_style = Path(__file__).parent / "static" / filename
    shutil.copy2(own_style, assets / "style.css")

    # --- Favicon / board logo (both optional) ---
    # Kept as whatever format the source file already is (ico/png/svg/...)
    # rather than forcing a conversion; the template links to it by that
    # same extension (env.globals["favicon_ext"]/["logo_ext"], set in
    # generate()).
    if favicon_path:
        favicon_src = Path(favicon_path)
        shutil.copy2(favicon_src, output_dir / f"favicon{favicon_src.suffix}")
    if logo_path:
        logo_src = Path(logo_path)
        shutil.copy2(logo_src, assets / f"logo{logo_src.suffix}")

    # --- CSS from prosilver theme ---
    # Keep CSS files at assets/ root (not assets/css/) so that their
    # ./images/... relative references resolve correctly.
    theme_dir = dump_dir / "styles" / "prosilver" / "theme"
    if theme_dir.exists():
        for css_file in theme_dir.glob("*.css"):
            dest = assets / css_file.name
            shutil.copy2(css_file, dest)
        _rewrite_css_imports(assets / "stylesheet.css")
        logger.info("Copied prosilver CSS (%d files)", len(list(theme_dir.glob("*.css"))))
    else:
        logger.warning("prosilver theme not found at %s", theme_dir)

    # --- Theme images (CSS expects ./images/ relative to CSS dir = assets/) ---
    theme_images = theme_dir / "images"
    if theme_images.exists():
        dest = assets / "images"
        dest.mkdir(exist_ok=True)
        shutil.copytree(theme_images, dest, dirs_exist_ok=True)
        logger.info("Copied theme images")

    # --- Smilies ---
    smilies_src = dump_dir / "images" / "smilies"
    if smilies_src.exists():
        dest = assets / "images" / "smilies"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(smilies_src, dest, dirs_exist_ok=True)
        logger.info("Copied smilies")

    # --- Rank images ---
    ranks_src = dump_dir / "images" / "ranks"
    if ranks_src.exists():
        dest = assets / "images" / "ranks"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ranks_src, dest, dirs_exist_ok=True)
        logger.info("Copied rank images")

    copy_avatars(dump_dir, output_dir)

    # --- Attachments ---
    # Some dumps end up with attachment files duplicated inside stray nested
    # subdirectories (e.g. dump/files/files/, dump/files/files/files/) from
    # how they were originally collected; flatten by basename instead of
    # preserving the dump's directory structure, keeping the shallowest
    # copy on a name collision. Each file is copied into its own
    # subdirectory named after its physical_filename (guaranteed unique),
    # containing a single file named after its real_filename — so the
    # served URL ends in the attachment's real name (e.g.
    # assets/attachments/1706_79dc.../mchat_deathwing.gif) rather than the
    # meaningless physical hash, and "Save Image/Link As" both save under
    # the right name without needing the `download` attribute. Falls back
    # to the old flat layout (assets/attachments/<physical_filename>) for
    # a physical_filename with no known real_filename (shouldn't normally
    # happen — every attachment file is referenced by a DB row).
    files_src = dump_dir / "files"
    if files_src.exists():
        dest = assets / "attachments"
        dest.mkdir(exist_ok=True)
        copied = 0
        # Remove anything now excluded that an earlier --incremental run
        # already copied before it became excluded (a newly-added
        # exclusion, or an attachment that turned out to belong to a
        # private message) — --incremental otherwise only ever adds
        # files here, never revisits ones already on disk.
        removed = 0
        for name in excluded_physical_filenames or ():
            target = dest / name
            if target.is_dir():
                shutil.rmtree(target)
                removed += 1
            elif target.exists():
                target.unlink()
                removed += 1
        if removed:
            logger.info("Removed %d previously-copied attachment(s) now excluded", removed)
        # dump/files/ also holds phpBB's own auto-generated
        # thumb_<attach_id>_<physical_filename> companion files for image
        # attachments — a separate concept from the attachment itself,
        # never referenced by its own name in phpbb_attachments (and this
        # archive never links to a thumbnail; it always serves the full
        # image directly). Not in physical_to_real means it isn't a real
        # attachment row at all — confirmed real: 1,498 such files sitting
        # in this dump, a meaningful fraction of them already
        # corrupted/truncated on top of being unreferenced dead weight.
        # Same --incremental blind spot as the exclusion cleanup above: a
        # prior run's already-copied junk is never revisited on its own.
        stale_junk = 0
        if dest.is_dir():
            for entry in dest.iterdir():
                if entry.name in (physical_to_real or {}):
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                stale_junk += 1
        if stale_junk:
            logger.info("Removed %d previously-copied non-attachment file(s) (e.g. phpBB's own thumbnails)", stale_junk)
        # A physical_filename can have more than one on-disk copy across
        # the stray nesting levels (e.g. a copy under dump/files/ and
        # another, separately collected, under dump/files/files/) — os.walk
        # visits shallower directories first, so collect every copy before
        # choosing rather than committing to whichever happens to be found
        # first. Some of these turn out to have been collected at
        # different times from different sources and don't share the same
        # corruption; picking blindly could mean using a broken copy when
        # a good one of the very same file was sitting one level deeper.
        candidates: dict[str, list[Path]] = {}
        for dirpath, _dirnames, filenames in os.walk(files_src):
            for name in filenames:
                if excluded_physical_filenames and name in excluded_physical_filenames:
                    continue
                if name not in (physical_to_real or {}):
                    continue
                candidates.setdefault(name, []).append(Path(dirpath) / name)

        recovered_from_nesting = 0
        for name, paths in candidates.items():
            real = (physical_to_real or {}).get(name)
            if real:
                # real_filename comes straight from the dump's own
                # database — an attacker-crafted or corrupted dump could
                # put path separators (e.g. "../../etc/x") in it, which a
                # bare Path join would honor literally at copy time,
                # writing outside assets/attachments/ entirely. Reduce to
                # a bare basename; a filename that collapses to nothing
                # (".", "..", or empty) falls back to the flat
                # physical-name layout below.
                real = os.path.basename(real)
                if real in ("", ".", ".."):
                    real = None
            target = dest / name / real if real else dest / name
            if target.exists():
                continue
            # Prefer the shallowest copy (paths is already in that order);
            # fall back to the first later copy that actually validates
            # for its type (see _attachment_is_valid) if the shallow one
            # doesn't. A real_filename is required to check type at all —
            # without one there's nothing to compare against, so this
            # just keeps the original shallowest-copy behavior.
            chosen = paths[0]
            if real and len(paths) > 1 and not _attachment_is_valid(chosen, real):
                for alt in paths[1:]:
                    if _attachment_is_valid(alt, real):
                        chosen = alt
                        recovered_from_nesting += 1
                        break
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(chosen, target)
            copied += 1
        if recovered_from_nesting:
            logger.info("Recovered %d attachment(s) using a valid copy from a deeper nested duplicate", recovered_from_nesting)
        logger.info("Copied attachments (%d files)", copied)


def build_attachments_map(db: PhpbbDatabase, post_ids: list[int]) -> dict[int, list[dict]]:
    """Return {post_id: [attachment, ...]} for a set of post IDs."""
    result: dict[int, list[dict]] = {}
    for pid in post_ids:
        rows = db.get_attachments(pid)
        if rows:
            result[pid] = rows
    return result


def _attachment_is_valid(path: Path, real_filename: str) -> bool:
    """True if path decodes cleanly for its apparent type: image via
    Pillow, zip integrity via the zipfile module, rar integrity via the
    system `7z` binary if present (testzip()/`7z t` both catch the "valid
    archive data followed by trailing garbage" shape actually seen in a
    real dump — 205 of 209 .zip and 24 of 24 .rar attachments failed this
    check, and 92% of each had a genuinely working copy sitting in the
    --attachment-recovery backup the whole time). Any other extension, or
    .rar without `7z` on PATH, is assumed valid, since nothing here can
    meaningfully check it."""
    lower = real_filename.lower()
    if lower.endswith(IMAGE_EXTENSIONS):
        try:
            with Image.open(path) as im:
                im.load()
            return True
        except Exception:
            return False
    if lower.endswith(".zip"):
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                return z.testzip() is None
        except Exception:
            return False
    if lower.endswith(".rar"):
        import shutil as _shutil
        if not _shutil.which("7z"):
            return True
        try:
            result = subprocess.run(["7z", "t", str(path)], capture_output=True, text=True, timeout=30)
            return "Everything is Ok" in result.stdout
        except Exception:
            return False
    return True


def find_bad_attachments(db: PhpbbDatabase, out: Path, excluded_physical_filenames: set[str] | None = None) -> dict[str, str]:
    """Return {physical_filename: real_filename} for image/zip/rar attachments
    that are missing from assets/attachments/ or fail to validate for
    their type (see _attachment_is_valid). These get dropped from post
    bodies instead of rendered as a broken link, unless
    --attachment-recovery finds a working copy. excluded_physical_filenames
    (private-message and excluded-forum attachments — see
    _open_db_and_copy_assets) are skipped entirely rather than reported as
    "bad": they were never copied to assets/ on purpose, so treating that
    absence as corruption would let --attachment-recovery undo the
    exclusion by restoring a working copy from the recovery backup."""
    attachments_dir = out / "assets" / "attachments"
    excluded = excluded_physical_filenames or set()
    bad: dict[str, str] = {}
    checked = 0
    for att in db.get_all_attachments():
        if att["physical_filename"] in excluded:
            continue
        real = att["real_filename"]
        lower = real.lower()
        if not (lower.endswith(IMAGE_EXTENSIONS) or lower.endswith((".zip", ".rar"))):
            continue
        checked += 1
        path = attachments_dir / att["physical_filename"] / real
        if not path.exists() or not _attachment_is_valid(path, real):
            bad[att["physical_filename"]] = real
    if bad:
        logger.warning("Dropping %d of %d image/zip/rar attachments (missing or corrupted)", len(bad), checked)
    return bad


def recover_bad_attachments(bad: dict[str, str], recovery_dir: Path, out: Path) -> set[str]:
    """For each physical_filename -> real_filename in `bad` (see
    find_bad_attachments), check recovery_dir for a same-named copy that
    validates cleanly — e.g. a backup collected separately from the main
    dump/files/ that turns out not to share the same corruption. A
    working copy is copied over the broken one in assets/attachments/ and
    left out of the returned still-bad set; anything not found or still
    broken there stays in it."""
    if not bad:
        return set()
    dest_dir = out / "assets" / "attachments"
    recovered = 0
    still_bad = set(bad.keys())
    for name, real in bad.items():
        candidate = recovery_dir / name
        if not candidate.exists() or not _attachment_is_valid(candidate, real):
            continue
        target = dest_dir / name / real
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)
        still_bad.discard(name)
        recovered += 1
    if recovered:
        logger.info("Recovered %d of %d attachments from %s", recovered, len(bad), recovery_dir)
    return still_bad


def find_bad_avatars(users: list[dict], out: Path) -> set[str]:
    """Return avatar keys that are missing from assets/avatars/ or fail to
    decode: "{userid}.{ext}" for avatar.driver.upload, or the gallery-relative
    path (e.g. "phpbb/gear_red.png") for avatar.driver.local. Mirrors the
    image-decode half of find_bad_attachments."""
    avatars_dir = out / "assets" / "avatars"
    bad: set[str] = set()
    checked = 0
    for user in users:
        kind = user.get("user_avatar_type")
        avatar = user.get("user_avatar", "")
        if kind == "avatar.driver.upload":
            m = re.match(r"(\d+)_\d+\.(\w+)$", avatar)
            if not m:
                continue
            key = f"{m.group(1)}.{m.group(2)}"
            path = avatars_dir / key
        elif kind == "avatar.driver.local":
            if not avatar:
                continue
            key = avatar
            path = avatars_dir / "gallery" / avatar
        else:
            continue

        checked += 1
        if not path.exists():
            bad.add(key)
            continue
        try:
            with Image.open(path) as im:
                im.load()
        except Exception:
            bad.add(key)
    if bad:
        logger.warning("Dropping %d of %d avatars (missing or corrupted)", len(bad), checked)
    return bad


def find_bad_smilies(smilies: list[dict], out: Path) -> set[str]:
    """Return smiley_url filenames that are missing from
    assets/images/smilies/ or fail to decode. Mirrors the image-decode
    half of find_bad_attachments/find_bad_avatars; unlike those, smilies
    come from phpBB's own distributed image pack rather than user
    uploads, so this exists for robustness on other dumps rather than a
    problem actually seen in any real one checked so far."""
    smilies_dir = out / "assets" / "images" / "smilies"
    bad: set[str] = set()
    checked_filenames: set[str] = set()
    for s in smilies:
        filename = s.get("smiley_url", "")
        if not filename or filename in checked_filenames:
            continue
        checked_filenames.add(filename)
        path = smilies_dir / filename
        if not path.exists():
            bad.add(filename)
            continue
        try:
            with Image.open(path) as im:
                im.load()
        except Exception:
            bad.add(filename)
    if bad:
        logger.warning("Dropping %d of %d smilies (missing or corrupted)", len(bad), len(checked_filenames))
    return bad


_IMAGE_FORMAT_EXT = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "BMP": "bmp", "WEBP": "webp"}


def _image_ext(im: "Image.Image") -> str | None:
    """Map a Pillow-detected image format to a file extension."""
    return _IMAGE_FORMAT_EXT.get(im.format, (im.format or "").lower() or None)


def load_url_mirrors(path: Path) -> dict[str, Path]:
    """Load a JSON file of {"url_prefix": "local_dir"} mappings. Any
    external image URL starting with a prefix is resolved against
    <local_dir>/<rest of the URL> instead of being fetched over the
    network — useful when a source site blocks the generator (e.g.
    Cloudflare bot protection) but you have direct filesystem access to
    its files. "local_dir" paths are resolved relative to this mirrors
    file's own directory unless already absolute."""
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    result = {}
    for prefix, local_dir in data.items():
        local_path = Path(local_dir)
        if not local_path.is_absolute():
            local_path = base / local_path
        result[prefix] = local_path
    return result


def load_ignored_hosts(path: Path) -> set[str]:
    """Load a JSON array of hostnames (e.g. "tinypic.com") to skip without
    ever attempting a network request. Matches the host itself and any
    subdomain (so "tinypic.com" also covers "i28.tinypic.com"). Useful for
    hosts you already know are permanently gone, so --incremental doesn't
    keep paying their timeout on every future run."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(h).lower().lstrip(".") for h in data}


def load_board_hosts(path: Path) -> set[str]:
    """Load a JSON array of extra hostnames (e.g. "phpbbmodders.org") this
    board is also known to have been reachable at, in addition to the
    dump's own phpbb_config.server_name — a real board's domain can
    change over its lifetime, and only the current one is ever in the
    dump. Used by internal-link rewriting to recognize a same-board
    viewtopic.php link regardless of which of its own domains a given
    post happened to use when it was written."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(h).lower() for h in data}


def _fetch_image(url: str, timeout: int = 10, url_mirrors: dict[str, Path] | None = None,
                  ignored_hosts: set[str] | None = None) -> tuple[bytes, str] | None:
    """Resolve a URL to (raw_bytes, extension): first via any matching
    local mirror directory (see load_url_mirrors), then skipping known-dead
    hosts (see load_ignored_hosts) without a network attempt, falling back
    to an actual network download. Returns None if unreachable/undecodable
    either way."""
    for prefix, local_dir in (url_mirrors or {}).items():
        if not url.startswith(prefix):
            continue
        # The URL suffix comes from a post's own [img]/<IMG> content — a
        # crafted "../../etc/x" suffix would otherwise resolve outside
        # local_dir entirely and read whatever's there instead. Resolve
        # both sides and require containment before touching the
        # filesystem.
        local_path = (local_dir / url[len(prefix):]).resolve()
        resolved_dir = local_dir.resolve()
        if not (local_path == resolved_dir or resolved_dir in local_path.parents):
            logger.warning("Mirror path for %s escapes %s, skipping: %s", url, local_dir, local_path)
            continue
        if not local_path.exists():
            continue
        try:
            with Image.open(local_path) as im:
                im.load()
                ext = _image_ext(im)
        except Exception as e:
            logger.warning("Mirrored file for %s failed to decode: %s (%s)", url, local_path, e)
            continue
        if ext:
            return local_path.read_bytes(), ext

    import urllib.error
    import urllib.parse
    import urllib.request
    from io import BytesIO

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if ignored_hosts and host and any(host == h or host.endswith(f".{h}") for h in ignored_hosts):
        logger.warning("Image URL host is on the ignore list, skipping: %s", url)
        return None

    def _try(fetch_url: str) -> tuple[bytes, str] | None:
        # A realistic browser UA + same-site Referer clears basic hotlink
        # protection on otherwise-public images (e.g. Cloudflare's default
        # bot rules) without attempting to defeat real access controls —
        # nothing here handles auth, CAPTCHAs, or private/signed URLs.
        fetch_parsed = urllib.parse.urlparse(fetch_url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": f"{fetch_parsed.scheme}://{fetch_parsed.netloc}/",
        }
        try:
            req = urllib.request.Request(fetch_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        except (urllib.error.URLError, OSError) as e:
            logger.warning("Image URL unreachable: %s (%s)", fetch_url, e)
            return None
        try:
            with Image.open(BytesIO(data)) as im:
                im.load()
                ext = _image_ext(im)
        except Exception as e:
            logger.warning("Image URL failed to decode: %s (%s)", fetch_url, e)
            return None
        if not ext:
            return None
        return data, ext

    result = _try(url)
    if result is None and host and (host == "postimg.org" or host.endswith(".postimg.org")):
        # postimg.org itself is a parked/dead domain, but postimg.cc — the
        # service's current domain — still serves the same images under the
        # same subdomain and path (verified against real old postimg.org
        # links from this dump, not assumed).
        new_host = "postimg.cc" if host == "postimg.org" else host[: -len("postimg.org")] + "postimg.cc"
        new_netloc = new_host if not parsed.port else f"{new_host}:{parsed.port}"
        rewritten = urllib.parse.urlunparse(parsed._replace(netloc=new_netloc))
        logger.warning("Retrying dead postimg.org URL against postimg.cc: %s", rewritten)
        result = _try(rewritten)
    return result


def download_remote_avatars(users: list[dict], out: Path, url_mirrors: dict[str, Path] | None = None,
                             ignored_hosts: set[str] | None = None) -> dict[int, str]:
    """Download avatar.driver.remote avatars into assets/avatars/{userid}.{ext}
    so the archive stays self-contained instead of hotlinking the original
    site. Returns {user_id: ext} for avatars that downloaded and decoded
    successfully; a dead URL or undecodable response is skipped (treated the
    same as having no avatar) rather than left as a broken external link."""
    remote_users = [
        u for u in users
        if u.get("user_avatar_type") == "avatar.driver.remote" and u.get("user_avatar", "").startswith(("http://", "https://"))
    ]
    cached: dict[int, str] = {}
    if not remote_users:
        return cached

    dest_dir = out / "assets" / "avatars"
    dest_dir.mkdir(parents=True, exist_ok=True)
    skipped = 0
    for user in remote_users:
        existing = next(dest_dir.glob(f"{user['user_id']}.*"), None)
        if existing is not None:
            # Already cached from a previous run (see generate()'s
            # --incremental, which preserves assets/ across runs).
            cached[user["user_id"]] = existing.suffix.lstrip(".")
            skipped += 1
            continue
        result = _fetch_image(user["user_avatar"], url_mirrors=url_mirrors, ignored_hosts=ignored_hosts)
        if result is None:
            continue
        data, ext = result
        (dest_dir / f"{user['user_id']}.{ext}").write_bytes(data)
        cached[user["user_id"]] = ext

    logger.info("Cached %d of %d remote avatars (%d already cached, %d newly fetched)",
                len(cached), len(remote_users), skipped, len(cached) - skipped)
    return cached


def load_avatar_overrides(path: Path) -> list[dict]:
    """Load a JSON array of {"user_id": int, "file": "path/to/image"}
    entries. "file" paths are resolved relative to this override file's own
    directory (unless already absolute), so a set of overrides can be kept
    together in one folder and moved around as a unit."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    result = []
    for entry in entries:
        file_path = Path(entry["file"])
        if not file_path.is_absolute():
            file_path = base / file_path
        result.append({"user_id": int(entry["user_id"]), "path": file_path})
    return result


def apply_avatar_overrides(overrides: list[dict], out: Path) -> dict[int, str]:
    """Copy each override's local image into assets/avatars/{userid}.{ext},
    validating it decodes first. Returns {user_id: ext}; an override whose
    file is missing or undecodable is skipped and logged rather than
    silently ignored, since it means the mapping file has a mistake in it."""
    dest_dir = out / "assets" / "avatars"
    dest_dir.mkdir(parents=True, exist_ok=True)
    applied: dict[int, str] = {}
    for entry in overrides:
        user_id, path = entry["user_id"], entry["path"]
        if not path.exists():
            logger.warning("Avatar override for user %s not found: %s", user_id, path)
            continue
        try:
            with Image.open(path) as im:
                im.load()
                ext = _image_ext(im)
        except Exception as e:
            logger.warning("Avatar override for user %s failed to decode: %s (%s)", user_id, path, e)
            continue
        if not ext:
            continue
        shutil.copy2(path, dest_dir / f"{user_id}.{ext}")
        applied[user_id] = ext
    if applied:
        logger.info("Applied %d avatar override(s)", len(applied))
    return applied


def report_missing_avatars(users: list[dict], bad_avatars: set[str], remote_avatar_exts: dict[int, str],
                            avatar_overrides: dict[int, str], template_path: Path) -> None:
    """Print every user whose avatar doesn't resolve to anything (after
    local files, remote fetches, and any existing overrides), and write a
    starter --avatar-overrides template listing them for editing."""
    missing = [
        u for u in users
        if u.get("user_avatar_type") and not avatar_url(u, "assets", bad_avatars, remote_avatar_exts, avatar_overrides)
    ]

    if not missing:
        print("No users with a missing or broken avatar.")
        return

    print(f"{len(missing)} user(s) with a missing or broken avatar:\n")
    print(f"{'user_id':<8} {'username':<24} {'type':<22} original")
    for u in sorted(missing, key=lambda u: u["user_id"]):
        print(f"{u['user_id']:<8} {u['username']:<24} {u.get('user_avatar_type', ''):<22} {u.get('user_avatar', '')}")

    template = [{"user_id": u["user_id"], "file": f"{u['username']}.ext"} for u in missing]
    template_path.write_text(json.dumps(template, indent=2), encoding="utf-8")
    print(f"\nWrote a starter override template to {template_path}")
    print('Edit each "file" to a local image path (relative to this file\'s own directory),')
    print("then pass it via --avatar-overrides.")


def find_image_urls(db: PhpbbDatabase, users: list[dict], forums: list[dict],
                     exclude_forum_ids: set[int] | None = None) -> set[str]:
    """Collect every external image URL referenced via [img] BBCode (with or
    without a UID suffix) or XML <IMG src>/<URL url> markup across post
    bodies, signatures, forum descriptions, and poll questions/options.
    Must be called with raw (not yet BBCode-converted) text. `forums` should
    already be exclusion-filtered — only post/poll text needs
    exclude_forum_ids explicitly, since neither is otherwise filtered before
    reaching here.

    Mirrors the same shapes PhpbbBBCodeParser actually renders (see
    bbcode.py's _convert_xml_markup/_convert_bbcode) so a shape the renderer
    resolves is never silently dropped just because discovery never fetched
    it — see phpbb-archive security review, finding 10. In particular a
    board that migrated from phpBB2 can carry a literal, unconverted
    [img]<URL url="X">...</URL>[/img] (see the "Malformed [img] tags" entry
    in docs/CHANGES.md) — confirmed real and common on this dump (969 real
    posts). html.unescape() matches a URL stored as XML text/attribute
    content, which has "&" entity-escaped to "&amp;" per XML rules —
    passing that straight to an HTTP request would break any query string
    with more than one parameter."""
    # A UID suffix (:abc123) only ever appears on the legacy bracket-BBCode
    # path, but matching it regardless of dump format costs nothing.
    pattern_bbcode = re.compile(r'\[img(?::\w+)?\](https?://[^\]]*)\[/img(?::\w+)?\]', re.IGNORECASE)
    pattern_xml = re.compile(r'<IMG\s+src="(https?://[^"]*)"', re.IGNORECASE)
    # [img]<URL url="X">...</URL>[/img] — see bbcode.py's own comment on
    # this same regex (case (a) in _convert_xml_markup) for the real-world
    # shape this matches, including the tolerated short gaps on both sides.
    pattern_img_url_migration = re.compile(
        r'\[img\][^<]{0,20}<URL url="(https?://[^"]*)"[^>]*>.*?</URL>[^\[]*?\[/img\]',
        re.IGNORECASE | re.DOTALL,
    )

    texts = db.get_all_post_texts(exclude_forum_ids)
    texts.extend(u.get("user_sig", "") for u in users if u.get("user_sig"))
    texts.extend(f.get("forum_desc", "") for f in forums if f.get("forum_desc"))
    texts.extend(db.get_all_poll_texts(exclude_forum_ids))

    urls: set[str] = set()
    for text in texts:
        urls.update(html.unescape(m.strip()) for m in pattern_bbcode.findall(text))
        urls.update(html.unescape(m) for m in pattern_xml.findall(text))
        urls.update(html.unescape(m) for m in pattern_img_url_migration.findall(text))
    return urls


def download_external_images(urls: set[str], out: Path, url_mirrors: dict[str, Path] | None = None,
                              ignored_hosts: set[str] | None = None) -> dict[str, str]:
    """Download external [img]/<IMG> URLs into assets/external/{hash}.{ext}
    so the archive stays self-contained. Returns {url: filename} for URLs
    that downloaded and decoded successfully; a dead or undecodable URL is
    skipped and its [img] tag is dropped entirely rather than left broken."""
    import hashlib

    cached: dict[str, str] = {}
    if not urls:
        return cached

    dest_dir = out / "assets" / "external"
    dest_dir.mkdir(parents=True, exist_ok=True)
    skipped = 0
    for url in sorted(urls):
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        existing = next(dest_dir.glob(f"{digest}.*"), None)
        if existing is not None:
            # Already cached from a previous run (see generate()'s
            # --incremental, which preserves assets/ across runs).
            cached[url] = existing.name
            skipped += 1
            continue
        result = _fetch_image(url, url_mirrors=url_mirrors, ignored_hosts=ignored_hosts)
        if result is None:
            continue
        data, ext = result
        name = f"{digest}.{ext}"
        (dest_dir / name).write_bytes(data)
        cached[url] = name

    logger.info("Cached %d of %d external images (%d already cached, %d newly fetched)",
                len(cached), len(urls), skipped, len(cached) - skipped)
    return cached


def _download_image_asset(url: str, dest_dir: Path, stem: str, label: str,
                           default_ext: str = "png") -> str | None:
    """Download an image from url into dest_dir/<stem>.<ext> — for an asset
    that's real on the live board but doesn't live anywhere in a bare SQL
    dump (a favicon, a logo). Extension comes from the URL's own path
    when it looks like an image one, else the response's Content-Type,
    else default_ext if neither is conclusive. Returns the extension
    used, or None if the URL couldn't be fetched (dead link, or blocked
    by something like a Cloudflare JS challenge that a plain HTTP client
    can't pass) — same graceful-skip treatment as every other network
    fetch in this generator, not a hard failure."""
    import urllib.error
    import urllib.parse
    import urllib.request

    ext_from_path = Path(urllib.parse.urlparse(url).path).suffix.lstrip(".").lower()
    known_exts = {"ico", "png", "svg", "gif", "jpg", "jpeg"}
    content_type_to_ext = {
        "image/vnd.microsoft.icon": "ico", "image/x-icon": "ico",
        "image/png": "png", "image/svg+xml": "svg",
        "image/gif": "gif", "image/jpeg": "jpg",
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
    except (urllib.error.URLError, OSError) as e:
        logger.warning("%s URL unreachable: %s (%s)", label, url, e)
        return None
    ext = ext_from_path if ext_from_path in known_exts else content_type_to_ext.get(content_type, default_ext)
    (dest_dir / f"{stem}.{ext}").write_bytes(data)
    return ext


def _svg_natural_size(path: Path) -> tuple[float, float] | None:
    """Read an SVG's natural width/height from its own width/height
    attributes if set, else derived from its viewBox — the common
    real-world case (a real dump's own logo.svg only declares viewBox,
    no explicit width/height). No new dependency: SVG is XML, and the
    stdlib parses it directly."""
    import re as _re
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None

    def _num(s: str | None) -> float | None:
        m = _re.match(r'[\d.]+', s or '')
        return float(m.group()) if m else None

    w, h = _num(root.get('width')), _num(root.get('height'))
    if w and h:
        return (w, h)
    viewbox = root.get('viewBox')
    if viewbox:
        parts = viewbox.split()
        if len(parts) == 4:
            try:
                return (float(parts[2]), float(parts[3]))
            except ValueError:
                pass
    return None


def _compute_logo_display_size(path: Path, max_height: int = 50, max_width: int = 200) -> tuple[int, int] | None:
    """Read a logo's real dimensions (Pillow for raster formats, SVG's own
    width/height/viewBox for vector ones) and scale them proportionally
    to fit within max_height/max_width (whichever binds first), never
    upscaling. A flat CSS max-height alone doesn't account for aspect
    ratio — a wide, short logo could still overflow width-wise with
    nothing but a height cap. Returns None (falls back to CSS-only
    scaling) if neither approach can determine a size."""
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        svg_size = _svg_natural_size(path)
        if not svg_size:
            return None
        w, h = svg_size
    if w <= 0 or h <= 0:
        return None
    scale = min(max_height / h, max_width / w, 1.0)
    return (round(w * scale), round(h * scale))


def get_user_rank(user: dict, ranks: list[dict]) -> dict | None:
    """Find the display rank for a user: special-assigned first, then by post count."""
    special_rank_id = user.get("user_rank", 0)
    if special_rank_id:
        for r in ranks:
            if r["rank_id"] == special_rank_id and r.get("rank_special"):
                return r
    post_count = user.get("user_posts", 0)
    best = None
    for r in ranks:
        if not r.get("rank_special") and r["rank_min"] <= post_count:
            if best is None or r["rank_min"] > best["rank_min"]:
                best = r
    return best


# ---------------------------------------------------------------------------
# Forum tree builder
# ---------------------------------------------------------------------------

def build_forum_tree(forums: list[dict]) -> list[dict]:
    by_id = {f["forum_id"]: {**f, "children": []} for f in forums}
    roots = []
    for f in sorted(forums, key=lambda x: x["left_id"]):
        node = by_id[f["forum_id"]]
        parent = by_id.get(f["parent_id"]) if f["parent_id"] != 0 else None
        # A forum whose parent isn't in the list (e.g. excluded via
        # --exclude without also excluding this child) becomes a root
        # rather than being dropped or raising a KeyError.
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


def prune_empty_categories(nodes: list[dict]) -> list[dict]:
    """Drop category (forum_type 0) nodes that end up with no children —
    mirrors phpBB's own behavior of hiding a category nobody can see into.
    A forum with no topics is not touched; only categories are pruned."""
    result = []
    for node in nodes:
        node["children"] = prune_empty_categories(node["children"])
        if node.get("forum_type") == 0 and not node["children"]:
            continue
        result.append(node)
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def build_jinja_env(template_dir: Path) -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    env.filters["timestamp"] = format_timestamp
    return env


def process_forum_descs(forums: list[dict], parser: PhpbbBBCodeParser) -> list[dict]:
    """Run forum_desc through the BBCode/XML parser for each forum."""
    result = []
    for f in forums:
        f = dict(f)
        desc = f.get("forum_desc", "")
        uid = f.get("forum_desc_uid", "")
        if desc:
            f["forum_desc"] = parser.convert(desc, uid=uid, post_id=None)
        result.append(f)
    return result


def render_index(env: jinja2.Environment, out: Path, forum_tree: list[dict],
                 total_posts: int, site_name: str = "",
                 announcement_html: str | None = None) -> None:
    tmpl = env.get_template("index.html")
    html = tmpl.render(
        page_title="Board Index",
        forum_tree=forum_tree,
        total_posts=total_posts,
        assets="assets",
        root="",
        site_name=site_name,
        announcement_html=announcement_html,
    )
    (out / "index.html").write_text(html, encoding="utf-8")
    logger.info("Rendered index.html")


def render_forums(env: jinja2.Environment, out: Path, db: PhpbbDatabase,
                  users: dict[int, dict], parser: PhpbbBBCodeParser,
                  forums: list[dict], site_name: str = "",
                  announcement_html: str | None = None,
                  exclude_forum_ids: set[int] | None = None) -> None:
    forums_dir = out / "forums"
    forums_dir.mkdir(exist_ok=True)
    tmpl = env.get_template("forum.html")

    children_by_parent: dict[int, list[dict]] = {}
    for f in forums:
        children_by_parent.setdefault(f["parent_id"], []).append(f)

    # Board-wide announcements (topic_type = 3) belong above every forum's
    # own listing, not just the forum they were physically posted in — see
    # db.get_global_announcements(). Fetched once and merged per-forum below
    # rather than folded into get_topics() itself, so each global topic's
    # own page (rendered from its actual forum via render_topics()) isn't
    # duplicated.
    global_announcements = db.get_global_announcements(exclude_forum_ids)

    def _topic_sort_tier(topic_type: int) -> int:
        if topic_type in (2, 3):
            return 0
        if topic_type == 1:
            return 1
        return 2

    # Categories (forum_type 0) get a page too — no topics of their own
    # (phpBB doesn't allow posting directly into a category), but a page
    # showing their description and sub-forums, same as a regular forum's
    # Sub-forums section. Link-type forums (2) still get no local page.
    renderable_forums = [f for f in forums if f.get("forum_type") in (0, 1)]
    for forum in process_forum_descs(renderable_forums, parser):
        is_category = forum.get("forum_type") == 0
        topics = []
        if not is_category:
            topics = db.get_topics(forum["forum_id"])
            others_global = [g for g in global_announcements if g["forum_id"] != forum["forum_id"]]
            if others_global:
                topics = sorted(
                    topics + others_global,
                    key=lambda t: (_topic_sort_tier(t.get("topic_type", 0)), -t["topic_last_post_time"]),
                )
            # Annotate each topic with its starter's username for the template
            for t in topics:
                poster = users.get(t.get("topic_poster", 0))
                t["topic_poster_name"] = poster["username"] if poster else ""

        html = tmpl.render(
            page_title=forum["forum_name"],
            forum=forum,
            is_category=is_category,
            sub_forums=children_by_parent.get(forum["forum_id"], []),
            topics=topics,
            assets="../assets",
            root="../",
            site_name=site_name,
            announcement_html=announcement_html,
        )
        (forums_dir / f"{forum['forum_id']}.html").write_text(html, encoding="utf-8")

    logger.info("Rendered %d forum pages", len(renderable_forums))


def render_topics(env: jinja2.Environment, out: Path, db: PhpbbDatabase,
                  users: dict[int, dict], smilies: list[dict],
                  ranks: list[dict], custom_bbcodes: list[dict],
                  forums: list[dict], bad_attachments: set[str],
                  bad_avatars: set[str], remote_avatar_exts: dict[int, str],
                  avatar_overrides: dict[int, str], external_images: dict[str, str],
                  internal_topic_ids: set[int], bad_smilies: set[str],
                  board_hosts: set[str],
                  site_name: str = "", announcement_html: str | None = None,
                  site_url: str | None = None) -> int:
    """Render all topic pages. Returns total post count."""
    topics_dir = out / "topics"
    topics_dir.mkdir(exist_ok=True)
    tmpl = env.get_template("topic.html")

    all_topics = []
    for forum in forums:
        all_topics.extend(db.get_topics(forum["forum_id"]))

    forum_names: dict[int, str] = {f["forum_id"]: f["forum_name"] for f in forums}
    total_posts = 0

    for topic in all_topics:
        posts = db.get_posts(topic["topic_id"])
        total_posts += len(posts)
        post_ids = [p["post_id"] for p in posts]
        attachments_map = build_attachments_map(db, post_ids)

        parser = PhpbbBBCodeParser(
            smilies=smilies,
            attachments=attachments_map,
            custom_bbcodes=custom_bbcodes,
            assets_prefix="../assets",
            bad_attachments=bad_attachments,
            external_images=external_images,
            internal_topic_ids=internal_topic_ids,
            bad_smilies=bad_smilies,
            board_hosts=board_hosts,
        )

        rendered_posts = []
        for post in posts:
            uid = post.get("bbcode_uid", "")
            text = post.get("post_text", "")
            rendered = parser.convert(text, uid=uid, post_id=post["post_id"],
                                       enable_smilies=bool(post.get("enable_smilies", 1)))

            poster_id = post.get("poster_id", 0)
            # user_id=1 is phpBB's own reserved "Anonymous" account — a
            # real row, but not a real profile, and not who actually wrote
            # this post. The poster's own name as typed at posting time is
            # in post_username (phpbb_posts) instead; template falls back
            # to it (see topic.html's author-less branch) rather than
            # linking every guest post to the same generic account page.
            author = users.get(poster_id) if poster_id != 1 else None
            author_ctx = None
            if author:
                rank = get_user_rank(author, ranks)
                sig_html = ""
                # enable_sig is a per-post checkbox (phpbb_posts.enable_sig),
                # not a per-user setting — the same user's signature can
                # legitimately show on some of their posts and not others.
                if author.get("user_sig") and post.get("enable_sig", 1):
                    sig_html = parser.convert(
                        author["user_sig"],
                        uid=author.get("user_sig_bbcode_uid", ""),
                        post_id=None,
                    )
                author_ctx = {
                    **author,
                    "avatar_url": avatar_url(author, "../assets", bad_avatars, remote_avatar_exts, avatar_overrides),
                    "rank_title": author.get("user_custom_title") or (rank["rank_title"] if rank else ""),
                    "rank_image": rank.get("rank_image", "") if rank else "",
                    "sig_html": sig_html,
                }

            rendered_posts.append({
                **post,
                "rendered_html": rendered,
                "author": author_ctx,
            })

        poll = None
        if topic.get("poll_title"):
            options = db.get_poll_options(topic["topic_id"])
            total_votes = sum(o["poll_option_total"] for o in options)
            poll = {
                "title_html": parser.convert(topic["poll_title"], uid="", post_id=None),
                "max_options": topic.get("poll_max_options", 1),
                "total_votes": total_votes,
                "options": [
                    {
                        "text_html": parser.convert(o["poll_option_text"], uid="", post_id=None),
                        "votes": o["poll_option_total"],
                        "percent": round(o["poll_option_total"] / total_votes * 100) if total_votes else 0,
                    }
                    for o in options
                ],
            }

        forum_name = forum_names.get(topic["forum_id"], "")
        html = tmpl.render(
            page_title=topic["topic_title"],
            topic=topic,
            poll=poll,
            forum_name=forum_name,
            posts=rendered_posts,
            assets="../assets",
            root="../",
            site_name=site_name,
            announcement_html=announcement_html,
            site_url=site_url,
        )
        (topics_dir / f"{topic['topic_id']}.html").write_text(html, encoding="utf-8")

    logger.info("Rendered %d topic pages (%d posts)", len(all_topics), total_posts)
    return total_posts


def render_users(env: jinja2.Environment, out: Path, db: PhpbbDatabase,
                 smilies: list[dict], ranks: list[dict],
                 custom_bbcodes: list[dict], bad_avatars: set[str],
                 remote_avatar_exts: dict[int, str], avatar_overrides: dict[int, str],
                 external_images: dict[str, str], internal_topic_ids: set[int],
                 bad_smilies: set[str], board_hosts: set[str],
                 site_name: str = "") -> None:
    users_dir = out / "users"
    users_dir.mkdir(exist_ok=True)
    tmpl = env.get_template("user.html")

    parser = PhpbbBBCodeParser(
        smilies=smilies,
        attachments={},
        custom_bbcodes=custom_bbcodes,
        assets_prefix="../assets",
        external_images=external_images,
        internal_topic_ids=internal_topic_ids,
        bad_smilies=bad_smilies,
        board_hosts=board_hosts,
    )

    for user in db.get_all_users():
        rank = get_user_rank(user, ranks)
        sig_html = ""
        if user.get("user_sig"):
            sig_html = parser.convert(
                user["user_sig"],
                uid=user.get("user_sig_bbcode_uid", ""),
                post_id=None,
            )

        html = tmpl.render(
            page_title=user["username"],
            user={**user, "avatar_url": avatar_url(user, "../assets", bad_avatars, remote_avatar_exts, avatar_overrides)},
            rank=rank,
            sig_html=sig_html,
            assets="../assets",
            root="../",
            site_name=site_name,
        )
        (users_dir / f"{user['user_id']}.html").write_text(html, encoding="utf-8")

    logger.info("Rendered %d user pages", len(db.get_all_users()))


def render_sitemap(out: Path, db: PhpbbDatabase, site_url: str, forums: list[dict]) -> None:
    """Write output/sitemap.xml and output/robots.txt. site_url is the
    absolute base URL the archive will be hosted at (e.g.
    "https://archive.example.com/") — required because sitemap entries
    must be absolute, unlike every other link the archive generates,
    which stays relative so the archive can be hosted at any path."""
    if not site_url.endswith("/"):
        site_url += "/"

    def iso_date(ts: int | None) -> str | None:
        if not ts:
            return None
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")

    urls: list[tuple[str, str | None]] = []

    latest = max((f.get("forum_last_post_time") or 0 for f in forums), default=0)
    urls.append((f"{site_url}index.html", iso_date(latest)))

    renderable_forums = [f for f in forums if f.get("forum_type") in (0, 1)]
    for forum in renderable_forums:
        urls.append((
            f"{site_url}forums/{forum['forum_id']}.html",
            iso_date(forum.get("forum_last_post_time")),
        ))

    for forum in renderable_forums:
        if forum.get("forum_type") != 1:
            continue
        for topic in db.get_topics(forum["forum_id"]):
            urls.append((
                f"{site_url}topics/{topic['topic_id']}.html",
                iso_date(topic.get("topic_last_post_time")),
            ))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{html.escape(loc)}</loc>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    (out / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Rendered sitemap.xml (%d URLs)", len(urls))

    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {site_url}sitemap.xml\n", encoding="utf-8"
    )
    logger.info("Rendered robots.txt")


def _apache_redirects(old_prefix: str, base_url: str) -> str:
    """old_prefix: the live board's own script path (e.g. "board" for a
    board installed at .../board/viewtopic.php), no leading/trailing
    slash, "" if it was installed at the web root. base_url: where this
    archive's own pages live — "/" for the same host, or an absolute
    "https://host/" root when the old board was reachable somewhere
    else entirely (a different subdomain, since a relative redirect
    target would otherwise resolve against wherever the rule itself
    runs, not necessarily where the archive actually is)."""
    p = f"{old_prefix}/" if old_prefix else ""
    return f"""\
# Redirects the live phpBB board's old dynamic URLs to this static
# archive's own pages. Generated by phpbb-archive's --redirect-format
# apache — regenerate this file (or move it) if the archive's own URL
# layout ever changes. A topic/forum/user id that isn't actually in
# this archive (excluded, or never existed) just redirects to a page
# that doesn't exist, the same 404 it would give without this rule.
RewriteEngine On

# {p}viewtopic.php?...t=<id>...p=<post_id>[...] -> {base_url}topics/<id>.html#p<post_id>
# (or the less common ...p=<post_id>...t=<id>... order — %N backreferences
# only ever come from the single LAST matched RewriteCond, never accumulate
# across separate RewriteCond lines, so t and p must be captured together
# in one regex per order, not as two separate conditions)
RewriteCond %{{QUERY_STRING}} (?:^|&)t=([0-9]+)(?:&[^&]*)*&p=([0-9]+)
RewriteRule ^{p}viewtopic\\.php$ {base_url}topics/%1.html#p%2? [NE,R=301,L]
RewriteCond %{{QUERY_STRING}} (?:^|&)p=([0-9]+)(?:&[^&]*)*&t=([0-9]+)
RewriteRule ^{p}viewtopic\\.php$ {base_url}topics/%2.html#p%1? [NE,R=301,L]

# {p}viewtopic.php?...t=<id>[...] (no specific post) -> {base_url}topics/<id>.html
RewriteCond %{{QUERY_STRING}} (?:^|&)t=([0-9]+)
RewriteRule ^{p}viewtopic\\.php$ {base_url}topics/%1.html? [R=301,L]

# {p}viewforum.php?...f=<id>[...] -> {base_url}forums/<id>.html
RewriteCond %{{QUERY_STRING}} (?:^|&)f=([0-9]+)
RewriteRule ^{p}viewforum\\.php$ {base_url}forums/%1.html? [R=301,L]

# {p}memberlist.php?mode=viewprofile&u=<id> -> {base_url}users/<id>.html
RewriteCond %{{QUERY_STRING}} (?:^|&)u=([0-9]+)
RewriteRule ^{p}memberlist\\.php$ {base_url}users/%1.html? [R=301,L]
"""


def _nginx_redirects(old_prefix: str, base_url: str) -> str:
    """Same parameters as _apache_redirects."""
    p = f"/{old_prefix}" if old_prefix else ""
    return f"""\
# Redirects the live phpBB board's old dynamic URLs to this static
# archive's own pages. Generated by phpbb-archive's --redirect-format
# nginx — regenerate this file (or move it) if the archive's own URL
# layout ever changes. A topic/forum/user id that isn't actually in
# this archive (excluded, or never existed) just redirects to a page
# that doesn't exist, the same 404 it would give without this rule.
# `include` this file inside your server {{}} block.

location = {p}/viewtopic.php {{
    if ($arg_p) {{
        return 301 {base_url}topics/$arg_t.html#p$arg_p;
    }}
    if ($arg_t) {{
        return 301 {base_url}topics/$arg_t.html;
    }}
}}

location = {p}/viewforum.php {{
    if ($arg_f) {{
        return 301 {base_url}forums/$arg_f.html;
    }}
}}

location = {p}/memberlist.php {{
    if ($arg_u) {{
        return 301 {base_url}users/$arg_u.html;
    }}
}}
"""


_REDIRECT_FORMATS = {
    "apache": (".htaccess", _apache_redirects),
    "nginx": ("nginx-redirects.conf", _nginx_redirects),
}


def render_redirects(out: Path, redirect_format: str, old_prefix: str, base_url: str) -> None:
    """Write a server-config snippet (output/.htaccess for apache,
    output/nginx-redirects.conf for nginx) that 301-redirects the old
    live board's dynamic URLs (viewtopic.php/viewforum.php/memberlist.php)
    to this archive's own static pages, preserving a direct post link's
    #pN anchor where the old URL carried a p= query param. A single
    generic rule per old script, driven by whatever id is actually in
    the incoming request — not a per-topic/forum/user list — so this
    stays small and doesn't need regenerating just because the archive's
    content changes, only if its own URL layout does. See
    _apache_redirects for old_prefix/base_url."""
    filename, render = _REDIRECT_FORMATS[redirect_format]
    (out / filename).write_text(render(old_prefix, base_url), encoding="utf-8")
    logger.info("Rendered %s (%s redirects)", filename, redirect_format)


def render_search(env: jinja2.Environment, out: Path, site_name: str = "") -> None:
    tmpl = env.get_template("search.html")
    html_out = tmpl.render(page_title="Search", assets="assets", root="", site_name=site_name)
    (out / "search.html").write_text(html_out, encoding="utf-8")
    logger.info("Rendered search.html")


def run_pagefind(out: Path) -> None:
    """Index every generated page for client-side search (see --search).
    Runs the pagefind binary installed via the pagefind[bin] Python
    package as a subprocess — there's no importable API, only a CLI.
    search.html itself is excluded via data-pagefind-ignore (see
    search.html's body_attrs block) since it has no real content to
    index, just the search widget."""
    logger.info("Indexing for search (pagefind) ...")
    subprocess.run(
        [sys.executable, "-m", "pagefind", "--site", str(out)],
        check=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _open_db_and_copy_assets(dump_dir: str, output_dir: str,
                              exclude_path: str | None = None,
                              style_css_path: str | None = None,
                              favicon_path: str | None = None,
                              logo_path: str | None = None,
                              theme: str = "light",
                              copy_assets_mode: str = "full") -> tuple[PhpbbDatabase, Path, Path, str, set[int], str, set[str]]:
    """Shared setup for generate() and the diagnostic modes: import the
    dump into SQLite and, per copy_assets_mode, copy assets/. Returns
    (db, dump, out, site_name, excluded_forum_ids, db_path,
    excluded_physical_filenames).

    copy_assets_mode controls what (if anything) gets written into
    --output, since a diagnostic mode may be pointed at an --output
    directory a real generate() run already deployed to:
      - "full": everything copy_assets() does (generate()'s own default).
      - "avatars": only copy_avatars() — find_missing_avatars() needs
        existing avatar files on disk to check resolution against, but
        nothing else (style.css/favicon/logo/theme CSS/smilies/ranks/
        attachments) should be touched by what's meant to be a read-only
        check.
      - "none": no writes to --output at all (list_forums(), check_images()
        don't render anything, so they don't need assets/ populated).
    Without this distinction, every diagnostic mode called copy_assets()
    unconditionally and without --exclude, silently overwriting a real
    deployment's custom style.css with the default palette and re-copying
    already-excluded (private-message/excluded-forum) attachments back
    into a real, possibly-published --output. See phpbb-archive security
    review, finding 14.

    db_path is a system-temp file, not anything under out/ — the imported
    database holds the *entire* dump verbatim (private messages, password
    hashes, email addresses, excluded/hidden content, everything), so it
    must never sit inside a directory a caller might publish. The caller
    is responsible for deleting it (see cleanup at the end of generate()
    and every diagnostic mode below)."""
    if copy_assets_mode not in ("full", "avatars", "none"):
        raise ValueError(f"invalid copy_assets_mode: {copy_assets_mode!r}")
    dump = Path(dump_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    table_prefix = read_table_prefix(dump / "config.php")
    logger.info("Table prefix: %s", table_prefix)

    sql_files = sorted(dump.glob("*.sql"))
    if not sql_files:
        raise FileNotFoundError(f"No .sql file found in {dump}")
    sql_file = sql_files[0]
    logger.info("Using SQL dump: %s", sql_file)

    db_fd, db_path = tempfile.mkstemp(suffix=".phpbb_archive.db")
    os.close(db_fd)
    logger.info("Importing MySQL dump → SQLite (working copy, outside output/) ...")
    import_mysql_dump(str(sql_file), db_path)
    db = PhpbbDatabase(db_path, table_prefix=table_prefix)

    site_name = db.get_config("sitename") or sql_file.stem
    logger.info("Site name: %s", site_name)

    resolve_default_style(db, dump)

    excluded_forum_ids: set[int] = set()
    # Private-message attachments are never public content, regardless of
    # --exclude — excluded from copying unconditionally, not just when a
    # forum happens to be excluded too.
    excluded_physical_filenames: set[str] = db.get_private_message_attachment_physical_filenames()
    if exclude_path:
        seed_ids = load_exclusions(Path(exclude_path))
        excluded_forum_ids = expand_exclusions_recursively(seed_ids, db.get_forums())
        excluded_physical_filenames |= db.get_attachment_physical_filenames_in_forums(excluded_forum_ids)
        logger.info("Excluding %d forum(s)/categor(y/ies) from the archive (%d listed, %d after including descendants)",
                    len(excluded_forum_ids), len(seed_ids), len(excluded_forum_ids))

    if copy_assets_mode == "full":
        physical_to_real = {a["physical_filename"]: a["real_filename"] for a in db.get_all_attachments()
                             if a["physical_filename"] not in excluded_physical_filenames}
        logger.info("Copying assets ...")
        copy_assets(dump, out, excluded_physical_filenames, style_css_path, physical_to_real, favicon_path, logo_path, theme)
    elif copy_assets_mode == "avatars":
        logger.info("Copying avatars ...")
        copy_avatars(dump, out)

    return db, dump, out, site_name, excluded_forum_ids, db_path, excluded_physical_filenames


def clean_disabled_feature_output(out: Path, search: bool, sitemap_url: str | None,
                                   redirect_format: str | None) -> None:
    """Remove feature-gated output an earlier run left behind that this
    run's flags no longer request (--search dropped, --sitemap-url
    removed, --redirect-format disabled or switched apache<->nginx) —
    an --incremental run's unconditional cleanup only ever clears
    forums/topics/users/index.html, which are rebuilt every run
    regardless of flags, so this content would otherwise sit in output/
    indefinitely showing stale or removed content."""
    conditionally_generated = {
        "search.html": search,
        "pagefind": search,
        "sitemap.xml": bool(sitemap_url),
        "robots.txt": bool(sitemap_url),
        ".htaccess": redirect_format == "apache",
        "nginx-redirects.conf": redirect_format == "nginx",
    }
    for name, keep in conditionally_generated.items():
        if keep:
            continue
        target = out / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def generate(dump_dir: str, output_dir: str, avatar_overrides_path: str | None = None,
             exclude_path: str | None = None, url_mirrors_path: str | None = None,
             incremental: bool = False, ignored_hosts_path: str | None = None,
             attachment_recovery_dir: str | None = None, style_css_path: str | None = None,
             announcement_path: str | None = None, sitemap_url: str | None = None,
             search: bool = False, profile_position: str = "left",
             favicon_path: str | None = None, favicon_url: str | None = None,
             logo_path: str | None = None, logo_url: str | None = None,
             logo_natural_size: bool = False, theme: str = "light",
             board_hosts_path: str | None = None,
             redirect_format: str | None = None,
             redirect_old_prefix: str | None = None) -> None:
    out = Path(output_dir)
    dump = Path(dump_dir)

    # Validate before doing anything destructive below: a missing dump
    # would otherwise only surface after --output has already been wiped,
    # and --output/--dump pointing at overlapping paths (a mistake, or one
    # accidentally left as the other's default) would delete whichever
    # directory the wipe below reaches first.
    if not sorted(dump.glob("*.sql")):
        raise FileNotFoundError(f"No .sql file found in {dump} — nothing to generate from")
    out_resolved, dump_resolved = out.resolve(), dump.resolve()
    if out_resolved == dump_resolved or dump_resolved in out_resolved.parents or out_resolved in dump_resolved.parents:
        raise ValueError(f"--output ({out}) and --dump ({dump}) must not be the same directory "
                          "or contain one another")

    # Load now, before --output (which may itself be the file's directory,
    # per the README's own documented --avatar-overrides output/avatar_
    # overrides.json workflow) gets wiped by a non-incremental run below.
    avatar_overrides_entries = load_avatar_overrides(Path(avatar_overrides_path)) if avatar_overrides_path else []

    if out.exists():
        if incremental:
            # Keep assets/ (attachments, avatars, external images) so
            # already-successful downloads aren't re-fetched — only the
            # generated pages, which must always reflect the current
            # dump/exclusions, get cleared. download_remote_avatars() and
            # download_external_images() skip a URL whose cached file is
            # already present; a URL that failed last run is retried.
            logger.info("Incremental run: clearing generated pages, keeping cached assets")
            # .phpbb_archive.db is a one-time migration cleanup: the
            # database now always builds in a system-temp file (see
            # _open_db_and_copy_assets), never under output/, but an
            # incremental run against an existing output/ built before
            # that fix would otherwise leave its old copy sitting there
            # forever — incremental mode never revisits a file it isn't
            # explicitly told to look at.
            for name in ("forums", "topics", "users", "index.html", ".phpbb_archive.db"):
                target = out / name
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()

            clean_disabled_feature_output(out, search=search, sitemap_url=sitemap_url, redirect_format=redirect_format)
        else:
            # Start from a clean slate: without this, a forum/topic/user
            # that no longer gets a page this run (e.g. a category, per
            # render_forums()) would leave its stale page behind forever.
            logger.info("Clearing previous output: %s", out)
            shutil.rmtree(out)

    db, dump, out, site_name, excluded_forum_ids, db_path, excluded_physical_filenames = _open_db_and_copy_assets(dump_dir, output_dir, exclude_path, style_css_path, favicon_path, logo_path, theme)

    # --- Image/zip/rar attachments missing or corrupted in the source dump ---
    bad_attachments_map = find_bad_attachments(db, out, excluded_physical_filenames)
    if attachment_recovery_dir:
        bad_attachments = recover_bad_attachments(bad_attachments_map, Path(attachment_recovery_dir), out)
    else:
        bad_attachments = set(bad_attachments_map.keys())

    # --- Lookup tables ---
    smilies = db.get_smilies()
    ranks = db.get_ranks()
    custom_bbcodes = db.get_bbcodes()
    users: dict[int, dict] = {u["user_id"]: u for u in db.get_all_users()}
    forums = [f for f in db.get_forums() if f["forum_id"] not in excluded_forum_ids]

    url_mirrors = load_url_mirrors(Path(url_mirrors_path)) if url_mirrors_path else None
    ignored_hosts = load_ignored_hosts(Path(ignored_hosts_path)) if ignored_hosts_path else None

    # --- Smilies missing or corrupted in the source dump ---
    bad_smilies = find_bad_smilies(smilies, out)

    # --- Avatars missing or corrupted in the source dump ---
    bad_avatars = find_bad_avatars(list(users.values()), out)
    remote_avatar_exts = download_remote_avatars(list(users.values()), out, url_mirrors, ignored_hosts)
    avatar_overrides = {}
    if avatar_overrides_path:
        avatar_overrides = apply_avatar_overrides(avatar_overrides_entries, out)

    # --- External [img]/<IMG> URLs referenced in posts, sigs, forum descs ---
    # forums is already exclusion-filtered, so forum_desc scanning skips
    # excluded forums naturally; post text is filtered explicitly.
    image_urls = find_image_urls(db, list(users.values()), forums, excluded_forum_ids)
    external_images = download_external_images(image_urls, out, url_mirrors, ignored_hosts)

    # --- Templates ---
    template_dir = Path(__file__).parent / "templates"
    env = build_jinja_env(template_dir)
    env.globals["search_enabled"] = search
    env.globals["profile_position"] = profile_position
    env.globals["site_desc"] = db.get_config("site_desc") or None
    env.globals["board_index_text"] = db.get_config("board_index_text") or "Board index"
    downloaded_favicon_ext = _download_image_asset(favicon_url, out, "favicon", "Favicon", default_ext="ico") if favicon_url else None
    env.globals["favicon_ext"] = downloaded_favicon_ext or (Path(favicon_path).suffix.lstrip(".") if favicon_path else None)
    downloaded_logo_ext = _download_image_asset(logo_url, out / "assets", "logo", "Logo") if logo_url else None
    env.globals["logo_ext"] = downloaded_logo_ext or (Path(logo_path).suffix.lstrip(".") if logo_path else None)
    env.globals["logo_natural_size"] = logo_natural_size
    # phpBB's own configured logo display size (distinct from — and often
    # much smaller than — the source image's raw pixel dimensions, e.g. a
    # real board's own 324x308 file displayed at 84x80). Real per-board
    # metadata already in the dump, not a guessed constant; used whenever
    # present regardless of whether --logo/--logo-url provided the file,
    # since it describes the *board's* intended size, not the file's.
    logo_config_width = db.get_config("sitelogo_width")
    logo_config_height = db.get_config("sitelogo_height")
    if logo_config_width and logo_config_width != "0" and logo_config_height and logo_config_height != "0":
        env.globals["logo_width"] = logo_config_width
        env.globals["logo_height"] = logo_config_height
    elif env.globals["logo_ext"]:
        # No configured size in the dump — compute a proportional one from
        # the actual logo file's real dimensions instead of leaving it to
        # a flat CSS max-height (see _compute_logo_display_size).
        computed = _compute_logo_display_size(out / "assets" / f"logo.{env.globals['logo_ext']}")
        env.globals["logo_width"] = computed[0] if computed else None
        env.globals["logo_height"] = computed[1] if computed else None
    else:
        env.globals["logo_width"] = None
        env.globals["logo_height"] = None

    # Cache-bust assets/style.css with a hash of its own content so a
    # re-themed/re-regenerated archive is picked up immediately instead of
    # a browser serving a stale cached copy (http.server sends only
    # Last-Modified, no Cache-Control/ETag, so browsers can skip
    # revalidation). Doesn't affect the documented "drop a new
    # assets/style.css into an already-built archive" workflow: that
    # leaves the HTML (and its query string) untouched either way.
    import hashlib
    style_css_path_out = out / "assets" / "style.css"
    env.globals["style_version"] = hashlib.sha1(style_css_path_out.read_bytes()).hexdigest()[:8]

    # topic_ids actually in this archive, so a post's own link back to
    # viewtopic.php?...t=N (a cross-reference to another topic on the same
    # board) can be rewritten to a relative in-archive link — see
    # PhpbbBBCodeParser._rewrite_internal_link().
    internal_topic_ids = {t["topic_id"] for f in forums for t in db.get_topics(f["forum_id"])}

    # Hostnames recognized as this board's own, for internal-link
    # rewriting (see PhpbbBBCodeParser._is_same_board_host) — the dump's
    # own server_name plus any --board-hosts extras (a board's domain can
    # change over its lifetime; only the current one is ever in the dump).
    board_hosts = {h for h in (db.get_config("server_name"),) if h}
    if board_hosts_path:
        board_hosts |= load_board_hosts(Path(board_hosts_path))

    # --- Shared parsers (used for forum descs and the announcement, not
    # post bodies — topics/forums/users pages convert those with their own
    # per-post parser). Two instances, one per page depth actually used
    # across the site: index.html/search.html sit at the site root
    # (assets_prefix="assets"), forums/N.html, topics/N.html, and
    # users/N.html all sit one level down (assets_prefix="../assets").
    # Converting once and reusing the same HTML at both depths — the
    # original bug here — bakes in whichever depth happened to run first,
    # breaking any embedded asset/internal link on every page at the
    # other depth. See phpbb-archive security review, finding 12.
    shared_parser = PhpbbBBCodeParser(
        smilies=smilies,
        attachments={},
        custom_bbcodes=custom_bbcodes,
        assets_prefix="assets",
        external_images=external_images,
        internal_topic_ids=internal_topic_ids,
        bad_smilies=bad_smilies,
        board_hosts=board_hosts,
    )
    shared_parser_nested = PhpbbBBCodeParser(
        smilies=smilies,
        attachments={},
        custom_bbcodes=custom_bbcodes,
        assets_prefix="../assets",
        external_images=external_images,
        internal_topic_ids=internal_topic_ids,
        bad_smilies=bad_smilies,
        board_hosts=board_hosts,
    )

    # Enrich forums with the topic_id of their last post (for linking)
    for f in forums:
        post_id = f.get("forum_last_post_id") or 0
        f["forum_last_topic_id"] = db.get_post_topic_id(post_id) if post_id else None

    # --- Board-wide announcement (optional) ---
    # Plain BBCode text authored for the archive itself (e.g. "this board is
    # now a read-only archive") rather than anything pulled from the dump —
    # uid="" since there's no phpBB UID annotation to strip from hand-written
    # text. Shown on index/forum/topic pages, not user profiles. Converted
    # once per depth (see above) rather than once and reused everywhere.
    announcement_html = None
    announcement_html_nested = None
    if announcement_path:
        announcement_text = Path(announcement_path).read_text(encoding="utf-8")
        if announcement_text.strip():
            announcement_html = shared_parser.convert(announcement_text, uid="")
            announcement_html_nested = shared_parser_nested.convert(announcement_text, uid="")

    # --- Pages ---
    forum_tree = prune_empty_categories(build_forum_tree(process_forum_descs(forums, shared_parser)))

    site_url = sitemap_url if not sitemap_url or sitemap_url.endswith("/") else sitemap_url + "/"

    total_posts = render_topics(env, out, db, users, smilies, ranks, custom_bbcodes, forums, bad_attachments, bad_avatars, remote_avatar_exts, avatar_overrides, external_images, internal_topic_ids, bad_smilies, board_hosts, site_name=site_name, announcement_html=announcement_html_nested, site_url=site_url)
    render_forums(env, out, db, users, shared_parser_nested, forums, site_name=site_name, announcement_html=announcement_html_nested, exclude_forum_ids=excluded_forum_ids)
    render_users(env, out, db, smilies, ranks, custom_bbcodes, bad_avatars, remote_avatar_exts, avatar_overrides, external_images, internal_topic_ids, bad_smilies, board_hosts, site_name=site_name)
    render_index(env, out, forum_tree or [], total_posts, site_name=site_name, announcement_html=announcement_html)

    if site_url:
        render_sitemap(out, db, site_url, forums)

    if redirect_format:
        # The live board's own script_path (e.g. "board" when it was
        # installed at .../board/viewtopic.php) — auto-detected from the
        # dump so the redirect rules match the request paths the old
        # server actually receives, with a CLI override for a board that
        # moved since the dump was taken. base_url is absolute
        # (--sitemap-url) when known, since a redirect rule may run on a
        # host other than where this archive itself now lives (e.g. the
        # old board's own subdomain) and a root-relative target would
        # otherwise resolve against that host instead; falls back to a
        # root-relative "/" when no --sitemap-url was given.
        old_prefix = redirect_old_prefix if redirect_old_prefix is not None else db.get_config("script_path")
        old_prefix = (old_prefix or "").strip("/")
        redirect_base_url = site_url if site_url else "/"
        render_redirects(out, redirect_format, old_prefix, redirect_base_url)

    if search:
        render_search(env, out, site_name=site_name)

    db.close()
    os.remove(db_path)

    if search:
        run_pagefind(out)

    logger.info("Done. Output: %s", out)


def find_missing_avatars(dump_dir: str, output_dir: str, avatar_overrides_path: str | None = None) -> None:
    """Diagnostic mode (-m/--missing-avatars): resolve avatars the same way
    generate() does (local files, remote fetches, any existing overrides),
    then list every user whose avatar still doesn't resolve and write a
    starter --avatar-overrides template for them. Does not render the site."""
    db, dump, out, _site_name, _excluded, db_path, _excluded_files = _open_db_and_copy_assets(
        dump_dir, output_dir, copy_assets_mode="avatars")

    users = db.get_all_users()
    bad_avatars = find_bad_avatars(users, out)
    remote_avatar_exts = download_remote_avatars(users, out)
    avatar_overrides = {}
    if avatar_overrides_path:
        avatar_overrides = apply_avatar_overrides(load_avatar_overrides(Path(avatar_overrides_path)), out)

    # The README's own documented workflow is --avatar-overrides
    # output/avatar_overrides.json — the exact same path this mode writes
    # its own starter template to. Re-running -m with --avatar-overrides
    # already supplied (e.g. to check what's still missing after filling
    # some in) would silently overwrite that hand-edited file with a
    # fresh template covering only the still-missing subset, discarding
    # every mapping already made for a user who's no longer "missing"
    # because that very override fixed them. Never write to the supplied
    # input path; use a clearly-distinct name whenever one was given.
    # See phpbb-archive security review, finding 14.
    template_path = out / ("avatar_overrides.new.json" if avatar_overrides_path else "avatar_overrides.json")
    report_missing_avatars(users, bad_avatars, remote_avatar_exts, avatar_overrides, template_path)

    db.close()
    os.remove(db_path)


def list_forums(dump_dir: str, output_dir: str) -> None:
    """Diagnostic mode (-l/--list-forums): print every forum/category with
    its forum_id, indented to show nesting, so you know which id(s) to put
    in an --exclude JSON file. Does not render the site."""
    db, dump, out, _site_name, _excluded, db_path, _excluded_files = _open_db_and_copy_assets(
        dump_dir, output_dir, copy_assets_mode="none")

    tree = build_forum_tree(db.get_forums())
    type_label = {0: "category", 1: "forum", 2: "link"}

    def _print(nodes: list[dict], depth: int = 0) -> None:
        for node in nodes:
            kind = type_label.get(node.get("forum_type"), "?")
            print(f"{'  ' * depth}{node['forum_id']:<6} [{kind:<8}] {node['forum_name']}")
            _print(node["children"], depth + 1)

    _print(tree)
    db.close()
    os.remove(db_path)


def check_images(dump_dir: str, output_dir: str, url_mirrors_path: str | None = None,
                  ignored_hosts_path: str | None = None) -> None:
    """Diagnostic mode (-i/--check-images): find every external [img]/<IMG>
    URL referenced in posts, signatures, and forum descriptions, and report
    which ones fail to resolve (via any --url-mirrors, or the network) —
    ahead of committing to a full, slow generate() run. Does not render the
    site or write any topic/forum/user pages."""
    db, dump, out, _site_name, _excluded, db_path, _excluded_files = _open_db_and_copy_assets(
        dump_dir, output_dir, copy_assets_mode="none")

    users = db.get_all_users()
    forums = db.get_forums()
    urls = find_image_urls(db, users, forums)
    url_mirrors = load_url_mirrors(Path(url_mirrors_path)) if url_mirrors_path else None
    ignored_hosts = load_ignored_hosts(Path(ignored_hosts_path)) if ignored_hosts_path else None

    failed = []
    for url in sorted(urls):
        if _fetch_image(url, url_mirrors=url_mirrors, ignored_hosts=ignored_hosts) is None:
            failed.append(url)

    print(f"{len(urls) - len(failed)} of {len(urls)} external image URL(s) resolve.")
    if failed:
        report_path = out / "unresolved_images.json"
        report_path.write_text(json.dumps(failed, indent=2) + "\n", encoding="utf-8")
        print(f"\n{len(failed)} unresolved — written to {report_path}")
        print("A starting point: trim it down to URLs worth mirroring for --url-mirrors, or pull")
        print("out hostnames that keep failing for --ignore-hosts. The rest will just be dropped.")

    db.close()
    os.remove(db_path)


def check_links(output_dir: str) -> None:
    """Diagnostic mode (-c/--check-links): scan every generated HTML page
    for internal href/src links that don't resolve — either to a file
    that doesn't exist under output/, or (when the link includes a
    #anchor) to an id="..." that doesn't exist in the target file. Only
    checks internal (relative) links; external URLs are a separate,
    already-handled problem (see --ignore-hosts/--url-mirrors). Needs an
    already-generated output/ — run a normal generate() first."""
    import urllib.parse
    out = Path(output_dir)
    if not out.exists():
        raise FileNotFoundError(f"{out} does not exist — run a normal generate() first")

    all_files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    html_files = sorted(p for p in all_files if p.endswith(".html"))

    # Anchored to an actual opening tag (<a ... href="..." or <img ... src="...")
    # rather than a bare href=/src= match anywhere in the file — a quoted
    # code block showing raw HTML source has its real < > escaped to &lt;/&gt;
    # but not necessarily its "  quotes, so a bare match would misfire on
    # inert text that merely looks like a tag attribute.
    link_re = re.compile(r'<[a-zA-Z][a-zA-Z0-9]*\b[^>]*?\s(?:href|src)="([^"]*)"')
    anchor_cache: dict[str, set[str]] = {}

    def anchors_in(rel_path: str) -> set[str]:
        if rel_path not in anchor_cache:
            text = (out / rel_path).read_text(encoding="utf-8", errors="replace")
            anchor_cache[rel_path] = set(re.findall(r'id="([^"]+)"', text))
        return anchor_cache[rel_path]

    broken = []
    for rel_path in html_files:
        text = (out / rel_path).read_text(encoding="utf-8", errors="replace")
        current_dir = Path(rel_path).parent
        for url in link_re.findall(text):
            if url.startswith(("http://", "https://", "mailto:", "javascript:", "data:")):
                continue
            path_part, _, anchor = url.partition("#")
            path_part = path_part.split("?", 1)[0]  # drop a query string (e.g. style.css?v=<hash>) — never part of the on-disk path
            if path_part:
                # Decode first — a percent-encoded href (e.g. an attachment
                # named "Star Rates.zip" served as ".../Star%20Rates.zip")
                # must match the real on-disk name in all_files, not its
                # encoded form.
                target = posixpath.normpath((current_dir / urllib.parse.unquote(path_part)).as_posix())
            else:
                target = rel_path  # self-reference (bare #anchor)
            if target not in all_files:
                broken.append({"page": rel_path, "link": url, "reason": "missing file"})
                continue
            if anchor and target.endswith(".html") and anchor not in anchors_in(target):
                broken.append({"page": rel_path, "link": url, "reason": "missing anchor"})

    print(f"Checked {len(html_files)} page(s), {len(all_files)} total file(s) in {out}.")
    if broken:
        report_path = out / "broken_links.json"
        report_path.write_text(json.dumps(broken, indent=2) + "\n", encoding="utf-8")
        print(f"\n{len(broken)} broken internal link(s) — written to {report_path}")
    else:
        print("No broken internal links found.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a static HTML archive from a phpBB MySQL dump")
    parser.add_argument("--dump", default="dump", help="Path to dump/ directory")
    parser.add_argument("--output", default="output", help="Path to output/ directory")
    parser.add_argument("--avatar-overrides", metavar="FILE",
                         help="JSON file mapping user_id to a local avatar image, for avatars "
                              "the generator can't fetch or decode on its own")
    parser.add_argument("-m", "--missing-avatars", action="store_true",
                         help="List users with a missing/broken avatar and write a starter "
                              "--avatar-overrides template, instead of generating the archive")
    parser.add_argument("--exclude", metavar="FILE",
                         help='JSON file of forum/category IDs to leave out of the archive entirely: '
                              '{"categories": [...], "forums": [...]}. Excluding a category also '
                              "excludes everything under it, recursively — list just the top-level "
                              "id(s) you want hidden. Attachments and external images used only in "
                              "excluded forums are never copied or downloaded. Use -l/--list-forums "
                              "to find forum_id values.")
    parser.add_argument("-l", "--list-forums", action="store_true",
                         help="Print every forum/category with its forum_id (indented to show "
                              "nesting), to help build an --exclude file, instead of generating "
                              "the archive")
    parser.add_argument("--url-mirrors", metavar="FILE",
                         help='JSON file of {"url_prefix": "local_dir"} mappings. Any external '
                              "[img]/avatar URL starting with a prefix is looked up in the matching "
                              "local directory first, instead of being fetched over the network — "
                              "useful when a source site blocks the generator (e.g. Cloudflare) but "
                              "you have direct filesystem access to its files.")
    parser.add_argument("-i", "--check-images", action="store_true",
                         help="List external [img]/<IMG> URLs that fail to resolve (via "
                              "--url-mirrors or the network) and write them to "
                              "output/unresolved_images.json, instead of generating the archive — "
                              "use ahead of a full run to see what needs mirroring")
    parser.add_argument("-c", "--check-links", action="store_true",
                         help="Scan an already-generated output/ for internal href/src links that "
                              "don't resolve — either to a file that doesn't exist, or (for a "
                              "#anchor link) to an id=\"...\" that doesn't exist in the target "
                              "file — and write them to output/broken_links.json, instead of "
                              "generating the archive. External URLs aren't checked here — see "
                              "--ignore-hosts/--url-mirrors for those. Run after a normal build.")
    parser.add_argument("--incremental", action="store_true",
                         help="Keep previously-downloaded attachments/avatars/external images "
                              "instead of re-fetching everything — only failed URLs are retried. "
                              "Generated pages are still rebuilt fresh every run. Off by default.")
    parser.add_argument("--ignore-hosts", metavar="FILE",
                         help='JSON array of hostnames (e.g. ["tinypic.com"]) to skip entirely '
                              "without a network attempt — matches subdomains too. Useful for "
                              "hosts you already know are permanently gone, so --incremental "
                              "doesn't keep paying their timeout on every future run.")
    parser.add_argument("--attachment-recovery", metavar="DIR",
                         help="Directory with the same layout as an attachment source "
                              "(physical_filename-named files) to check for a working copy of "
                              "any attachment that's missing or fails to decode from the main "
                              "dump — e.g. a separately-collected backup that isn't affected by "
                              "the same corruption. A working copy found there replaces the "
                              "broken one instead of it being dropped.")
    parser.add_argument("--style-css", metavar="FILE",
                         help="Replace the archive's built-in neutral stylesheet with a custom "
                              "one (e.g. colors approximating a specific board's own theme). "
                              "Every page links assets/style.css rather than inlining it, so this "
                              "file (or a hand-edited assets/style.css copy) can also just be "
                              "dropped into an already-generated archive to re-theme it without "
                              "regenerating anything. Omit to keep the default neutral palette.")
    parser.add_argument("--announcement", metavar="FILE",
                         help="Plain text file of BBCode (e.g. \"[b]This board is now a "
                              "read-only archive.[/b]\") to show as a notice on the index, every "
                              "forum page, and every topic page. Not pulled from the dump — "
                              "written fresh for the archive itself. Omit the flag, or leave the "
                              "file blank, for no announcement.")
    parser.add_argument("--sitemap-url", metavar="URL",
                         help="Absolute base URL the archive will be hosted at (e.g. "
                              "https://archive.example.com/) — writes output/sitemap.xml (index, "
                              "every forum, every topic, with a lastmod date from the most recent "
                              "post) and output/robots.txt pointing at it. Every other link the "
                              "archive generates is relative so it works at any path; sitemap "
                              "entries can't be, which is why this needs an explicit absolute URL "
                              "rather than being inferred. Omit for no sitemap/robots.txt.")
    parser.add_argument("--search", action="store_true",
                         help="Add a dedicated search.html (linked from every page's header) "
                              "indexing every generated page with Pagefind, a static "
                              "client-side search engine — no server required, same as the rest "
                              "of the archive. Requires the pagefind[bin] package (see "
                              "generator/requirements.txt) and runs it as a subprocess after "
                              "every other page is written. Off by default.")
    parser.add_argument("--profile-position", choices=["left", "right"], default="left",
                         help="Which side of a post the poster's profile sidebar (avatar, rank, "
                              "post count) sits on in viewtopic. Defaults to left, matching "
                              "phpBB's own layout.")
    favicon_group = parser.add_mutually_exclusive_group()
    favicon_group.add_argument("--favicon", metavar="FILE",
                         help="Image file (ico/png/svg/...) used as the archive's favicon. Kept "
                              "in its original format, copied to output/favicon.<ext>. Omit for "
                              "no favicon.")
    favicon_group.add_argument("--favicon-url", metavar="URL",
                         help="Fetch the favicon from a live URL instead of a local file (e.g. "
                              "https://example.com/favicon.ico) — for a board's real favicon, "
                              "which doesn't live anywhere in a bare SQL dump. A URL that can't "
                              "be reached is skipped with a warning, same as any other network "
                              "fetch in this generator, rather than failing the whole run.")
    logo_group = parser.add_mutually_exclusive_group()
    logo_group.add_argument("--logo", metavar="FILE",
                         help="Image file (png/svg/gif/...) shown in the header in place of the "
                              "plain site-name text, scaled via CSS to fit (max-height: 50px in "
                              "the default stylesheet). Kept in its original format, copied to "
                              "output/assets/logo.<ext>. Omit to keep the plain text site name.")
    logo_group.add_argument("--logo-url", metavar="URL",
                         help="Fetch the logo from a live URL instead of a local file — for a "
                              "board's real logo, which doesn't live anywhere in a bare SQL dump. "
                              "A URL that can't be reached (dead link, or blocked by something "
                              "like a Cloudflare JS challenge that this can't pass) is skipped "
                              "with a warning, same as any other network fetch in this generator, "
                              "rather than failing the whole run.")
    parser.add_argument("--logo-natural-size", action="store_true",
                         help="Show the logo at its own original size instead of scaling it to "
                              "fit the header (max-height: 50px in the default stylesheet). Off "
                              "by default.")
    parser.add_argument("--theme", choices=["light", "dark"], default="light",
                         help="Which built-in neutral palette to use — light (default) or dark. "
                              "Has no effect when --style-css is given, since a custom stylesheet "
                              "is its own fixed palette.")
    parser.add_argument("--board-hosts", metavar="FILE",
                         help="JSON array of extra hostnames this board is also known to have "
                              "been reachable at (e.g. a former domain), in addition to the "
                              "dump's own phpbb_config.server_name. Used to recognize a post's "
                              "own viewtopic.php link back to this board so it can be rewritten "
                              "to a relative in-archive link — without this, only a link that "
                              "happens to use the domain currently in the dump is recognized, so "
                              "a board that changed domains over its lifetime needs its other "
                              "domains listed here. A topic id alone is not enough to tell: it's "
                              "just a small integer, and two unrelated phpBB installs (or the "
                              "same board on an old and current domain) can easily reuse the same "
                              "one for a completely different topic.")
    parser.add_argument("--redirect-format", choices=["apache", "nginx"],
                         help="Write a server-config snippet that 301-redirects the live board's "
                              "old dynamic URLs (viewtopic.php/viewforum.php/memberlist.php) to "
                              "this archive's own static pages — output/.htaccess for apache, "
                              "output/nginx-redirects.conf for nginx (include it in your server "
                              "{} block). A single generic rule per old script, driven by whatever "
                              "id is actually in the incoming request, not a per-page list — an id "
                              "that isn't actually in this archive just 404s through it, the same "
                              "as it would without this. Omit for no redirect file.")
    parser.add_argument("--redirect-old-prefix", metavar="PATH",
                         help="Path the live board's scripts were actually installed under "
                              "(e.g. \"board\" if it was reachable at .../board/viewtopic.php), "
                              "so --redirect-format's rules match the old server's real request "
                              "paths. Auto-detected from the dump's own phpbb_config.script_path "
                              "by default; only pass this to override that (e.g. the board moved "
                              "to a different path after the dump was taken). Use \"\" to force no "
                              "prefix. Has no effect without --redirect-format.")
    args = parser.parse_args()
    if args.missing_avatars:
        find_missing_avatars(args.dump, args.output, args.avatar_overrides)
    elif args.list_forums:
        list_forums(args.dump, args.output)
    elif args.check_images:
        check_images(args.dump, args.output, args.url_mirrors, args.ignore_hosts)
    elif args.check_links:
        check_links(args.output)
    else:
        generate(args.dump, args.output, args.avatar_overrides, args.exclude, args.url_mirrors,
                 args.incremental, args.ignore_hosts, args.attachment_recovery, args.style_css,
                 args.announcement, args.sitemap_url, args.search, args.profile_position,
                 args.favicon, args.favicon_url, args.logo, args.logo_url,
                 args.logo_natural_size, args.theme, args.board_hosts, args.redirect_format,
                 args.redirect_old_prefix)


if __name__ == "__main__":
    main()
