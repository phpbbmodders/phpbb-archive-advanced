"""MySQL dump → SQLite import and phpBB database query helpers."""

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

    def get_forums(self) -> list[dict]:
        return self._query(
            f'SELECT * FROM "{self._table("forums")}" ORDER BY left_id'
        )

    def get_topics(self, forum_id: int) -> list[dict]:
        return self._query(
            f'SELECT * FROM "{self._table("topics")}" WHERE forum_id = ? '
            f"ORDER BY topic_last_post_time DESC",
            (forum_id,),
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
        rows = self._query(
            f'SELECT * FROM "{self._table("users")}" WHERE user_id = ?',
            (user_id,),
        )
        return rows[0] if rows else None

    def get_all_users(self) -> list[dict]:
        return self._query(
            f'SELECT * FROM "{self._table("users")}" WHERE user_type != 2 '
            f"ORDER BY username"
        )

    def get_attachments(self, post_id: int) -> list[dict]:
        return self._query(
            f'SELECT * FROM "{self._table("attachments")}" WHERE post_msg_id = ?',
            (post_id,),
        )

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

    def close(self):
        self.conn.close()
