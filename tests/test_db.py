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
  `forum_password` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`forum_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_forums` VALUES (1,'General','General discussion',5,20,100,1700000000,'testuser',0,1,2,'');
INSERT INTO `phpbb_forums` VALUES (2,'Ordering Forum','Sticky/announce ordering fixture',3,3,200,3000,'testuser',0,3,4,'');
INSERT INTO `phpbb_forums` VALUES (3,'Third Forum','Global announcement home forum',1,1,300,2000,'testuser',0,5,6,'');
INSERT INTO `phpbb_forums` VALUES (4,'Locked Forum','Password-protected fixture',0,0,0,0,'',0,7,8,'$H$9somehash');

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
  `poll_title` varchar(255) NOT NULL DEFAULT '',
  `topic_type` tinyint(3) NOT NULL DEFAULT 0,
  PRIMARY KEY (`topic_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_topics` VALUES (1,1,'Test Topic',2,1600000000,50,3,3,1700000000,'testuser',0,1,'',0);
INSERT INTO `phpbb_topics` VALUES (2,1,'Hidden Topic',2,1600000000,0,0,0,1700000000,'testuser',0,3,'',0);
INSERT INTO `phpbb_topics` VALUES (3,1,'Poll Topic',2,1600000000,0,1,0,1700000000,'testuser',0,1,'Pick one',0);

-- Sticky(1)/announce(2)/normal(0) ordering fixture: last_post_time alone
-- would sort these Normal > Announce > Sticky, but tier priority must
-- place Announce above Sticky above Normal regardless.
INSERT INTO `phpbb_topics` VALUES (10,2,'Normal Topic',2,1600000000,0,1,0,3000,'testuser',0,1,'',0);
INSERT INTO `phpbb_topics` VALUES (11,2,'Sticky Topic',2,1600000000,0,1,0,1000,'testuser',0,1,'',1);
INSERT INTO `phpbb_topics` VALUES (12,2,'Announce Topic',2,1600000000,0,1,0,2000,'testuser',0,1,'',2);

-- Global announcement (topic_type=3) fixture — physically posted in forum
-- 3, but get_global_announcements() must return it board-wide.
INSERT INTO `phpbb_topics` VALUES (20,3,'Global Announcement',2,1600000000,0,1,0,2500,'testuser',0,1,'',3);

CREATE TABLE `phpbb_poll_options` (
  `poll_option_id` tinyint(4) NOT NULL DEFAULT 0,
  `topic_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `poll_option_text` text NOT NULL,
  `poll_option_total` mediumint(8) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`poll_option_id`, `topic_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_poll_options` VALUES (1,3,'Option A',3);
INSERT INTO `phpbb_poll_options` VALUES (2,3,'Option B',1);

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
INSERT INTO `phpbb_attachments` VALUES (3,2,1,0,'first.png','first.png',0);
INSERT INTO `phpbb_attachments` VALUES (4,2,1,0,'second.png','second.png',0);

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

CREATE TABLE `phpbb_profile_fields` (
  `field_id` mediumint(8) unsigned NOT NULL AUTO_INCREMENT,
  `field_name` varchar(255) NOT NULL DEFAULT '',
  `field_ident` varchar(20) NOT NULL DEFAULT '',
  `field_type` varchar(100) NOT NULL DEFAULT '',
  `field_hide` tinyint(1) unsigned NOT NULL DEFAULT 0,
  `field_no_view` tinyint(1) unsigned NOT NULL DEFAULT 0,
  `field_active` tinyint(1) unsigned NOT NULL DEFAULT 0,
  `field_order` mediumint(8) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`field_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Real shape confirmed on phpbbmodders.net's own dump: field_name is just
-- an internal identifier equal to field_ident, NOT the display label (see
-- get_profile_fields()) — 11 real active/visible fields (website, location,
-- icq, etc.) plus 2 real hidden+inactive+no-view registration-antispam
-- fields.
INSERT INTO `phpbb_profile_fields` VALUES (1,'phpbb_website','phpbb_website','profilefields.type.url',0,0,1,1);
INSERT INTO `phpbb_profile_fields` VALUES (2,'realname','realname','profilefields.type.string',0,0,1,2);
INSERT INTO `phpbb_profile_fields` VALUES (3,'antispam','antispam','profilefields.type.dropdown',1,1,0,3);

CREATE TABLE `phpbb_profile_lang` (
  `field_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `lang_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `lang_name` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`field_id`, `lang_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- field 1 mirrors a real phpBB built-in field: an ALL-CAPS internal
-- language key, not literal text (see humanize_profile_field_label()).
-- field 2 mirrors a real admin-added custom field: already human text,
-- passed through unchanged.
INSERT INTO `phpbb_profile_lang` VALUES (1,1,'WEBSITE');
INSERT INTO `phpbb_profile_lang` VALUES (2,1,'Real name');
INSERT INTO `phpbb_profile_lang` VALUES (3,1,'Antispam Question');

CREATE TABLE `phpbb_profile_fields_data` (
  `user_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `pf_phpbb_website` varchar(255) NOT NULL DEFAULT '',
  `pf_realname` varchar(255) NOT NULL DEFAULT '',
  `pf_antispam` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

INSERT INTO `phpbb_profile_fields_data` VALUES (2,'http://example.com','Test User','2');
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
    assert len(forums) == 4
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
    assert len(topics) == 2
    assert topics[0]["topic_title"] == "Test Topic"


def test_get_topics_excludes_hidden(db_path):
    # topic_visibility=3 (needs-reapproval, same as soft-deleted/unapproved
    # for a public viewer) must never be returned — see phpbb-archive
    # security review, finding 8.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    topics = db.get_topics(forum_id=1)
    assert all(t["topic_id"] != 2 for t in topics)


def test_get_topics_pins_announcements_above_stickies_above_normal(db_path):
    # forum_id=2's fixture topics have last_post_time in the OPPOSITE order
    # (Normal newest, Announce middle, Sticky oldest) so this only passes if
    # topic_type tier actually overrides last_post_time, not merely
    # coincides with it.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    topics = db.get_topics(forum_id=2)
    assert [t["topic_title"] for t in topics] == [
        "Announce Topic",
        "Sticky Topic",
        "Normal Topic",
    ]


def test_get_global_announcements(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    globals_ = db.get_global_announcements()
    assert [t["topic_title"] for t in globals_] == ["Global Announcement"]
    assert globals_[0]["forum_id"] == 3


def test_get_global_announcements_respects_exclusion(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    globals_ = db.get_global_announcements(exclude_forum_ids={3})
    assert globals_ == []


def test_get_password_protected_forum_ids(db_path):
    # forum_password='' (phpBB's own default/no-password value) must never
    # be mistaken for a real password, and the reverse — a real hash, no
    # matter its content — must always count as protected.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    assert db.get_password_protected_forum_ids() == {4}


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


def test_get_all_poll_texts(db_path):
    # poll_title/poll_option_text go through the same BBCode/XML parser
    # as post text and can equally contain [img] — never included in
    # image discovery before, silently dropping any poll image (see
    # phpbb-archive security review, finding 10).
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    texts = db.get_all_poll_texts()
    assert "Pick one" in texts
    assert "Option A" in texts
    assert "Option B" in texts


def test_get_all_poll_texts_respects_exclusion(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    texts = db.get_all_poll_texts(exclude_forum_ids={1})
    assert texts == []


def test_get_attachments_ordered_by_attach_id_desc(db_path):
    # phpBB's own viewtopic.php query orders attachments "attach_id DESC,
    # post_msg_id ASC" — a stored [attachment=N] tag's numeric index was
    # assigned against that order when the post was originally rendered,
    # so returning them in a different order (e.g. insertion/rowid order)
    # pairs a tag with the wrong attachment. Inserted here as attach_id
    # 3 then 4 (ascending) to catch a query with no ORDER BY, which
    # SQLite would otherwise return in that same insertion order — see
    # phpbb-archive security review, finding 9.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    attachments = db.get_attachments(post_id=2)
    assert [a["real_filename"] for a in attachments] == ["second.png", "first.png"]


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


def test_get_profile_fields_excludes_hidden_inactive_noview(db_path):
    # The Antispam Question field (field_hide=1, field_no_view=1,
    # field_active=0) must never be returned — a static archive has no
    # login, so every viewer is equivalent to an anonymous visitor.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    fields = db.get_profile_fields()
    assert [f["field_ident"] for f in fields] == ["phpbb_website", "realname"]


def test_get_profile_fields_uses_profile_lang_not_field_name(db_path):
    # field_name is just an internal identifier (equal to field_ident) in
    # real phpBB, not the display label — the real label is
    # phpbb_profile_lang.lang_name. See get_profile_fields()'s docstring.
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    fields = db.get_profile_fields()
    assert [f["display_label"] for f in fields] == ["WEBSITE", "Real name"]


def test_get_all_profile_field_values(db_path):
    # Raw value fetch is unfiltered by visibility — that filtering happens
    # by only looking up idents from get_profile_fields()'s already-
    # filtered list, not here (see render_users()).
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    values = db.get_all_profile_field_values()
    assert values == {
        2: {"phpbb_website": "http://example.com", "realname": "Test User", "antispam": "2"},
    }


def test_get_all_profile_field_values_omits_users_with_no_values(db_path):
    db = PhpbbDatabase(db_path, table_prefix="phpbb_")
    values = db.get_all_profile_field_values()
    assert 1 not in values
