import re
from pathlib import Path

from generator.bbcode import PhpbbBBCodeParser
from generator.generate import (
    _apache_redirects,
    _nginx_redirects,
    build_edit_notice,
    clean_disabled_feature_output,
    compute_post_pages,
    copy_assets,
    copy_avatars,
    download_external_images,
    download_remote_avatars,
    find_image_urls,
    humanize_profile_field_label,
    load_exclusions,
    load_password_override,
    paginate_posts,
    paginate_topics,
    process_forum_descs,
    read_table_prefix,
    render_redirects,
    strip_trailing_attachments,
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
        assert "return 301 /topics/$1.html#p$2;" in result
        assert "location = /viewforum.php {" in result
        assert "location = /memberlist.php {" in result

    def test_nginx_with_prefix(self):
        result = _nginx_redirects("board", "/")
        assert "location = /board/viewtopic.php {" in result
        assert "location = /board/viewforum.php {" in result
        assert "location = /board/memberlist.php {" in result

    def test_nginx_absolute_base_url(self):
        result = _nginx_redirects("", "https://archive.example.com/")
        assert "return 301 https://archive.example.com/topics/$1.html#p$2;" in result

    def test_nginx_generic_topic_post_requires_both_numeric(self):
        # A malformed request with p but no t must not fall through to
        # the generic rule and build a broken destination like
        # "/topics/.html#p123" — confirmed live against a real nginx
        # instance: the previous "if ($arg_p) { .../topics/$arg_t.html#
        # p$arg_p; }" checked $arg_p's presence but used $arg_t in the
        # destination without checking it at all.
        result = _nginx_redirects("", "/")
        assert 'set $topic_post_key "$arg_t:$arg_p";' in result
        assert 'if ($topic_post_key ~ "^([0-9]+):([0-9]+)$")' in result
        assert 'if ($arg_t ~ "^[0-9]+$")' in result
        assert "$arg_t.html#p$arg_p" not in result

    def test_render_redirects_apache_writes_htaccess(self, tmp_path):
        render_redirects(tmp_path, "apache", "board", "/")
        content = (tmp_path / ".htaccess").read_text(encoding="utf-8")
        assert "RewriteRule ^board/viewtopic\\.php$" in content

    def test_render_redirects_nginx_writes_conf(self, tmp_path):
        render_redirects(tmp_path, "nginx", "board", "/")
        content = (tmp_path / "nginx-redirects.conf").read_text(encoding="utf-8")
        assert "location = /board/viewtopic.php {" in content


class TestPaginatedRedirectBlocks:
    # A deep link to a specific post (?t=X&p=Y) in a paginated topic (see
    # TOPIC_PAGE_SIZE in generate.py) must land on that post's own page,
    # not always page 1 — real topic 8413 on phpbbmodders.net's own dump
    # has exactly this shape: page 1 holds post_ids 31649-31673, page 2
    # holds 31674-31698, etc., each page an exact contiguous, non-
    # overlapping range (confirmed real).

    MULTI_PAGE_TOPICS = {8413: [
        list(range(31649, 31674)),  # page 1 (no rule needed, it's the default)
        list(range(31674, 31699)),  # page 2
    ]}

    def test_apache_no_pagination_data_unchanged(self):
        # multi_page_topics=None (the default) must produce byte-identical
        # output to before this feature existed.
        assert _apache_redirects("", "/") == _apache_redirects("", "/", None)

    def test_apache_emits_page_specific_rule(self):
        result = _apache_redirects("", "/", self.MULTI_PAGE_TOPICS)
        assert "topics/8413-p2.html#p%1?" in result
        # Real post ids from page 2 appear in the alternation; page 1's
        # own ids must not (they don't need a special rule).
        assert "31674" in result
        assert "31649" not in result

    def test_apache_page_rule_comes_before_generic_rule(self):
        # The specific rule's [L] flag only helps if Apache reaches it
        # first — it must be placed before the generic t=/p= rule.
        result = _apache_redirects("", "/", self.MULTI_PAGE_TOPICS)
        specific_pos = result.index("topics/8413-p2.html")
        generic_pos = result.index("topics/%1.html#p%2?")
        assert specific_pos < generic_pos

    def test_apache_handles_both_t_p_orders(self):
        # Same t-before-p / p-before-t duplication the generic rule needs,
        # for the same %N-backreference reason.
        result = _apache_redirects("", "/", self.MULTI_PAGE_TOPICS)
        assert result.count("topics/8413-p2.html#p%1?") == 2

    def test_nginx_no_pagination_data_unchanged(self):
        assert _nginx_redirects("", "/") == _nginx_redirects("", "/", None)

    def test_nginx_emits_page_specific_rule(self):
        result = _nginx_redirects("", "/", self.MULTI_PAGE_TOPICS)
        assert "topics/8413-p2.html#p$arg_p" in result
        assert "31674" in result
        assert "31649" not in result

    def test_nginx_uses_separator_to_avoid_digit_concatenation_ambiguity(self):
        # $arg_t and $arg_p concatenated with no separator could conflate
        # two different (topic, post) pairs purely by digit boundary (e.g.
        # topic 84/post 1331674 vs. topic 8413/post 31674) — must use an
        # explicit non-numeric separator.
        result = _nginx_redirects("", "/", self.MULTI_PAGE_TOPICS)
        assert '$topic_page_key ~ "^8413:' in result

    def test_nginx_sets_combined_key_before_testing_it(self):
        # nginx's `if` only accepts a bare variable as its left-hand
        # operand — an inline "$arg_t:$arg_p" there silently never
        # matches (confirmed live against a real nginx instance). The
        # combined value must be assigned with `set` first.
        result = _nginx_redirects("", "/", self.MULTI_PAGE_TOPICS)
        assert 'set $topic_page_key "$arg_t:$arg_p";' in result
        assert result.index('set $topic_page_key') < result.index('if ($topic_page_key')


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


class TestCopyAvatarsIsolatedFromOtherAssets:
    # A diagnostic mode (-m/--missing-avatars) needs existing avatar files
    # on disk to correctly check resolution against, but nothing else —
    # the old code called the full copy_assets() unconditionally and
    # without --exclude, silently overwriting a real deployment's custom
    # style.css with the default palette and re-copying already-excluded
    # attachments back into a real, possibly-published --output. See
    # phpbb-archive security review, finding 14.

    def test_copies_uploaded_avatars(self, tmp_path):
        dump = tmp_path / "dump"
        (dump / "images" / "avatars" / "upload").mkdir(parents=True)
        (dump / "images" / "avatars" / "upload" / "abc123_5.png").write_bytes(b"fake avatar")
        out = tmp_path / "output"
        copy_avatars(dump, out)
        assert (out / "assets" / "avatars" / "5.png").read_bytes() == b"fake avatar"

    def test_does_not_touch_style_css_or_other_assets(self, tmp_path):
        dump = tmp_path / "dump"
        (dump / "images" / "avatars" / "upload").mkdir(parents=True)
        out = tmp_path / "output"
        copy_avatars(dump, out)
        assert not (out / "assets" / "style.css").exists()
        assert not (out / "assets" / "attachments").exists()

    def test_full_copy_assets_still_includes_avatars(self, tmp_path):
        # copy_assets() (the real generate() path) must still get
        # everything copy_avatars() does — this just moved, not removed.
        dump = tmp_path / "dump"
        (dump / "images" / "avatars" / "upload").mkdir(parents=True)
        (dump / "images" / "avatars" / "upload" / "abc123_5.png").write_bytes(b"fake avatar")
        out = tmp_path / "output"
        copy_assets(dump, out)
        assert (out / "assets" / "avatars" / "5.png").read_bytes() == b"fake avatar"


class TestBuildEditNotice:
    # Mirrors phpBB 3.3.x core's own viewtopic.php gate exactly: shown when
    # (post_edit_count AND display_last_edited) OR post_edit_reason is set.
    # Confirmed real on phpbbmodders.net's own dump: 503 edited posts,
    # display_last_edited=1 (enabled).

    def test_no_notice_when_never_edited(self):
        post = {"post_edit_count": 0, "post_edit_reason": ""}
        assert build_edit_notice(post, poster_id=2, author_username="alice",
                                  users={}, display_last_edited=True) is None

    def test_no_notice_when_edited_but_display_last_edited_off_and_no_reason(self):
        post = {"post_edit_count": 1, "post_edit_reason": "", "post_edit_user": 0,
                "post_edit_time": 1000}
        assert build_edit_notice(post, poster_id=2, author_username="alice",
                                  users={}, display_last_edited=False) is None

    def test_notice_shown_when_display_last_edited_on(self):
        post = {"post_edit_count": 2, "post_edit_reason": "", "post_edit_user": 0,
                "post_edit_time": 1000}
        notice = build_edit_notice(post, poster_id=2, author_username="alice",
                                    users={}, display_last_edited=True)
        assert notice == {"editor_name": "alice", "edit_time": 1000,
                           "edit_count": 2, "reason": None}

    def test_reason_forces_notice_even_when_display_last_edited_off(self):
        post = {"post_edit_count": 1, "post_edit_reason": "fixed typo",
                "post_edit_user": 0, "post_edit_time": 1000}
        notice = build_edit_notice(post, poster_id=2, author_username="alice",
                                    users={}, display_last_edited=False)
        assert notice is not None
        assert notice["reason"] == "fixed typo"

    def test_editor_is_post_author_by_default(self):
        post = {"post_edit_count": 1, "post_edit_reason": "", "post_edit_user": 0,
                "post_edit_time": 1000}
        notice = build_edit_notice(post, poster_id=2, author_username="alice",
                                    users={}, display_last_edited=True)
        assert notice["editor_name"] == "alice"

    def test_editor_is_different_user_when_post_edit_user_differs(self):
        # A moderator editing someone else's post — real phpBB shows the
        # moderator's name, not the post author's.
        post = {"post_edit_count": 1, "post_edit_reason": "", "post_edit_user": 9,
                "post_edit_time": 1000}
        users = {9: {"user_id": 9, "username": "modbob"}}
        notice = build_edit_notice(post, poster_id=2, author_username="alice",
                                    users=users, display_last_edited=True)
        assert notice["editor_name"] == "modbob"

    def test_falls_back_to_post_username_for_guest_author(self):
        post = {"post_edit_count": 1, "post_edit_reason": "", "post_edit_user": 0,
                "post_edit_time": 1000, "post_username": "GuestPoster"}
        notice = build_edit_notice(post, poster_id=1, author_username=None,
                                    users={}, display_last_edited=True)
        assert notice["editor_name"] == "GuestPoster"

    def test_falls_back_to_unknown_when_no_name_available(self):
        post = {"post_edit_count": 1, "post_edit_reason": "", "post_edit_user": 0,
                "post_edit_time": 1000}
        notice = build_edit_notice(post, poster_id=1, author_username=None,
                                    users={}, display_last_edited=True)
        assert notice["editor_name"] == "Unknown"

    def test_unknown_editor_user_falls_back_to_unknown(self):
        post = {"post_edit_count": 1, "post_edit_reason": "", "post_edit_user": 99,
                "post_edit_time": 1000}
        notice = build_edit_notice(post, poster_id=2, author_username="alice",
                                    users={}, display_last_edited=True)
        assert notice["editor_name"] == "Unknown"


class TestRegenLightSkipsFetch:
    # --regen-light's skip_fetch=True must never touch the network for a
    # URL that isn't already cached, only rely on what's already on disk —
    # verified here by using unreachable/fake URLs a real fetch would hang
    # or fail on, and confirming no attempt is made either way.

    def test_download_external_images_skip_fetch_skips_uncached_urls(self, tmp_path):
        out = tmp_path / "output"
        result = download_external_images({"http://example.invalid/pic.png"}, out, skip_fetch=True)
        assert result == {}

    def test_download_external_images_skip_fetch_still_uses_cache(self, tmp_path):
        import hashlib
        out = tmp_path / "output"
        dest_dir = out / "assets" / "external"
        dest_dir.mkdir(parents=True)
        url = "http://example.invalid/pic.png"
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        (dest_dir / f"{digest}.png").write_bytes(b"fake")
        result = download_external_images({url}, out, skip_fetch=True)
        assert result == {url: f"{digest}.png"}

    def test_download_remote_avatars_skip_fetch_skips_uncached(self, tmp_path):
        out = tmp_path / "output"
        users = [{"user_id": 5, "user_avatar_type": "avatar.driver.remote",
                  "user_avatar": "http://example.invalid/a.png"}]
        result = download_remote_avatars(users, out, skip_fetch=True)
        assert result == {}

    def test_download_remote_avatars_skip_fetch_still_uses_cache(self, tmp_path):
        out = tmp_path / "output"
        dest_dir = out / "assets" / "avatars"
        dest_dir.mkdir(parents=True)
        (dest_dir / "5.png").write_bytes(b"fake avatar")
        users = [{"user_id": 5, "user_avatar_type": "avatar.driver.remote",
                  "user_avatar": "http://example.invalid/a.png"}]
        result = download_remote_avatars(users, out, skip_fetch=True)
        assert result == {5: "png"}


class TestHumanizeProfileFieldLabel:
    # phpBB's own built-in default fields store an ALL-CAPS internal
    # language key as their profile_lang label; a custom admin-added
    # field's label is already normal text. Confirmed real on
    # phpbbmodders.net: WEBSITE/ICQ/LOCATION/etc. (built-in) vs.
    # "Real name"/"Pick Your choice" (custom, admin-authored).

    def test_titlecases_all_caps_builtin_label(self):
        assert humanize_profile_field_label("WEBSITE") == "Website"

    def test_known_acronym_exception_stays_unchanged(self):
        # Confirmed real: phpBB's own language/en/common.php translates
        # this one as the literal acronym "ICQ", not the title-cased "Icq"
        # a blind .title() would produce — caught via a real browser check
        # on phpbbmodders.net's own dump (user 986).
        assert humanize_profile_field_label("ICQ") == "ICQ"

    def test_leaves_already_human_label_unchanged(self):
        assert humanize_profile_field_label("Real name") == "Real name"

    def test_leaves_mixed_case_label_unchanged(self):
        assert humanize_profile_field_label("Pick Your choice") == "Pick Your choice"


class TestPaginatePosts:
    def test_splits_into_page_size_chunks(self):
        posts = [{"post_id": i} for i in range(1, 8)]
        pages = paginate_posts(posts, page_size=3)
        assert [len(p) for p in pages] == [3, 3, 1]

    def test_single_page_when_under_threshold(self):
        posts = [{"post_id": i} for i in range(1, 4)]
        assert paginate_posts(posts, page_size=25) == [posts]

    def test_empty_topic_still_gets_one_page(self):
        # An empty page list, not zero pages — a topic with no recoverable
        # posts still needs exactly one rendered page.
        assert paginate_posts([], page_size=25) == [[]]

    def test_exact_multiple_of_page_size(self):
        posts = [{"post_id": i} for i in range(1, 51)]
        pages = paginate_posts(posts, page_size=25)
        assert [len(p) for p in pages] == [25, 25]


class TestComputePostPages:
    def test_single_topic_under_threshold_all_page_one(self):
        topic_posts = {1: [{"post_id": 10}, {"post_id": 11}, {"post_id": 12}]}
        result = compute_post_pages(topic_posts, page_size=25)
        assert result == {10: 1, 11: 1, 12: 1}

    def test_single_topic_spans_multiple_pages(self):
        posts = [{"post_id": i} for i in range(1, 8)]
        topic_posts = {1: posts}
        result = compute_post_pages(topic_posts, page_size=3)
        assert result == {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 3}

    def test_page_numbers_are_per_topic_not_global(self):
        # Two topics, each individually under the threshold — the second
        # topic's posts must still be page 1 of *their own* topic, not
        # some running offset across topics.
        topic_posts = {
            1: [{"post_id": 100}, {"post_id": 101}],
            2: [{"post_id": 200}, {"post_id": 201}],
        }
        result = compute_post_pages(topic_posts, page_size=25)
        assert result == {100: 1, 101: 1, 200: 1, 201: 1}


class TestReadTablePrefix:
    def test_single_quoted_value(self, tmp_path):
        config = tmp_path / "config.php"
        config.write_text("<?php\n$table_prefix = 'phpbb_';\n", encoding="utf-8")
        assert read_table_prefix(config) == "phpbb_"

    def test_double_quoted_value(self, tmp_path):
        config = tmp_path / "config.php"
        config.write_text('<?php\n$table_prefix = "phpbb_";\n', encoding="utf-8")
        assert read_table_prefix(config) == "phpbb_"

    def test_custom_prefix(self, tmp_path):
        config = tmp_path / "config.php"
        config.write_text("<?php\n$table_prefix = 'forum_';\n", encoding="utf-8")
        assert read_table_prefix(config) == "forum_"

    def test_missing_file_falls_back_to_default(self, tmp_path):
        assert read_table_prefix(tmp_path / "does_not_exist.php") == "phpbb_"

    def test_no_matching_line_falls_back_to_default(self, tmp_path):
        config = tmp_path / "config.php"
        config.write_text("<?php\n// nothing relevant here\n", encoding="utf-8")
        assert read_table_prefix(config) == "phpbb_"


class TestLoadExclusions:
    def test_loads_categories_and_forums(self, tmp_path):
        path = tmp_path / "exclude.json"
        path.write_text('{"categories": [1, 2], "forums": [10]}', encoding="utf-8")
        assert load_exclusions(path) == {1, 2, 10}

    def test_string_ids_are_coerced_to_int(self, tmp_path):
        path = tmp_path / "exclude.json"
        path.write_text('{"categories": ["5"], "forums": []}', encoding="utf-8")
        assert load_exclusions(path) == {5}

    def test_invalid_id_raises_error_naming_file_and_key(self, tmp_path):
        path = tmp_path / "exclude.json"
        path.write_text('{"categories": [], "forums": [52, "o52"]}', encoding="utf-8")
        try:
            load_exclusions(path)
            assert False, "expected ValueError"
        except ValueError as e:
            assert str(path) in str(e)
            assert "forums" in str(e)
            assert "o52" in str(e)


class TestLoadPasswordOverride:
    def test_loads_forums(self, tmp_path):
        path = tmp_path / "password_override.json"
        path.write_text('{"forums": [10, 20]}', encoding="utf-8")
        assert load_password_override(path) == {10, 20}

    def test_string_ids_are_coerced_to_int(self, tmp_path):
        path = tmp_path / "password_override.json"
        path.write_text('{"forums": ["5"]}', encoding="utf-8")
        assert load_password_override(path) == {5}

    def test_invalid_id_raises_error_naming_file_and_key(self, tmp_path):
        path = tmp_path / "password_override.json"
        path.write_text('{"forums": [52, "o52"]}', encoding="utf-8")
        try:
            load_password_override(path)
            assert False, "expected ValueError"
        except ValueError as e:
            assert str(path) in str(e)
            assert "forums" in str(e)
            assert "o52" in str(e)


class TestStripTrailingAttachments:
    def test_removes_trailing_image_attachment_block(self):
        html = (
            "<p>hello</p>"
            '\n<div class="post-attachments">'
            '<div class="inline-attachment">'
            '<a href="x"><img src="x" alt="a.png" loading="lazy" /></a>'
            "<br/><em>Attachment: a.png</em></div></div>"
        )
        assert strip_trailing_attachments(html) == "<p>hello</p>"

    def test_removes_trailing_non_image_attachment_block(self):
        html = (
            "<p>see attached</p>"
            '\n<div class="post-attachments">'
            '<div class="inline-attachment">badge<a href="x">Attachment: a.zip</a></div></div>'
        )
        assert strip_trailing_attachments(html) == "<p>see attached</p>"

    def test_leaves_html_without_attachments_unchanged(self):
        html = "<p>just text, no attachments</p>"
        assert strip_trailing_attachments(html) == html


class TestPaginateTopics:
    def test_splits_into_page_size_chunks(self):
        topics = [{"topic_id": i} for i in range(1, 8)]
        pages = paginate_topics(topics, page_size=3)
        assert [len(p) for p in pages] == [3, 3, 1]

    def test_single_page_when_under_threshold(self):
        topics = [{"topic_id": i} for i in range(1, 4)]
        assert paginate_topics(topics, page_size=50) == [topics]

    def test_empty_forum_still_gets_one_page(self):
        assert paginate_topics([], page_size=50) == [[]]

    def test_exact_multiple_of_page_size(self):
        topics = [{"topic_id": i} for i in range(1, 101)]
        pages = paginate_topics(topics, page_size=50)
        assert [len(p) for p in pages] == [50, 50]


class TestForumPaginatedRedirectBlocks:
    # A deep link with a start= offset (?f=X&start=Y) into a paginated
    # forum's topic listing (see FORUM_PAGE_SIZE in generate.py) must land
    # on that page, not always page 1. Unlike a topic's real, irregular
    # post ids, a forum page's start offset is an exact, deterministic
    # multiple of FORUM_PAGE_SIZE (50): page 2 starts at 50, page 3 at 100.

    MULTI_PAGE_FORUMS = {125: 3}  # forum 125, 3 total pages

    def test_apache_no_pagination_data_unchanged(self):
        assert _apache_redirects("", "/") == _apache_redirects("", "/", None, None)

    def test_apache_emits_page_specific_rules_for_each_page(self):
        result = _apache_redirects("", "/", None, self.MULTI_PAGE_FORUMS)
        assert "forums/125-p2.html?" in result
        assert "forums/125-p3.html?" in result
        assert "start=50" in result
        assert "start=100" in result

    def test_apache_page_rule_comes_before_generic_rule(self):
        result = _apache_redirects("", "/", None, self.MULTI_PAGE_FORUMS)
        specific_pos = result.index("forums/125-p2.html")
        generic_pos = result.index("forums/%1.html?")
        assert specific_pos < generic_pos

    def test_apache_handles_both_f_start_orders(self):
        result = _apache_redirects("", "/", None, self.MULTI_PAGE_FORUMS)
        assert result.count("forums/125-p2.html?") == 2

    def test_nginx_no_pagination_data_unchanged(self):
        assert _nginx_redirects("", "/") == _nginx_redirects("", "/", None, None)

    def test_nginx_emits_page_specific_rules_for_each_page(self):
        result = _nginx_redirects("", "/", None, self.MULTI_PAGE_FORUMS)
        assert '$forum_page_key = "125:50"' in result
        assert '$forum_page_key = "125:100"' in result
        assert "forums/125-p2.html" in result
        assert "forums/125-p3.html" in result

    def test_nginx_sets_combined_key_before_testing_it(self):
        # nginx's `if` only accepts a bare variable as its left-hand
        # operand — an inline "$arg_f:$arg_start" there silently never
        # matches (confirmed live against a real nginx instance). The
        # combined value must be assigned with `set` first.
        result = _nginx_redirects("", "/", None, self.MULTI_PAGE_FORUMS)
        assert 'set $forum_page_key "$arg_f:$arg_start";' in result
        assert result.index('set $forum_page_key') < result.index('if ($forum_page_key')

    def test_nginx_page_rule_comes_before_generic_rule(self):
        result = _nginx_redirects("", "/", None, self.MULTI_PAGE_FORUMS)
        specific_pos = result.index("forums/125-p2.html")
        generic_pos = result.index("forums/$arg_f.html")
        assert specific_pos < generic_pos
