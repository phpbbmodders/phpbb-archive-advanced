"""MySQL dump → SQLite import and phpBB database query helpers."""

import html
import re
import sqlite3
import logging

logger = logging.getLogger(__name__)


def _split_by_string_literals(sql: str, backslash_escapes: bool = True) -> list[tuple[str, bool]]:
    """Split sql into (chunk, is_literal) pairs at single-quoted string
    boundaries. A doubled '' is always honored as an escaped quote (valid
    ANSI SQL); backslash_escapes additionally honors a backslash-escaped
    \\' the way raw mysqldump output actually quotes a value.

    backslash_escapes must be False once escape sequences have already
    been resolved to literal characters (see import_mysql_dump) — a
    literal backslash at that point is just a character, not the start of
    an escape sequence, and treating it as one would misjudge where a
    string actually ends.

    Covers the whole input in order, so a caller can transform only the
    non-literal chunks (actual SQL syntax) while leaving literal chunks
    (real row data) untouched."""
    segments: list[tuple[str, bool]] = []
    buf: list[str] = []
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if not in_string:
            if c == "'":
                if buf:
                    segments.append(("".join(buf), False))
                    buf = []
                in_string = True
                buf.append(c)
            else:
                buf.append(c)
            i += 1
        else:
            if backslash_escapes and c == "\\" and i + 1 < n:
                buf.append(sql[i:i + 2])
                i += 2
                continue
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                buf.append(c)
                segments.append(("".join(buf), True))
                buf = []
                in_string = False
                i += 1
                continue
            buf.append(c)
            i += 1
    if buf:
        segments.append(("".join(buf), in_string))
    return segments


def _apply_outside_strings(sql: str, transform, backslash_escapes: bool = True) -> str:
    """Apply `transform` to every chunk of sql that is NOT inside a
    string literal (see _split_by_string_literals), leaving literal
    chunks unchanged. Keeps MySQL-syntax-only substitutions (comments,
    type keywords, backtick identifiers) from also matching inside real
    row data that happens to look similar — e.g. a post discussing SQL
    that itself contains "unsigned", a C-style /* comment */, or a
    backtick, or code ending in a trailing comma before a ')'."""
    return "".join(
        transform(chunk) if not is_literal else chunk
        for chunk, is_literal in _split_by_string_literals(sql, backslash_escapes)
    )


_MYSQL_ESCAPE_MAP = {
    "'": "''",  # → SQL-standard escaped quote, so the literal stays open
    '"': '"',
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    "\\": "\\",
    "Z": "\x1a",
}


def _unescape_mysql_string_literals(sql: str) -> str:
    """Resolve MySQL's backslash-escaped value content into literal
    characters, in one left-to-right pass. A chain of independent global
    .replace() calls (the previous approach) can misparse a value that
    itself ends in an escaped backslash immediately followed by the real
    closing quote (e.g. a stored value ending "...\\\\", a literal
    backslash) — replacing \\' → '' first would consume the second
    backslash together with that closing quote as if they were an
    escaped-quote pair, corrupting where the string actually ends. A
    single pass has no such ambiguity: each escape is resolved and
    consumed exactly once, left to right. An unrecognized \\X drops the
    backslash and keeps X, matching MySQL's own fallback behavior."""
    out = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "\\" and i + 1 < n:
            nxt = sql[i + 1]
            out.append(_MYSQL_ESCAPE_MAP.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def import_mysql_dump(sql_path: str, db_path: str) -> None:
    """Convert a mysqldump file to a SQLite database.

    Handles MySQL-specific syntax: backtick quoting, AUTO_INCREMENT,
    ENGINE=, unsigned integers, charset declarations, etc. — applied only
    outside string literals (see _apply_outside_strings), so real row
    data is never mistaken for the surrounding SQL syntax.
    """
    import os
    if os.path.exists(db_path):
        os.remove(db_path)

    with open(sql_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Remove MySQL-specific constructs line by line. Comments are left
    # alone here — stripped later, together with the rest of the
    # MySQL-only syntax, once string literals are unambiguous to find
    # (see below) — a line-by-line prefix check can't accidentally match
    # inside a value's content either way, since a raw mysqldump escapes
    # any embedded newline in a value rather than emitting one literally.
    lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        # Skip single-line comments, MySQL commands, and inline index defs
        u = stripped.upper()
        if (stripped.startswith("--")
                or u.startswith("LOCK ")
                or u.startswith("UNLOCK ")
                or u.startswith("SET ")
                or u.startswith("CREATE DATABASE")
                or u.startswith("USE ")
                or u.startswith("DROP TABLE")
                or u.startswith("KEY ")
                or u.startswith("UNIQUE KEY ")
                or u.startswith("UNIQUE INDEX ")
                or u.startswith("FULLTEXT KEY ")
                or u in ("COMMIT;", "COMMIT", "ROLLBACK;", "ROLLBACK",
                         "START TRANSACTION;", "START TRANSACTION",
                         "BEGIN;", "BEGIN")):
            continue
        lines.append(line)

    sql = "\n".join(lines)

    # MySQL dump escapes special chars in string values; SQLite stores
    # them literally. Resolve those escapes before anything below, which
    # assumes backslashes no longer mean anything special (see
    # _split_by_string_literals's backslash_escapes parameter).
    sql = _unescape_mysql_string_literals(sql)

    def _strip_mysql_syntax(chunk: str) -> str:
        # Comments (including MySQL conditional comments /*!...*/)
        chunk = re.sub(r"/\*.*?\*/", "", chunk, flags=re.DOTALL)
        # Removing KEY lines above can leave a trailing comma before the
        # closing paren: col TEXT,\n) → col TEXT\n)
        chunk = re.sub(r",(\s*\))", r"\1", chunk)
        # Strip MySQL-specific type modifiers and keywords
        chunk = re.sub(r"\bunsigned\b", "", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\bAUTO_INCREMENT\b", "", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\)\s*ENGINE=.*?;", ");", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"DEFAULT CHARSET=\w+", "", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"COLLATE \w+", "", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"CHARACTER SET \w+", "", chunk, flags=re.IGNORECASE)
        # Replace MySQL integer types with SQLite INTEGER (order matters: specific before generic)
        chunk = re.sub(r"\bmediumint\(\d+\)", "INTEGER", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\bsmallint\(\d+\)", "INTEGER", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\btinyint\(\d+\)", "INTEGER", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\bbigint\(\d+\)", "INTEGER", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\bint\(\d+\)", "INTEGER", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\bmediumtext\b", "TEXT", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\blongtext\b", "TEXT", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"\bvarchar\(\d+\)", "TEXT", chunk, flags=re.IGNORECASE)
        # Replace backticks with double quotes for identifiers
        return chunk.replace("`", '"')

    # backslash_escapes=False: _unescape_mysql_string_literals() above
    # already resolved every escape sequence, so a backslash from here on
    # is just a literal character, not the start of one.
    sql = _apply_outside_strings(sql, _strip_mysql_syntax, backslash_escapes=False)

    conn = sqlite3.connect(db_path)
    conn.executescript(sql)
    conn.close()


class PhpbbDatabase:
    """Query helper for phpBB tables in a SQLite database."""

    def __init__(self, db_path: str, table_prefix: str = "phpbb_"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.prefix = table_prefix

    def _table(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _unescape_fields(rows: list[dict], *fields: str) -> list[dict]:
        """Undo phpBB's stored htmlspecialchars() encoding on plain display
        fields (names, titles, subjects) so templates can HTML-escape them
        normally on render instead of double-encoding already-encoded text.

        Not for post_text/user_sig/forum_desc: those go through the BBCode
        parser, which expects them still encoded.
        """
        for row in rows:
            for field in fields:
                if row.get(field):
                    row[field] = html.unescape(row[field])
        return rows

    def get_forums(self) -> list[dict]:
        return self._unescape_fields(
            self._query(
                f'SELECT * FROM "{self._table("forums")}" ORDER BY left_id'
            ),
            "forum_name", "forum_last_post_subject",
        )

    def get_topics(self, forum_id: int) -> list[dict]:
        # topic_visibility = 1 is phpBB's own ITEM_APPROVED — the same
        # condition phpBB itself applies before showing a topic to an
        # anonymous/non-moderator visitor. 0/2/3 (unapproved, soft-deleted,
        # needs-reapproval) are moderation states, not public content.
        #
        # topic_type: phpBB pins announcements (2) and global announcements
        # (3, see get_global_announcements()) above stickies (1), which sort
        # above ordinary topics (0) — matching phpBB's own real forum-view
        # ordering. Within each tier, the forum's normal order (last-post-
        # time descending) is unchanged.
        return self._unescape_fields(
            self._query(
                f'SELECT * FROM "{self._table("topics")}" WHERE forum_id = ? AND topic_visibility = 1 '
                f"ORDER BY CASE topic_type WHEN 2 THEN 0 WHEN 3 THEN 0 WHEN 1 THEN 1 ELSE 2 END, "
                f"topic_last_post_time DESC",
                (forum_id,),
            ),
            "topic_title",
        )

    def get_global_announcements(self, exclude_forum_ids: set[int] | None = None) -> list[dict]:
        """Board-wide announcements (topic_type = 3, phpBB's TOPIC_GLOBAL) —
        pinned above every forum's own topic listing, not just the forum
        they were physically posted in, matching phpBB's own real behavior.
        Callers merge this into each forum's own get_topics() result for
        display; it's intentionally not folded into get_topics() itself so
        a global topic's own page is still generated exactly once, from its
        actual forum_id."""
        if exclude_forum_ids:
            placeholders = ",".join("?" * len(exclude_forum_ids))
            rows = self._query(
                f'SELECT * FROM "{self._table("topics")}" WHERE topic_type = 3 AND topic_visibility = 1 '
                f"AND forum_id NOT IN ({placeholders}) ORDER BY topic_last_post_time DESC",
                tuple(exclude_forum_ids),
            )
        else:
            rows = self._query(
                f'SELECT * FROM "{self._table("topics")}" WHERE topic_type = 3 AND topic_visibility = 1 '
                f"ORDER BY topic_last_post_time DESC",
            )
        return self._unescape_fields(rows, "topic_title")

    def get_post_topic_id(self, post_id: int) -> int | None:
        rows = self._query(
            f'SELECT topic_id FROM "{self._table("posts")}" WHERE post_id = ?',
            (post_id,),
        )
        return rows[0]["topic_id"] if rows else None

    def get_posts(self, topic_id: int) -> list[dict]:
        # post_visibility = 1: same reasoning as get_topics() above — a
        # post can be individually hidden/unapproved/pending-reapproval
        # even inside an otherwise-visible topic.
        return self._query(
            f'SELECT * FROM "{self._table("posts")}" WHERE topic_id = ? AND post_visibility = 1 '
            f"ORDER BY post_time ASC",
            (topic_id,),
        )

    def get_user(self, user_id: int) -> dict | None:
        rows = self._unescape_fields(
            self._query(
                f'SELECT * FROM "{self._table("users")}" WHERE user_id = ?',
                (user_id,),
            ),
            "username",
        )
        return rows[0] if rows else None

    def get_all_users(self) -> list[dict]:
        return self._unescape_fields(
            self._query(
                f'SELECT * FROM "{self._table("users")}" WHERE user_type != 2 '
                f"ORDER BY username"
            ),
            "username",
        )

    def get_attachments(self, post_id: int) -> list[dict]:
        # post_msg_id is shared with private messages (it holds a msg_id
        # there instead of a post_id — separate id sequences, so a msg_id
        # can numerically collide with an unrelated post_id). in_message=0
        # is required, not just a post_id match, or a private message's
        # attachment can render on a public topic page.
        # attach_id DESC matches phpBB's own ordering (viewtopic.php's
        # attachment query) — the numeric index in a stored [attachment=N]
        # tag was assigned against that order when the post was originally
        # rendered, so returning them in a different order would pair a
        # tag with the wrong attachment.
        return self._query(
            f'SELECT * FROM "{self._table("attachments")}" WHERE post_msg_id = ? AND in_message = 0 '
            f"ORDER BY attach_id DESC",
            (post_id,),
        )

    def get_poll_options(self, topic_id: int) -> list[dict]:
        return self._query(
            f'SELECT * FROM "{self._table("poll_options")}" WHERE topic_id = ? '
            f"ORDER BY poll_option_id",
            (topic_id,),
        )

    def get_all_attachments(self, exclude_forum_ids: set[int] | None = None) -> list[dict]:
        if exclude_forum_ids:
            placeholders = ",".join("?" * len(exclude_forum_ids))
            return self._query(
                f'SELECT a.* FROM "{self._table("attachments")}" a '
                f'JOIN "{self._table("posts")}" p ON a.post_msg_id = p.post_id '
                f"WHERE p.forum_id NOT IN ({placeholders})",
                tuple(exclude_forum_ids),
            )
        return self._query(f'SELECT * FROM "{self._table("attachments")}"')

    def get_private_message_attachment_physical_filenames(self) -> set[str]:
        """physical_filename values for private-message attachments
        (in_message=1) — these must never be copied into assets/, the same
        as an excluded-forum attachment. Private messages aren't public
        content at all, regardless of forum exclusion."""
        rows = self._query(
            f'SELECT DISTINCT physical_filename FROM "{self._table("attachments")}" WHERE in_message = 1'
        )
        return {r["physical_filename"] for r in rows}

    def get_attachment_physical_filenames_in_forums(self, forum_ids: set[int]) -> set[str]:
        """physical_filename values for attachments belonging to posts in
        the given forums — used to keep excluded-forum attachments out of
        assets/ entirely rather than just unlinked."""
        if not forum_ids:
            return set()
        placeholders = ",".join("?" * len(forum_ids))
        rows = self._query(
            f'SELECT DISTINCT a.physical_filename FROM "{self._table("attachments")}" a '
            f'JOIN "{self._table("posts")}" p ON a.post_msg_id = p.post_id '
            f"WHERE p.forum_id IN ({placeholders})",
            tuple(forum_ids),
        )
        return {r["physical_filename"] for r in rows}

    def get_all_post_texts(self, exclude_forum_ids: set[int] | None = None) -> list[str]:
        if exclude_forum_ids:
            placeholders = ",".join("?" * len(exclude_forum_ids))
            rows = self._query(
                f'SELECT post_text FROM "{self._table("posts")}" WHERE forum_id NOT IN ({placeholders})',
                tuple(exclude_forum_ids),
            )
        else:
            rows = self._query(f'SELECT post_text FROM "{self._table("posts")}"')
        return [r["post_text"] for r in rows if r["post_text"]]

    def get_all_poll_texts(self, exclude_forum_ids: set[int] | None = None) -> list[str]:
        """poll_title (phpbb_topics) and poll_option_text (phpbb_poll_options,
        joined to phpbb_topics for its forum_id) — both go through the same
        BBCode/XML parser as post text, and can equally contain [img]."""
        if exclude_forum_ids:
            placeholders = ",".join("?" * len(exclude_forum_ids))
            title_rows = self._query(
                f'SELECT poll_title FROM "{self._table("topics")}" '
                f"WHERE forum_id NOT IN ({placeholders})",
                tuple(exclude_forum_ids),
            )
            option_rows = self._query(
                f'SELECT po.poll_option_text FROM "{self._table("poll_options")}" po '
                f'JOIN "{self._table("topics")}" t ON t.topic_id = po.topic_id '
                f"WHERE t.forum_id NOT IN ({placeholders})",
                tuple(exclude_forum_ids),
            )
        else:
            title_rows = self._query(f'SELECT poll_title FROM "{self._table("topics")}"')
            option_rows = self._query(f'SELECT poll_option_text FROM "{self._table("poll_options")}"')
        texts = [r["poll_title"] for r in title_rows if r["poll_title"]]
        texts.extend(r["poll_option_text"] for r in option_rows if r["poll_option_text"])
        return texts

    def get_smilies(self) -> list[dict]:
        return self._query(f'SELECT * FROM "{self._table("smilies")}"')

    def get_ranks(self) -> list[dict]:
        return self._query(f'SELECT * FROM "{self._table("ranks")}"')

    def get_bbcodes(self) -> list[dict]:
        return self._query(f'SELECT * FROM "{self._table("bbcodes")}"')

    def get_config(self, name: str) -> str | None:
        rows = self._query(
            f'SELECT config_value FROM "{self._table("config")}" WHERE config_name = ?',
            (name,),
        )
        return rows[0]["config_value"] if rows else None

    def get_style_path(self, style_id: int) -> str | None:
        rows = self._query(
            f'SELECT style_path FROM "{self._table("styles")}" WHERE style_id = ?',
            (style_id,),
        )
        return rows[0]["style_path"] if rows else None

    def close(self):
        self.conn.close()
