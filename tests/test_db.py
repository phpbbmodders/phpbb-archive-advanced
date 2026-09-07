import os
import sqlite3
import tempfile
import pytest
from generator.db import import_mysql_dump, PhpbbDatabase

FIXTURE_SQL = """
CREATE TABLE `phpbb_forums` (
  `forum_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `forum_name` varchar(255) NOT NULL DEFAULT '',
  `forum_desc` text NOT NULL,
  `forum_topics_approved` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `forum_posts_approved` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `forum_last_post_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `forum_last_post_time` int(11) unsigned NOT NULL DEFAULT 0,
  `forum_last_poster_name` varchar(255) NOT NULL DEFAULT '',
  `parent_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `left_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `right_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`forum_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_forums` VALUES (1,'General','General discussion',5,20,100,1700000000,'testuser',0,1,2);

CREATE TABLE `phpbb_users` (
  `user_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `username` varchar(255) NOT NULL DEFAULT '',
  `user_avatar` varchar(255) NOT NULL DEFAULT '',
  `user_avatar_type` varchar(255) NOT NULL DEFAULT '',
  `user_regdate` int(11) unsigned NOT NULL DEFAULT 0,
  `user_posts` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `user_sig` mediumtext NOT NULL,
  `user_rank` mediumint(8) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_users` VALUES (2,'testuser','','',1600000000,10,'',0);

CREATE TABLE `phpbb_topics` (
  `topic_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `forum_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `topic_title` varchar(255) NOT NULL DEFAULT '',
  `topic_poster` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `topic_time` int(11) unsigned NOT NULL DEFAULT 0,
  `topic_views` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `topic_posts_approved` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `topic_last_post_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `topic_last_post_time` int(11) unsigned NOT NULL DEFAULT 0,
  `topic_last_poster_name` varchar(255) NOT NULL DEFAULT '',
  `topic_status` tinyint(3) NOT NULL DEFAULT 0,
  `topic_visibility` tinyint(3) NOT NULL DEFAULT 1,
  PRIMARY KEY (`topic_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_topics` VALUES (1,1,'Test Topic',2,1600000000,50,3,3,1700000000,'testuser',0,1);
INSERT INTO `phpbb_topics` VALUES (2,1,'Hidden Topic',2,1600000000,0,0,0,1700000000,'testuser',0,3);

CREATE TABLE `phpbb_posts` (
  `post_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `topic_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `forum_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `poster_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `post_time` int(11) unsigned NOT NULL DEFAULT 0,
  `post_text` mediumtext NOT NULL,
  `post_subject` varchar(255) NOT NULL DEFAULT '',
  `bbcode_uid` varchar(8) NOT NULL DEFAULT '',
  `enable_bbcode` tinyint(1) unsigned NOT NULL DEFAULT 1,
  `post_visibility` tinyint(3) NOT NULL DEFAULT 1,
  PRIMARY KEY (`post_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_posts` VALUES (1,1,1,2,1600000000,'Hello [b:abc123]world[/b:abc123]','Test Topic','abc123',1,1);
INSERT INTO `phpbb_posts` VALUES (2,1,1,2,1600100000,'A reply','Re: Test Topic','',1,1);
INSERT INTO `phpbb_posts` VALUES (3,1,1,2,1600200000,'Needs reapproval after edit','Re: Test Topic','',1,3);

CREATE TABLE `phpbb_attachments` (
  `attach_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `post_msg_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `topic_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `in_message` tinyint(1) unsigned NOT NULL DEFAULT 0,
  `physical_filename` varchar(255) NOT NULL DEFAULT '',
  `real_filename` varchar(255) NOT NULL DEFAULT '',
  `download_count` mediumint(8) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`attach_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_attachments` VALUES (1,1,1,0,'abc123.png','photo.png',0);
INSERT INTO `phpbb_attachments` VALUES (2,1,0,1,'pm_secret.png','secret.png',0);

CREATE TABLE `phpbb_smilies` (
  `smiley_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `code` varchar(50) NOT NULL DEFAULT '',
  `smiley_url` varchar(50) NOT NULL DEFAULT '',
  `emotion` varchar(50) NOT NULL DEFAULT '',
  PRIMARY KEY (`smiley_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_smilies` VALUES (1,':)','icon_e_smile.gif','Smile');

CREATE TABLE `phpbb_ranks` (
  `rank_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `rank_title` varchar(255) NOT NULL DEFAULT '',
  `rank_min` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `rank_special` tinyint(1) unsigned NOT NULL DEFAULT 0,
  `rank_image` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`rank_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `phpbb_bbcodes` (
  `bbcode_id` smallint(4) unsigned NOT NULL DEFAULT 0,
  `bbcode_tag` varchar(16) NOT NULL DEFAULT '',
  `bbcode_match` varchar(255) NOT NULL DEFAULT '',
  `bbcode_tpl` mediumtext NOT NULL,
  PRIMARY KEY (`bbcode_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
"""


@pytest.fixture
def db_path(tmp_path):
    """Create a SQLite DB from the MySQL fixture."""
    sql_file = tmp_path / "test.sql"
    sql_file.write_text(FIXTURE_SQL, encoding="utf-8")
    db_file = tmp_path / "test.db"
    import_mysql_dump(str(sql_file), str(db_file))
    return str(db_file)


def test_import_creates_tables(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    forums = db.get_forums()
    assert len(forums) == 1
    assert forums[0]["forum_name"] == "General"


def _import_single_value(tmp_path, value: str) -> str:
    """Import a minimal one-row dump and return the stored value verbatim
    — for checking that import_mysql_dump() doesn't mistake real row data
    for MySQL-only syntax it strips (see the tests below)."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    sql_file = tmp_path / "content.sql"
    sql_file.write_text(
        "CREATE TABLE `t` (`v` mediumtext NOT NULL);\n"
        f"INSERT INTO `t` VALUES ('{escaped}');\n",
        encoding="utf-8",
    )
    db_file = tmp_path / "content.db"
    import_mysql_dump(str(sql_file), str(db_file))
    conn = sqlite3.connect(str(db_file))
    return conn.execute('SELECT v FROM "t"').fetchone()[0]


class TestImportPreservesRowContent:
    # import_mysql_dump()'s MySQL-syntax cleanup (comments, type keywords,
    # backtick identifiers, a trailing-comma fixup) used to run as plain
    # regexes over the whole file, so it could match text that merely
    # *looks* like SQL syntax inside a post's own stored content — see
    # phpbb-archive security review, finding 4.

    def test_preserves_ddl_keywords_and_comment_syntax(self, tmp_path):
        value = "unsigned mediumtext /*sample*/ `tick`"
        assert _import_single_value(tmp_path, value) == value

    def test_preserves_trailing_comma_before_paren(self, tmp_path):
        value = "call myFunc(a, b,)  and enjoy"
        assert _import_single_value(tmp_path, value) == value


def test_get_topics(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    topics = db.get_topics(forum_id=1)
    assert len(topics) == 1
    assert topics[0]["topic_title"] == "Test Topic"


def test_get_topics_excludes_hidden(db_path):
    # topic_visibility=3 (needs-reapproval, same as soft-deleted/unapproved
    # for a public viewer) must never be returned — see phpbb-archive
    # security review, finding 8.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    topics = db.get_topics(forum_id=1)
    assert all(t["topic_id"] != 2 for t in topics)


def test_get_posts(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    posts = db.get_posts(topic_id=1)
    assert len(posts) == 2
    assert "world" in posts[0]["post_text"]


def test_get_posts_excludes_hidden(db_path):
    # post_visibility=3 on an otherwise-visible topic must be excluded —
    # same finding as above, but per-post rather than per-topic.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    posts = db.get_posts(topic_id=1)
    assert all(p["post_id"] != 3 for p in posts)


def test_get_attachments_excludes_private_messages(db_path):
    # post_msg_id is shared with private messages, where it holds a
    # msg_id instead of a post_id — separate id sequences that can
    # numerically collide. A PM attachment (in_message=1) sharing
    # post_msg_id=1 with the real post must never come back for that
    # post — see phpbb-archive security review, finding 2.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    attachments = db.get_attachments(post_id=1)
    assert len(attachments) == 1
    assert attachments[0]["real_filename"] == "photo.png"


def test_get_private_message_attachment_physical_filenames(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    names = db.get_private_message_attachment_physical_filenames()
    assert names == {"pm_secret.png"}


def test_get_user(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    user = db.get_user(user_id=2)
    assert user["username"] == "testuser"


def test_get_smilies(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    smilies = db.get_smilies()
    assert len(smilies) == 1
    assert smilies[0]["code"] == ":)"
