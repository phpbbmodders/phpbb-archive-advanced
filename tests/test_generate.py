import re
from pathlib import Path

from generator.bbcode import PhpbbBBCodeParser
from generator.generate import (
    _apache_redirects,
    _nginx_redirects,
    clean_disabled_feature_output,
    copy_assets,
    find_image_urls,
    process_forum_descs,
    render_redirects,
)


class _FakeDB:
    """Stands in for PhpbbDatabase for find_image_urls(), which only
    calls get_all_post_texts()/get_all_poll_texts()."""

    def __init__(self, post_texts=(), poll_texts=()):
        self._post_texts = list(post_texts)
        self._poll_texts = list(poll_texts)

    def get_all_post_texts(self, exclude_forum_ids=None):
        return list(self._post_texts)

    def get_all_poll_texts(self, exclude_forum_ids=None):
        return list(self._poll_texts)


class TestFindImageUrls:
    # find_image_urls() must discover every shape PhpbbBBCodeParser
    # actually renders — a shape the renderer resolves but discovery
    # never fetches gets silently dropped (empty external_images entry)
    # instead of shown. See phpbb-archive security review, finding 10;
    # each case below was confirmed against phpbbmodders.net's real dump
    # before fixing (counts noted in docs/CHANGES.md).

    def test_finds_bare_img_bbcode(self):
        db = _FakeDB(post_texts=["[img]http://example.com/pic.png[/img]"])
        assert find_image_urls(db, [], []) == {"http://example.com/pic.png"}

    def test_finds_img_with_uid_suffix(self):
        # [img:uid]...[/img:uid] — the plain "[img]" pattern requires an
        # exact "[/img]" close with no suffix, so a real UID-tagged post
        # produced an empty discovery set before this fix.
        db = _FakeDB(post_texts=["[img:abc123]http://example.com/pic.png[/img:abc123]"])
        assert find_image_urls(db, [], []) == {"http://example.com/pic.png"}

    def test_finds_xml_img_src(self):
        db = _FakeDB(post_texts=['<t><IMG src="http://example.com/pic.png">text</IMG></t>'])
        assert find_image_urls(db, [], []) == {"http://example.com/pic.png"}

    def test_finds_img_url_migration_shape(self):
        # A board that migrated from phpBB2 can carry a literal,
        # unconverted [img] wrapping an already-XML-converted <URL>
        # link — confirmed real and common (969 real posts) on
        # phpbbmodders.net's own dump; sample real text:
        # [img]<URL url="http://phpbbmodders.net/images/stuff/
        # announce_logo.gif">http://.../announce_logo.gif</URL>[/img]
        text = ('<t>before [img]<URL url="http://example.com/userbar.gif">'
                'http://example.com/userbar.gif</URL>[/img] after</t>')
        db = _FakeDB(post_texts=[text])
        assert find_image_urls(db, [], []) == {"http://example.com/userbar.gif"}

    def test_decodes_html_entities_in_migration_shape_url(self):
        # A URL stored as an XML attribute has its "&" escaped to
        # "&amp;" per XML rules — confirmed real (13 of the 969 real
        # migration-shape posts above) on phpbbmodders.net's own dump.
        # Passing the literal "&amp;" straight to an HTTP request breaks
        # any query string with more than one parameter.
        text = '<t>[img]<URL url="http://example.com/file.php?id=1&amp;x=2">t</URL>[/img]</t>'
        db = _FakeDB(post_texts=[text])
        assert find_image_urls(db, [], []) == {"http://example.com/file.php?id=1&x=2"}

    def test_finds_poll_images(self):
        # poll_title/poll_option_text render through the same parser as
        # post text and can equally contain [img] — never included in
        # discovery before this fix.
        db = _FakeDB(poll_texts=["<t>[img]http://example.com/poll.png[/img]</t>"])
        assert find_image_urls(db, [], []) == {"http://example.com/poll.png"}


class TestRedirectGeneration:
    # Real-world driver: phpbbmodders.net's own phpbb_config.script_path
    # is "/board" (a real, non-root install), and the archive is deployed
    # at the site root — so a rule without the old prefix would never
    # match the live board's actual request paths. Verified against a
    # real dump and real nginx/apache syntax checkers (nginx -t,
    # apachectl -t) — see phpbb-archive security review, finding 11 and
    # the --redirect-format feature notes in docs/CHANGES.md.

    def test_apache_no_prefix_relative_base(self):
        result = _apache_redirects("", "/")
        assert "RewriteRule ^viewtopic\\.php$ /topics/%1.html#p%2? [NE,R=301,L]" in result
        assert "RewriteRule ^viewtopic\\.php$ /topics/%2.html#p%1? [NE,R=301,L]" in result
        assert "RewriteRule ^viewforum\\.php$ /forums/%1.html? [R=301,L]" in result
        assert "RewriteRule ^memberlist\\.php$ /users/%1.html? [R=301,L]" in result

    def test_apache_with_prefix(self):
        result = _apache_redirects("board", "/")
        assert "RewriteRule ^board/viewtopic\\.php$ /topics/%1.html#p%2? [NE,R=301,L]" in result
        assert "RewriteRule ^board/viewtopic\\.php$ /topics/%2.html#p%1? [NE,R=301,L]" in result
        assert "RewriteRule ^board/viewforum\\.php$ /forums/%1.html? [R=301,L]" in result
        assert "RewriteRule ^board/memberlist\\.php$ /users/%1.html? [R=301,L]" in result

    def test_apache_absolute_base_url(self):
        # A redirect rule that must run on a different host than the
        # archive itself (e.g. the old board's own subdomain) needs an
        # absolute target — a root-relative "/topics/..." would resolve
        # against that other host instead.
        result = _apache_redirects("", "https://archive.example.com/")
        assert "RewriteRule ^viewtopic\\.php$ https://archive.example.com/topics/%1.html#p%2? [NE,R=301,L]" in result

    def test_apache_uses_NE_flag_for_fragment_carrying_rules(self):
        # Without [NE], Apache percent-encodes "#" to "%23" in an
        # external-redirect target (confirmed against Apache's own
        # mod_rewrite flags docs, and reproduced live: the same rule
        # without [NE] redirected to a literal "...html%23p99" instead
        # of "...html#p99"). Only the two t+p combined rules carry a
        # fragment; the t-only/f/u rules don't need it.
        result = _apache_redirects("", "/")
        assert result.count("[NE,R=301,L]") == 2
        assert "[R=301,L]" in result  # the non-fragment rules still exist, without NE

    def test_apache_t_and_p_backreferences_not_split_across_conditions(self):
        # %N backreferences only ever come from the single LAST matched
        # RewriteCond — confirmed against Apache's own mod_rewrite docs,
        # and reproduced live: two separate RewriteCond lines (one for
        # t=, one for p=) made %1 and %2 BOTH resolve to whichever
        # condition matched last, producing a nonsense redirect
        # (topics/<post_id>.html#p with an empty anchor) instead of
        # topics/<topic_id>.html#p<post_id>. t and p must be captured
        # by a single RewriteCond's own regex, not two separate ones.
        result = _apache_redirects("", "/")
        # Extract each RewriteCond immediately preceding a viewtopic
        # RewriteRule with a #p fragment in its target, and confirm each
        # one is a single condition capturing both t and p together.
        pairs = re.findall(
            r'RewriteCond %\{QUERY_STRING\} (\S+)\nRewriteRule \^viewtopic\\\.php\$ \S*#p\S*\?',
            result,
        )
        assert len(pairs) == 2
        for pattern in pairs:
            assert pattern.count("([0-9]+)") == 2  # both t and p captured by ONE regex

    def test_apache_t_and_p_regex_matches_real_query_shapes(self):
        # Direct regex-level check standing in for a live Apache request
        # (verified separately, live, against a real isolated apache2
        # instance) — Python's re engine handles this pattern (no PCRE-
        # specific syntax) identically to Apache's. Real query shapes
        # pulled straight from phpbbmodders.net's own dump, including
        # the far more common t-before-p order and one confirmed real
        # example of the rarer p-before-t order.
        result = _apache_redirects("", "/")
        t_then_p = re.search(r'\(\?:\^\|&\)t=\(\[0-9\]\+\)\(\?:&\[\^&\]\*\)\*&p=\(\[0-9\]\+\)', result).group(0)
        p_then_t = re.search(r'\(\?:\^\|&\)p=\(\[0-9\]\+\)\(\?:&\[\^&\]\*\)\*&t=\(\[0-9\]\+\)', result).group(0)

        m = re.search(t_then_p, "f=118&t=6367&p=26325")
        assert m.groups() == ("6367", "26325")
        m = re.search(t_then_p, "t=42&p=99")
        assert m.groups() == ("42", "99")
        m = re.search(p_then_t, "f=125&p=50434&t=10913")
        assert m.groups() == ("50434", "10913")

    def test_nginx_no_prefix_relative_base(self):
        result = _nginx_redirects("", "/")
        assert "location = /viewtopic.php {" in result
        assert "return 301 /topics/$arg_t.html#p$arg_p;" in result
        assert "location = /viewforum.php {" in result
        assert "location = /memberlist.php {" in result

    def test_nginx_with_prefix(self):
        result = _nginx_redirects("board", "/")
        assert "location = /board/viewtopic.php {" in result
        assert "location = /board/viewforum.php {" in result
        assert "location = /board/memberlist.php {" in result

    def test_nginx_absolute_base_url(self):
        result = _nginx_redirects("", "https://archive.example.com/")
        assert "return 301 https://archive.example.com/topics/$arg_t.html#p$arg_p;" in result

    def test_render_redirects_apache_writes_htaccess(self, tmp_path):
        render_redirects(tmp_path, "apache", "board", "/")
        content = (tmp_path / ".htaccess").read_text(encoding="utf-8")
        assert "RewriteRule ^board/viewtopic\\.php$" in content

    def test_render_redirects_nginx_writes_conf(self, tmp_path):
        render_redirects(tmp_path, "nginx", "board", "/")
        content = (tmp_path / "nginx-redirects.conf").read_text(encoding="utf-8")
        assert "location = /board/viewtopic.php {" in content


class TestCleanDisabledFeatureOutput:
    # An --incremental run's unconditional cleanup only ever clears
    # forums/topics/users/index.html — feature-gated output (search,
    # sitemap/robots, redirects) isn't in that list since a normal run
    # regenerates it fresh, but that means a later run that disables a
    # feature (or switches --redirect-format apache<->nginx) leaves the
    # earlier run's file sitting there indefinitely, showing stale or
    # removed content. Confirmed on a real regeneration: nginx-
    # redirects.conf from an earlier --redirect-format nginx run was
    # still present after a later --redirect-format apache run.

    def _touch(self, out, *names):
        for name in names:
            if name in ("pagefind",):
                (out / name).mkdir()
                (out / name / "pagefind-entry.json").write_text("{}", encoding="utf-8")
            else:
                (out / name).write_text("stale", encoding="utf-8")

    def test_removes_search_output_when_search_disabled(self, tmp_path):
        self._touch(tmp_path, "search.html", "pagefind")
        clean_disabled_feature_output(tmp_path, search=False, sitemap_url=None, redirect_format=None)
        assert not (tmp_path / "search.html").exists()
        assert not (tmp_path / "pagefind").exists()

    def test_keeps_search_output_when_search_enabled(self, tmp_path):
        self._touch(tmp_path, "search.html", "pagefind")
        clean_disabled_feature_output(tmp_path, search=True, sitemap_url=None, redirect_format=None)
        assert (tmp_path / "search.html").exists()
        assert (tmp_path / "pagefind").exists()

    def test_removes_sitemap_output_when_sitemap_url_dropped(self, tmp_path):
        self._touch(tmp_path, "sitemap.xml", "robots.txt")
        clean_disabled_feature_output(tmp_path, search=False, sitemap_url=None, redirect_format=None)
        assert not (tmp_path / "sitemap.xml").exists()
        assert not (tmp_path / "robots.txt").exists()

    def test_removes_stale_nginx_redirects_when_switched_to_apache(self, tmp_path):
        self._touch(tmp_path, "nginx-redirects.conf")
        clean_disabled_feature_output(tmp_path, search=False, sitemap_url=None, redirect_format="apache")
        assert not (tmp_path / "nginx-redirects.conf").exists()

    def test_removes_stale_htaccess_when_switched_to_nginx(self, tmp_path):
        self._touch(tmp_path, ".htaccess")
        clean_disabled_feature_output(tmp_path, search=False, sitemap_url=None, redirect_format="nginx")
        assert not (tmp_path / ".htaccess").exists()

    def test_removes_redirect_output_when_redirect_format_dropped(self, tmp_path):
        self._touch(tmp_path, ".htaccess", "nginx-redirects.conf")
        clean_disabled_feature_output(tmp_path, search=False, sitemap_url=None, redirect_format=None)
        assert not (tmp_path / ".htaccess").exists()
        assert not (tmp_path / "nginx-redirects.conf").exists()

    def test_missing_files_are_not_an_error(self, tmp_path):
        clean_disabled_feature_output(tmp_path, search=False, sitemap_url=None, redirect_format=None)


class TestCopyAssetsAttachmentJunkFiltering:
    # dump/files/ also holds phpBB's own auto-generated
    # thumb_<attach_id>_<physical_filename> companion files for image
    # attachments — never referenced by their own name in
    # phpbb_attachments, and this archive never links to a thumbnail (it
    # always serves the full image directly). Confirmed real on
    # phpbbmodders.net's own dump: 1,498 such files, a meaningful
    # fraction already corrupted/truncated on top of being unreferenced
    # dead weight sitting in the generated output.

    def _dump_dir(self, tmp_path):
        d = tmp_path / "dump"
        (d / "files").mkdir(parents=True)
        return d

    def test_real_attachment_is_copied(self, tmp_path):
        dump = self._dump_dir(tmp_path)
        (dump / "files" / "abc123").write_bytes(b"fake image data")
        out = tmp_path / "output"
        copy_assets(dump, out, physical_to_real={"abc123": "photo.png"})
        assert (out / "assets" / "attachments" / "abc123" / "photo.png").read_bytes() == b"fake image data"

    def test_phpbb_thumbnail_file_is_not_copied(self, tmp_path):
        dump = self._dump_dir(tmp_path)
        (dump / "files" / "abc123").write_bytes(b"fake image data")
        (dump / "files" / "thumb_1_abc123").write_bytes(b"fake thumbnail data")
        out = tmp_path / "output"
        copy_assets(dump, out, physical_to_real={"abc123": "photo.png"})
        attachments_dir = out / "assets" / "attachments"
        assert (attachments_dir / "abc123" / "photo.png").exists()
        assert not (attachments_dir / "thumb_1_abc123").exists()

    def test_stale_junk_from_a_prior_incremental_run_is_removed(self, tmp_path):
        # --incremental only ever adds attachment files across runs and
        # never revisits ones already on disk — a junk file copied by an
        # older version of this code (before this fix) would otherwise
        # sit there indefinitely.
        dump = self._dump_dir(tmp_path)
        (dump / "files" / "abc123").write_bytes(b"fake image data")
        out = tmp_path / "output"
        attachments_dir = out / "assets" / "attachments"
        attachments_dir.mkdir(parents=True)
        (attachments_dir / "thumb_1_abc123").write_bytes(b"stale junk from an earlier run")
        copy_assets(dump, out, physical_to_real={"abc123": "photo.png"})
        assert not (attachments_dir / "thumb_1_abc123").exists()
        assert (attachments_dir / "abc123" / "photo.png").exists()


class TestCopyAssetsRecoversFromNestedDuplicates:
    # The same physical_filename can have more than one on-disk copy
    # across the stray nested subdirectories a faulty backup script can
    # leave behind (dump/files/, dump/files/files/, ...) — os.walk visits
    # the shallowest one first, so blindly keeping "whichever copy is
    # found first" can mean using a corrupted shallow copy when a good
    # copy of the very same file exists one level deeper, collected at a
    # different time from a different source.

    @staticmethod
    def _real_png_bytes() -> bytes:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (2, 2), color="red").save(buf, format="PNG")
        return buf.getvalue()

    def test_uses_deeper_copy_when_shallow_copy_is_corrupted(self, tmp_path):
        dump = tmp_path / "dump"
        (dump / "files" / "files").mkdir(parents=True)
        (dump / "files" / "abc123").write_bytes(b"not a real png, truncated garbage")
        (dump / "files" / "files" / "abc123").write_bytes(self._real_png_bytes())
        out = tmp_path / "output"
        copy_assets(dump, out, physical_to_real={"abc123": "photo.png"})
        copied = (out / "assets" / "attachments" / "abc123" / "photo.png").read_bytes()
        assert copied == self._real_png_bytes()

    def test_keeps_shallow_copy_when_it_is_already_valid(self, tmp_path):
        dump = tmp_path / "dump"
        (dump / "files" / "files").mkdir(parents=True)
        (dump / "files" / "abc123").write_bytes(self._real_png_bytes())
        (dump / "files" / "files" / "abc123").write_bytes(b"a different, also-valid copy would never be reached")
        out = tmp_path / "output"
        copy_assets(dump, out, physical_to_real={"abc123": "photo.png"})
        copied = (out / "assets" / "attachments" / "abc123" / "photo.png").read_bytes()
        assert copied == self._real_png_bytes()

    def test_falls_back_to_shallow_copy_when_all_copies_are_corrupted(self, tmp_path):
        dump = tmp_path / "dump"
        (dump / "files" / "files").mkdir(parents=True)
        (dump / "files" / "abc123").write_bytes(b"garbage one")
        (dump / "files" / "files" / "abc123").write_bytes(b"garbage two")
        out = tmp_path / "output"
        copy_assets(dump, out, physical_to_real={"abc123": "photo.png"})
        # Still copies something (find_bad_attachments/--attachment-recovery
        # handle it from here) rather than silently copying nothing.
        assert (out / "assets" / "attachments" / "abc123" / "photo.png").read_bytes() == b"garbage one"


class TestProcessForumDescsRespectsPageDepth:
    # forum_desc is converted once per page depth actually used across the
    # site (index.html at the root, forums/N.html one level down) — a
    # forum description that embeds an internal link or asset must resolve
    # correctly no matter which depth's parser rendered it. Real dump
    # example (phpbbmodders.net forum 44, "phpBB Project"): a forum_desc
    # containing an internal viewtopic.php link — see phpbb-archive
    # security review, finding 12.

    def _forum(self, desc):
        return {"forum_id": 44, "forum_name": "phpBB Project", "forum_desc": desc,
                "forum_desc_uid": "", "forum_type": 1, "parent_id": 0}

    def test_internal_link_resolves_correctly_at_root_depth(self):
        parser = PhpbbBBCodeParser(
            smilies=[], attachments={}, assets_prefix="assets",
            internal_topic_ids={12345}, board_hosts={"phpbbmodders.net"},
        )
        desc = '<r>More info at <URL url="http://phpbbmodders.net/board/viewtopic.php?f=44&amp;t=12345">here</URL>.</r>'
        result = process_forum_descs([self._forum(desc)], parser)
        assert 'href="topics/12345.html"' in result[0]["forum_desc"]

    def test_internal_link_resolves_correctly_one_level_down(self):
        # This is the page depth forums/N.html actually renders at — using
        # the root-depth parser here (the original bug: render_forums()
        # was passed the same parser instance used for index.html) instead
        # produces href="topics/12345.html", which 404s from forums/44.html
        # (resolves to forums/topics/12345.html instead of ../topics/...).
        parser = PhpbbBBCodeParser(
            smilies=[], attachments={}, assets_prefix="../assets",
            internal_topic_ids={12345}, board_hosts={"phpbbmodders.net"},
        )
        desc = '<r>More info at <URL url="http://phpbbmodders.net/board/viewtopic.php?f=44&amp;t=12345">here</URL>.</r>'
        result = process_forum_descs([self._forum(desc)], parser)
        assert 'href="../topics/12345.html"' in result[0]["forum_desc"]
