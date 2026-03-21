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
        assert result.count("abc123.png") == 1


class TestNesting:
    def test_bold_inside_quote(self, parser):
        result = parser.convert('[quote="Bob"][b]important[/b][/quote]', uid="")
        assert "<strong>important</strong>" in result
        assert "Bob" in result

    def test_plain_text_passthrough(self, parser):
        result = parser.convert("just some text", uid="")
        assert result == "just some text"
