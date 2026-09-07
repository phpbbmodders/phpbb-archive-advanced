"""MySQL dump → SQLite import and phpBB database query helpers."""

import html
import re
import sqlite3
import logging

logger = logging.getLogger(__name__)


def import_mysql_dump(sql_path: str, db_path: str) -> None:
    """Convert a mysqldump file to a SQLite database.

    Handles MySQL-specific syntax: backtick quoting, AUTO_INCREMENT,
    ENGINE=, unsigned integers, charset declarations, etc.
    """
    import os
    if os.path.exists(db_path):
        os.remove(db_path)

    with open(sql_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Remove multi-line /* ... */ comments (including MySQL conditional comments /*!...*/)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)

    # Remove MySQL-specific constructs line by line
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

    # MySQL dump escapes special chars in string values; SQLite stores them literally.
    # Convert MySQL escape sequences to actual characters before importing.
    sql = sql.replace("\\'", "''")   # \' → '' (SQLite-style quote escape)
    sql = sql.replace('\\"', '"')    # \" → " (double-quote in single-quoted strings)
    sql = sql.replace("\\n", "\n")   # \n → newline
    sql = sql.replace("\\t", "\t")   # \t → tab
    sql = sql.replace("\\r", "\r")   # \r → carriage return
    sql = sql.replace("\\\\", "\\")  # \\ → backslash (must be last)

    # Removing KEY lines can leave a trailing comma before the closing paren:
    #   col TEXT,\n) → col TEXT\n)
    sql = re.sub(r",(\s*\))", r"\1", sql)

    # Strip MySQL-specific type modifiers and keywords
    sql = re.sub(r"\bunsigned\b", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bAUTO_INCREMENT\b", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\)\s*ENGINE=.*?;", ");", sql, flags=re.IGNORECASE)
    sql = re.sub(r"DEFAULT CHARSET=\w+", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"COLLATE \w+", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"CHARACTER SET \w+", "", sql, flags=re.IGNORECASE)
    # Replace MySQL integer types with SQLite INTEGER (order matters: specific before generic)
    sql = re.sub(r"\bmediumint\(\d+\)", "INTEGER", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bsmallint\(\d+\)", "INTEGER", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\btinyint\(\d+\)", "INTEGER", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bbigint\(\d+\)", "INTEGER", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bint\(\d+\)", "INTEGER", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bmediumtext\b", "TEXT", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\blongtext\b", "TEXT", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bvarchar\(\d+\)", "TEXT", sql, flags=re.IGNORECASE)
    # Replace backticks with double quotes for identifiers
    sql = sql.replace("`", '"')

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
        return self._unescape_fields(
            self._query(
                f'SELECT * FROM "{self._table("topics")}" WHERE forum_id = ? '
                f"ORDER BY topic_last_post_time DESC",
                (forum_id,),
            ),
            "topic_title",
        )

    def get_post_topic_id(self, post_id: int) -> int | None:
        rows = self._query(
            f'SELECT topic_id FROM "{self._table("posts")}" WHERE post_id = ?',
            (post_id,),
        )
        return rows[0]["topic_id"] if rows else None

    def get_posts(self, topic_id: int) -> list[dict]:
        return self._query(
            f'SELECT * FROM "{self._table("posts")}" WHERE topic_id = ? '
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
        return self._query(
            f'SELECT * FROM "{self._table("attachments")}" WHERE post_msg_id = ? AND in_message = 0',
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
