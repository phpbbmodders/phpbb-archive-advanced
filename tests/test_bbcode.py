import pytest
from generator.bbcode import PhpbbBBCodeParser


@pytest.fixture
def parser():
    smilies = [
        {"code": ":)", "smiley_url": "icon_e_smile.gif", "emotion": "Smile"},
        {"code": ":D", "smiley_url": "icon_e_biggrin.gif", "emotion": "Very Happy"},
    ]
    attachments = {
        1: [{"physical_filename": "abc123.png", "real_filename": "photo.png"}],
    }
    return PhpbbBBCodeParser(
        smilies=smilies,
        attachments=attachments,
        assets_prefix="../assets",
    )


class TestUIDStripping:
    def test_strips_uid_from_bold(self, parser):
        result = parser.convert("[b:abc123]hello[/b:abc123]", uid="abc123")
        assert result == "<strong>hello</strong>"

    def test_strips_uid_from_italic(self, parser):
        result = parser.convert("[i:xyz789]text[/i:xyz789]", uid="xyz789")
        assert result == "<em>text</em>"

    def test_no_uid_passthrough(self, parser):
        result = parser.convert("[b]hello[/b]", uid="")
        assert result == "<strong>hello</strong>"


class TestBasicTags:
    def test_underline(self, parser):
        result = parser.convert("[u]text[/u]", uid="")
        assert result == "<span style=\"text-decoration: underline\">text</span>"

    def test_url_with_label(self, parser):
        result = parser.convert('[url=https://example.com]click[/url]', uid="")
        assert 'href="https://example.com"' in result
        assert "click" in result

    def test_url_bare(self, parser):
        result = parser.convert("[url]https://example.com[/url]", uid="")
        assert 'href="https://example.com"' in result

    def test_url_rejects_dangerous_scheme(self, parser):
        # phpBB never validated a stored [url=...]'s scheme at write time,
        # so a real dump can contain old spam/exploit content using it —
        # see phpbb-archive security review, finding 6.
        result = parser.convert("[url=javascript:alert(1)]click[/url]", uid="")
        assert "javascript:" not in result
        assert 'href="#"' in result
        assert "click" in result

    def test_image(self, parser):
        result = parser.convert("[img]https://example.com/pic.png[/img]", uid="")
        assert '<img' in result
        assert 'src="https://example.com/pic.png"' in result

    def test_quote_with_author(self, parser):
        result = parser.convert('[quote="Alice"]hello[/quote]', uid="")
        assert "Alice" in result
        assert "hello" in result

    def test_quote_without_author(self, parser):
        result = parser.convert("[quote]hello[/quote]", uid="")
        assert "hello" in result

    def test_code(self, parser):
        result = parser.convert("[code]x = 1[/code]", uid="")
        assert "<code" in result or "<pre" in result
        assert "x = 1" in result

    def test_color(self, parser):
        result = parser.convert("[color=#FF0000]red[/color]", uid="")
        assert "color" in result
        assert "red" in result

    def test_size(self, parser):
        result = parser.convert("[size=150]big[/size]", uid="")
        assert "big" in result

    def test_list(self, parser):
        result = parser.convert("[list][*]one[*]two[/list]", uid="")
        assert "<li>" in result


class TestSmilies:
    def test_smiley_replacement(self, parser):
        result = parser.convert('<!-- s:) --><img src="{SMILIES_PATH}/icon_e_smile.gif" /><!-- s:) -->', uid="")
        assert 'src="../assets/images/smilies/icon_e_smile.gif"' in result

    def test_unknown_smiley_stripped(self, parser):
        result = parser.convert('<!-- s:( --><img src="{SMILIES_PATH}/icon_sad.gif" /><!-- s:( -->', uid="")
        # Should still produce an img tag with best-effort path
        assert "<img" in result


class TestHorizontalRule:
    def test_hr_bbcode(self, parser):
        # phpBB stores hr as [hr:uid][/hr:uid] — after uid strip: [hr][/hr]
        result = parser.convert("before[hr][/hr]after", uid="")
        assert "<hr>" in result
        assert "[hr]" not in result
        assert "[/hr]" not in result

    def test_hr_xml(self, parser):
        result = parser.convert("<r>before<HR/>after</r>", uid="")
        assert "<hr>" in result

    def test_hr_xml_orphan_text_node(self, parser):
        # phpBB may emit <HR/>[hr] where [hr] is a bare text node
        result = parser.convert("<r>before<HR/>[hr]after</r>", uid="")
        assert "<hr>" in result
        assert "[hr]" not in result

    def test_hr_xml_selfclosing_space(self, parser):
        result = parser.convert("<r>text<HR />more</r>", uid="")
        assert "<hr>" in result


class TestAttachments:
    def test_attachment_inline(self, parser):
        result = parser.convert("[attachment=0]photo.png[/attachment]", uid="", post_id=1)
        assert "../assets/attachments/" in result

    def test_attachment_trailing(self, parser):
        # Post has an attachment but no [attachment=N] tag in body — should be appended
        result = parser.convert("just some text", uid="", post_id=1)
        assert "../assets/attachments/abc123.png" in result
        assert "post-attachments" in result

    def test_attachment_not_duplicated_when_embedded(self, parser):
        # Attachment referenced inline should NOT appear in trailing section too
        result = parser.convert("[attachment=0]photo.png[/attachment]", uid="", post_id=1)
        assert result.count('class="inline-attachment"') == 1
        assert "post-attachments" not in result


class TestNesting:
    def test_bold_inside_quote(self, parser):
        result = parser.convert('[quote="Bob"][b]important[/b][/quote]', uid="")
        assert "<strong>important</strong>" in result
        assert "Bob" in result

    def test_plain_text_passthrough(self, parser):
        result = parser.convert("just some text", uid="")
        assert result == "just some text"


class TestInternalLinkRewriting:
    # A topic id alone is not enough to call a link "this board's own" —
    # it's just a small integer, near-guaranteed to collide with some
    # unrelated phpBB install's own topic ids. Confirmed on a real dump:
    # thousands of links to other real boards (phpbb.com, rmcgirr83.org,
    # etc.) whose own t=N happens to match a topic id that also exists
    # here — see phpbb-archive security review, finding 11.

    @pytest.fixture
    def parser(self):
        return PhpbbBBCodeParser(
            smilies=[], attachments={}, assets_prefix="../assets",
            internal_topic_ids={42}, board_hosts={"ourboard.example"},
        )

    def test_rewrites_link_on_own_host(self, parser):
        result = parser.convert(
            "[url=https://ourboard.example/viewtopic.php?t=42]click[/url]", uid="")
        assert 'href="../topics/42.html"' in result

    def test_rewrites_relative_link_with_no_host(self, parser):
        result = parser.convert(
            "[url=viewtopic.php?t=42]click[/url]", uid="")
        assert 'href="../topics/42.html"' in result

    def test_does_not_rewrite_same_topic_id_on_different_host(self, parser):
        # A different board's own topic 42 is not our topic 42.
        result = parser.convert(
            "[url=https://unrelated-board.example/viewtopic.php?t=42]click[/url]", uid="")
        assert 'href="../topics/42.html"' not in result
        assert "unrelated-board.example" in result

    def test_does_not_rewrite_own_host_unknown_topic_id(self, parser):
        # Our own host, but a topic id that isn't in this archive
        # (excluded, or simply never existed) — left as a normal link.
        result = parser.convert(
            "[url=https://ourboard.example/viewtopic.php?t=999]click[/url]", uid="")
        assert 'href="../topics/999.html"' not in result
