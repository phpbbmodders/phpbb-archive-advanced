#!/usr/bin/env python3
"""phpBB static site generator.

Converts a phpBB MySQL dump into a self-contained static HTML archive.

Usage:
    .venv/bin/python -m generator.generate --dump dump/ --output output/
"""

import argparse
import datetime
import logging
import re
import shutil
from pathlib import Path

import jinja2

from generator.db import import_mysql_dump, PhpbbDatabase
from generator.bbcode import PhpbbBBCodeParser

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


def avatar_url(user: dict, assets_prefix: str) -> str | None:
    """Return a relative URL for the user's avatar, or None if none."""
    avatar = user.get("user_avatar", "")
    kind = user.get("user_avatar_type", "")
    if not avatar or avatar == "noavatar":
        return None
    # Remote URLs (any type): use directly
    if avatar.startswith("http://") or avatar.startswith("https://"):
        return avatar
    # IPB migration artifact (e.g. "upload:av-26.png?1715977904") — can't recover file
    if avatar.startswith("upload:"):
        return None
    # Locally uploaded file: DB stores "{userid}_{timestamp}.ext", we copy as "{userid}.ext"
    if kind == "avatar.driver.upload":
        m = re.match(r"(\d+)_\d+\.(\w+)$", avatar)
        if m:
            return f"{assets_prefix}/avatars/{m.group(1)}.{m.group(2)}"
    return None


def _rewrite_css_imports(css_path: Path) -> None:
    """Strip ?hash=... query strings from @import URLs for static hosting."""
    content = css_path.read_text(encoding="utf-8", errors="replace")
    # url("file.css?hash=abc") → url("file.css")
    content = re.sub(r'(url\(["\']?[^"\')]+?)\?[^"\')]*(["\']?\))', r'\1\2', content)
    css_path.write_text(content, encoding="utf-8")


def copy_assets(dump_dir: Path, output_dir: Path) -> None:
    """Copy CSS, images, smilies, avatars, and attachments into output/assets/."""
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

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

    # --- Avatars ---
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

    # --- Attachments ---
    files_src = dump_dir / "files"
    if files_src.exists():
        dest = assets / "attachments"
        dest.mkdir(exist_ok=True)
        shutil.copytree(files_src, dest, dirs_exist_ok=True)
        logger.info("Copied attachments")


def build_attachments_map(db: PhpbbDatabase, post_ids: list[int]) -> dict[int, list[dict]]:
    """Return {post_id: [attachment, ...]} for a set of post IDs."""
    result: dict[int, list[dict]] = {}
    for pid in post_ids:
        rows = db.get_attachments(pid)
        if rows:
            result[pid] = rows
    return result


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
        if f["parent_id"] == 0:
            roots.append(node)
        else:
            by_id[f["parent_id"]]["children"].append(node)
    return roots


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
                 total_posts: int, site_name: str = "") -> None:
    tmpl = env.get_template("index.html")
    html = tmpl.render(
        page_title="Board Index",
        forum_tree=forum_tree,
        total_posts=total_posts,
        assets="assets",
        root="",
        site_name=site_name,
    )
    (out / "index.html").write_text(html, encoding="utf-8")
    logger.info("Rendered index.html")


def render_forums(env: jinja2.Environment, out: Path, db: PhpbbDatabase,
                  users: dict[int, dict], parser: PhpbbBBCodeParser,
                  forums: list[dict], site_name: str = "") -> None:
    forums_dir = out / "forums"
    forums_dir.mkdir(exist_ok=True)
    tmpl = env.get_template("forum.html")

    for forum in process_forum_descs(forums, parser):
        topics = db.get_topics(forum["forum_id"])
        # Annotate each topic with its starter's username for the template
        for t in topics:
            poster = users.get(t.get("topic_poster", 0))
            t["topic_poster_name"] = poster["username"] if poster else ""

        html = tmpl.render(
            page_title=forum["forum_name"],
            forum=forum,
            topics=topics,
            assets="../assets",
            root="../",
            site_name=site_name,
        )
        (forums_dir / f"{forum['forum_id']}.html").write_text(html, encoding="utf-8")

    logger.info("Rendered %d forum pages", len(forums))


def render_topics(env: jinja2.Environment, out: Path, db: PhpbbDatabase,
                  users: dict[int, dict], smilies: list[dict],
                  ranks: list[dict], custom_bbcodes: list[dict],
                  forums: list[dict], site_name: str = "") -> int:
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
        )

        rendered_posts = []
        for post in posts:
            uid = post.get("bbcode_uid", "")
            text = post.get("post_text", "")
            rendered = parser.convert(text, uid=uid, post_id=post["post_id"])

            author = users.get(post.get("poster_id", 0))
            author_ctx = None
            if author:
                rank = get_user_rank(author, ranks)
                sig_html = ""
                if author.get("user_sig"):
                    sig_html = parser.convert(
                        author["user_sig"],
                        uid=author.get("user_sig_bbcode_uid", ""),
                        post_id=None,
                    )
                author_ctx = {
                    **author,
                    "avatar_url": avatar_url(author, "../assets"),
                    "rank_title": author.get("user_custom_title") or (rank["rank_title"] if rank else ""),
                    "rank_image": rank.get("rank_image", "") if rank else "",
                    "sig_html": sig_html,
                }

            rendered_posts.append({
                **post,
                "rendered_html": rendered,
                "author": author_ctx,
            })

        forum_name = forum_names.get(topic["forum_id"], "")
        html = tmpl.render(
            page_title=topic["topic_title"],
            topic=topic,
            forum_name=forum_name,
            posts=rendered_posts,
            assets="../assets",
            root="../",
            site_name=site_name,
        )
        (topics_dir / f"{topic['topic_id']}.html").write_text(html, encoding="utf-8")

    logger.info("Rendered %d topic pages (%d posts)", len(all_topics), total_posts)
    return total_posts


def render_users(env: jinja2.Environment, out: Path, db: PhpbbDatabase,
                 smilies: list[dict], ranks: list[dict],
                 custom_bbcodes: list[dict], site_name: str = "") -> None:
    users_dir = out / "users"
    users_dir.mkdir(exist_ok=True)
    tmpl = env.get_template("user.html")

    parser = PhpbbBBCodeParser(
        smilies=smilies,
        attachments={},
        custom_bbcodes=custom_bbcodes,
        assets_prefix="../assets",
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
            user={**user, "avatar_url": avatar_url(user, "../assets")},
            rank=rank,
            sig_html=sig_html,
            assets="../assets",
            root="../",
            site_name=site_name,
        )
        (users_dir / f"{user['user_id']}.html").write_text(html, encoding="utf-8")

    logger.info("Rendered %d user pages", len(db.get_all_users()))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate(dump_dir: str, output_dir: str) -> None:
    dump = Path(dump_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # --- DB ---
    table_prefix = read_table_prefix(dump / "config.php")
    logger.info("Table prefix: %s", table_prefix)

    sql_files = sorted(dump.glob("*.sql"))
    if not sql_files:
        raise FileNotFoundError(f"No .sql file found in {dump}")
    sql_file = sql_files[0]
    logger.info("Using SQL dump: %s", sql_file)

    db_path = str(out / ".phpbb_archive.db")
    logger.info("Importing MySQL dump → SQLite ...")
    import_mysql_dump(str(sql_file), db_path)
    db = PhpbbDatabase(db_path, table_prefix=table_prefix)

    site_name = db.get_config("sitename") or sql_file.stem
    logger.info("Site name: %s", site_name)

    # --- Assets ---
    logger.info("Copying assets ...")
    copy_assets(dump, out)

    # --- Lookup tables ---
    smilies = db.get_smilies()
    ranks = db.get_ranks()
    custom_bbcodes = db.get_bbcodes()
    users: dict[int, dict] = {u["user_id"]: u for u in db.get_all_users()}
    forums = db.get_forums()

    # --- Templates ---
    template_dir = Path(__file__).parent / "templates"
    env = build_jinja_env(template_dir)

    # --- Shared parser (used for forum descs and user sigs as well as posts) ---
    shared_parser = PhpbbBBCodeParser(
        smilies=smilies,
        attachments={},
        custom_bbcodes=custom_bbcodes,
        assets_prefix="assets",  # index-level path; topics/forums use their own parser
    )

    # Enrich forums with the topic_id of their last post (for linking)
    for f in forums:
        post_id = f.get("forum_last_post_id") or 0
        f["forum_last_topic_id"] = db.get_post_topic_id(post_id) if post_id else None

    # --- Pages ---
    forum_tree = build_forum_tree(process_forum_descs(forums, shared_parser))

    total_posts = render_topics(env, out, db, users, smilies, ranks, custom_bbcodes, forums, site_name=site_name)
    render_forums(env, out, db, users, shared_parser, forums, site_name=site_name)
    render_users(env, out, db, smilies, ranks, custom_bbcodes, site_name=site_name)
    render_index(env, out, forum_tree or [], total_posts, site_name=site_name)

    db.close()
    logger.info("Done. Output: %s", out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a static HTML archive from a phpBB MySQL dump")
    parser.add_argument("--dump", default="dump", help="Path to dump/ directory")
    parser.add_argument("--output", default="output", help="Path to output/ directory")
    args = parser.parse_args()
    generate(args.dump, args.output)


if __name__ == "__main__":
    main()
