"""phpBB BBCode → HTML converter.

Handles phpBB's UID-annotated BBCode format, smiley HTML comments,
and [attachment] tags.
"""

import re
import html
import logging
import urllib.parse

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

# Bootstrap Icons (https://icons.getbootstrap.com/, MIT-licensed) — raw path
# data pulled verbatim from the project's own repo (twbs/icons), 16x16
# viewBox, fill="currentColor" so CSS controls color (matches --theme
# dark/light and any --style-css override, same as every other icon-less
# part of the archive's own CSS).
_ICON_FILE_ZIP = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="attachment-icon" viewBox="0 0 16 16"><path d="M5 7.5a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v.938l.4 1.599a1 1 0 0 1-.416 1.074l-.93.62a1 1 0 0 1-1.11 0l-.929-.62a1 1 0 0 1-.415-1.074L5 8.438zm2 0H6v.938a1 1 0 0 1-.03.243l-.4 1.598.93.62.929-.62-.4-1.598A1 1 0 0 1 7 8.438z"/><path d="M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5zm-3 0A1.5 1.5 0 0 1 9.5 3V1h-2v1h-1v1h1v1h-1v1h1v1H6V5H5V4h1V3H5V2h1V1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V4.5z"/></svg>'
_ICON_FILE_TEXT = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="attachment-icon" viewBox="0 0 16 16"><path d="M5.5 7a.5.5 0 0 0 0 1h5a.5.5 0 0 0 0-1zM5 9.5a.5.5 0 0 1 .5-.5h5a.5.5 0 0 1 0 1h-5a.5.5 0 0 1-.5-.5m0 2a.5.5 0 0 1 .5-.5h2a.5.5 0 0 1 0 1h-2a.5.5 0 0 1-.5-.5"/><path d="M9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V4.5zm0 1v2A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1z"/></svg>'
_ICON_FILE_PDF = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="attachment-icon" viewBox="0 0 16 16"><path d="M14 14V4.5L9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2M9.5 3A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h5.5z"/><path d="M4.603 14.087a.8.8 0 0 1-.438-.42c-.195-.388-.13-.776.08-1.102.198-.307.526-.568.897-.787a7.7 7.7 0 0 1 1.482-.645 20 20 0 0 0 1.062-2.227 7.3 7.3 0 0 1-.43-1.295c-.086-.4-.119-.796-.046-1.136.075-.354.274-.672.65-.823.192-.077.4-.12.602-.077a.7.7 0 0 1 .477.365c.088.164.12.356.127.538.007.188-.012.396-.047.614-.084.51-.27 1.134-.52 1.794a11 11 0 0 0 .98 1.686 5.8 5.8 0 0 1 1.334.05c.364.066.734.195.96.465.12.144.193.32.2.518.007.192-.047.382-.138.563a1.04 1.04 0 0 1-.354.416.86.86 0 0 1-.51.138c-.331-.014-.654-.196-.933-.417a5.7 5.7 0 0 1-.911-.95 11.7 11.7 0 0 0-1.997.406 11.3 11.3 0 0 1-1.02 1.51c-.292.35-.609.656-.927.787a.8.8 0 0 1-.58.029m1.379-1.901q-.25.115-.459.238c-.328.194-.541.383-.647.547-.094.145-.096.25-.04.361q.016.032.026.044l.035-.012c.137-.056.355-.235.635-.572a8 8 0 0 0 .45-.606m1.64-1.33a13 13 0 0 1 1.01-.193 12 12 0 0 1-.51-.858 21 21 0 0 1-.5 1.05zm2.446.45q.226.245.435.41c.24.19.407.253.498.256a.1.1 0 0 0 .07-.015.3.3 0 0 0 .094-.125.44.44 0 0 0 .059-.2.1.1 0 0 0-.026-.063c-.052-.062-.2-.152-.518-.209a4 4 0 0 0-.612-.053zM8.078 7.8a7 7 0 0 0 .2-.828q.046-.282.038-.465a.6.6 0 0 0-.032-.198.5.5 0 0 0-.145.04c-.087.035-.158.106-.196.283-.04.192-.03.469.046.822q.036.167.09.346z"/></svg>'
_ICON_FILE_CODE = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="attachment-icon" viewBox="0 0 16 16"><path d="M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5zm-3 0A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V4.5z"/><path d="M8.646 6.646a.5.5 0 0 1 .708 0l2 2a.5.5 0 0 1 0 .708l-2 2a.5.5 0 0 1-.708-.708L10.293 9 8.646 7.354a.5.5 0 0 1 0-.708m-1.292 0a.5.5 0 0 0-.708 0l-2 2a.5.5 0 0 0 0 .708l2 2a.5.5 0 0 0 .708-.708L5.707 9l1.647-1.646a.5.5 0 0 0 0-.708"/></svg>'
_ICON_FILE_PLAY = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="attachment-icon" viewBox="0 0 16 16"><path d="M6 6.883v4.234a.5.5 0 0 0 .757.429l3.528-2.117a.5.5 0 0 0 0-.858L6.757 6.454a.5.5 0 0 0-.757.43z"/><path d="M14 14V4.5L9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2M9.5 3A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h5.5z"/></svg>'
_ICON_FILE_IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="attachment-icon" viewBox="0 0 16 16"><path d="M6.502 7a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3"/><path d="M14 14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5L14 4.5zM4 1a1 1 0 0 0-1 1v10l2.224-2.224a.5.5 0 0 1 .61-.075L8 11l2.157-3.02a.5.5 0 0 1 .76-.063L13 10V4.5h-2A1.5 1.5 0 0 1 9.5 3V1z"/></svg>'
_ICON_FILE_GENERIC = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="attachment-icon" viewBox="0 0 16 16"><path d="M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5zm-3 0A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V4.5z"/></svg>'

# Extension → icon, grouped by what each Bootstrap Icon actually depicts
# rather than one entry per extension. Confirmed real on phpbbmodders.net's
# own dump: zip/rar dominate non-image attachments, with a handful of txt/
# pdf/xml/swf/wmv/js/psd — the rest of each group's extensions are included
# for real-world coverage beyond this one dump, not because they were all
# individually confirmed present here.
_EXT_ICONS: dict[str, str] = {
    **{e: _ICON_FILE_ZIP for e in ("zip", "rar", "7z", "tar", "gz", "bz2", "xz")},
    **{e: _ICON_FILE_TEXT for e in ("txt", "log", "csv", "rtf", "md")},
    "pdf": _ICON_FILE_PDF,
    **{e: _ICON_FILE_CODE for e in (
        "js", "php", "py", "html", "htm", "css", "json", "xml", "sql",
        "c", "cpp", "h", "java", "sh", "bat", "yml", "yaml",
    )},
    **{e: _ICON_FILE_PLAY for e in (
        "swf", "wmv", "mp4", "avi", "mov", "mkv", "mp3", "wav", "ogg", "flac",
    )},
    **{e: _ICON_FILE_IMAGE for e in ("psd", "ai", "eps", "svg")},
}


def _attachment_ext_badge(filename: str) -> str:
    """An icon for a non-image attachment link, matched to the file's own
    extension via Bootstrap Icons (https://icons.getbootstrap.com/, MIT) —
    a bare SQL dump carries no per-filetype icon set of its own (phpBB's
    mimetype icons live alongside the software install, not in the
    database), so this maps extension text to a bundled icon instead of a
    fabricated/guessed one. Icon-only (no separate text label) since the
    extension is already visible in the attachment's own filename right
    next to it; the extension is still present as the icon's <title>, for
    a hover tooltip and screen readers. An extension not in _EXT_ICONS
    (including one too long to plausibly be a real extension, e.g. a
    filename with no real extension at all) falls back to a generic file
    icon rather than guessing."""
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    if not ext or len(ext) > 5:
        return ""
    icon = _EXT_ICONS.get(ext.lower(), _ICON_FILE_GENERIC)
    icon = icon.replace(
        'viewBox="0 0 16 16">',
        f'viewBox="0 0 16 16"><title>{html.escape(ext.upper())}</title>',
        1,
    )
    return f'{icon} '


class PhpbbBBCodeParser:
    def __init__(self, smilies: list[dict], attachments: dict[int, list[dict]],
                 custom_bbcodes: list[dict] | None = None,
                 assets_prefix: str = "../assets",
                 bad_attachments: set[str] | None = None,
                 external_images: dict[str, str] | None = None,
                 internal_topic_ids: set[int] | None = None,
                 bad_smilies: set[str] | None = None,
                 board_hosts: set[str] | None = None):
        # Map smiley code → (image filename, display width, display height).
        # phpBB stores a smiley pack's *intended* display size separately
        # from its source image files, which are often much larger (a
        # 202x214 source file displayed at 17x18) — width/height 0 means
        # "not set" (older/custom dumps), so the size attrs are omitted and
        # the browser falls back to the image's native size, same as before.
        self.smilies = {
            s["code"]: (s["smiley_url"], s.get("smiley_width") or 0, s.get("smiley_height") or 0)
            for s in smilies
        }
        # Reverse lookup by filename for the older HTML-comment smiley
        # format (_convert_smilies), which only gives us the image filename
        # from the existing <img src>, not the code.
        self.smiley_sizes_by_filename = {
            filename: (w, h) for filename, w, h in self.smilies.values() if w and h
        }
        # smiley_url filenames missing/corrupted in the dump (see
        # find_bad_smilies) — left as raw code text rather than rendered as
        # a broken image, same treatment as an unrecognized code.
        self.bad_smilies = bad_smilies or set()
        # Map post_id → list of attachment dicts (ordered)
        self.attachments = attachments
        # Custom BBCodes: list of dicts with bbcode_tag, bbcode_match, bbcode_tpl
        self.custom_bbcodes = custom_bbcodes or []
        self.assets_prefix = assets_prefix
        # physical_filename values that look like images but are missing or
        # fail to decode (e.g. corrupted in the source dump) — dropped
        # entirely from the rendered post rather than shown as a broken
        # image or a "missing attachment" placeholder.
        self.bad_attachments = bad_attachments or set()
        # Map external [img]/<IMG> URL → locally cached filename under
        # assets/external/ (see download_external_images). A URL missing
        # from this map is dead or undecodable, and its tag is dropped.
        self.external_images = external_images or {}
        # topic_ids actually present in this archive, for rewriting a post's
        # own link back to viewtopic.php?...t=N (a cross-reference to another
        # topic on the same board) into a relative link within the archive
        # instead of leaving it pointing at the original site. A topic_id
        # not in this set (excluded, or a link to a different board
        # entirely) is left as a normal external link.
        self.internal_topic_ids = internal_topic_ids or set()
        # Hostnames (lowercase, "www." stripped) recognized as this
        # board's own — a real board's domain can change over its
        # lifetime (e.g. phpbbmodders.net/.com/.org all really were this
        # same board at different times), so this isn't just the dump's
        # current server_name. A link with no host at all (relative, or
        # protocol-relative) is always same-site and doesn't need this
        # set at all. See _rewrite_internal_link.
        self.board_hosts = {h.lower().removeprefix("www.") for h in (board_hosts or set())}
        self.topics_prefix = re.sub(r'assets$', 'topics', assets_prefix)

    def _is_same_board_host(self, url: str) -> bool:
        """True if url has no host at all (relative, or a bare
        viewtopic.php?... reference — same-site by construction) or its
        host matches one of self.board_hosts. A topic-id match alone
        isn't enough to call a link internal: t=<N> is just a small
        integer, near-guaranteed to collide with some other phpBB
        install's own topic ids (confirmed on a real dump — thousands of
        links to unrelated boards like phpbb.com, each with its own t=N
        that can coincide with a topic id that also exists here)."""
        host = urllib.parse.urlparse(url).hostname
        return host is None or host.lower().removeprefix("www.") in self.board_hosts

    def _rewrite_internal_link(self, url: str) -> str:
        """If url points at this board's own viewtopic.php (see
        _is_same_board_host) for a topic that's actually in this archive,
        rewrite it to a relative topics/N.html link (preserving a #pNNNN
        post anchor if present) so cross-topic references stay working
        inside the static archive. Otherwise returns url with any phpBB
        session id stripped (see _strip_sid) — including a viewtopic.php
        link on a different board entirely, or to a topic that's been
        excluded from this archive, which stays a normal external link
        rather than becoming a broken or (worse) a wrong one."""
        topic_match = re.search(r'viewtopic\.php\?[^"#]*\bt=(\d+)', url)
        if topic_match and self._is_same_board_host(url):
            topic_id = int(topic_match.group(1))
            if topic_id in self.internal_topic_ids:
                fragment_match = re.search(r'(#p\d+)', url)
                fragment = fragment_match.group(1) if fragment_match else ''
                return f'{self.topics_prefix}/{topic_id}.html{fragment}'
        result = self._strip_sid(url)
        # phpBB never validated a stored [url=...]'s scheme at write time,
        # so a real dump can still contain a spam/exploit post with
        # [url=javascript:...] or [url=data:...] — rendered as a live
        # link, that executes on click. Neutralize anything outside a
        # small allowlist (or no scheme at all, i.e. a relative/anchor
        # link, which can't execute) rather than trusting the dump.
        return result if self._is_safe_url(result) else '#'

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """True if url is safe to use as a rendered link's href."""
        lower = url.strip().lower()
        if lower.startswith(("http://", "https://", "ftp://", "ftps://", "mailto:")):
            return True
        # No scheme at all (relative path, #anchor, or protocol-relative
        # //host/...) is safe — a scheme is only present if there's a ':'
        # before the first path/query/fragment separator.
        prefix = re.split(r'[/?#]', lower, maxsplit=1)[0]
        return ':' not in prefix

    @staticmethod
    def _strip_sid(url: str) -> str:
        """Remove a phpBB session id (sid=<32 lowercase hex chars>, an md5
        hash — phpBB's own convention for every one of its scripts:
        viewtopic.php, index.php, admin_*.php, third-party MOD scripts,
        on any phpBB install anywhere, not just this board) from a URL's
        query string. Meaningless in an archived/static context (session
        ids expire almost immediately), and not worth keeping around.
        Uses urllib to rebuild the query string properly rather than
        regex-splicing it, so the surrounding params stay well-formed."""
        parsed = urllib.parse.urlparse(url)
        if not parsed.query:
            return url
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in params if not (k == "sid" and re.fullmatch(r'[0-9a-f]{32}', v))]
        if len(filtered) == len(params):
            return url
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(filtered)))

    def _append_trailing_attachments(self, result: str, post_id: int, original_text: str) -> str:
        """Append attachments that had no [attachment=N] inline tag."""
        post_attachments = self.attachments.get(post_id, [])
        if not post_attachments:
            return result

        # Collect indices already embedded in the original text. Checked
        # against original_text, which still has its UID suffix
        # (:abc123]) at this point — [attachment=N] must match it too, or
        # every UID-tagged post's inline attachments look unembedded here
        # and get appended a second time in the trailing section.
        embedded: set[int] = set()
        for m in re.finditer(r'\[attachment=(\d+)(?::[^\]]*)?\]', original_text):
            embedded.add(int(m.group(1)))
        for m in re.finditer(r'<ATTACHMENT[^>]*\bindex="(\d+)"', original_text, re.IGNORECASE):
            embedded.add(int(m.group(1)))

        trailing = []
        for i, att in enumerate(post_attachments):
            if i in embedded:
                continue
            physical = att["physical_filename"]
            real = att["real_filename"]
            is_image = real.lower().endswith(IMAGE_EXTENSIONS)
            if physical in self.bad_attachments:
                continue
            path = f"{self.assets_prefix}/attachments/{physical}/{urllib.parse.quote(real)}"
            if is_image:
                trailing.append(
                    f'<div class="inline-attachment">'
                    f'<a href="{path}" download="{html.escape(real)}">'
                    f'<img src="{path}" alt="{html.escape(real)}" loading="lazy" /></a>'
                    f'<br/><em>Attachment: {html.escape(real)}</em></div>'
                )
            else:
                trailing.append(
                    f'<div class="inline-attachment">{_attachment_ext_badge(real)}<a href="{path}" download="{html.escape(real)}">Attachment: {html.escape(real)}</a></div>'
                )

        if trailing:
            result += '\n<div class="post-attachments">' + "".join(trailing) + "</div>"
        return result

    def convert(self, text: str, uid: str, post_id: int | None = None,
                enable_smilies: bool = True) -> str:
        """Convert phpBB BBCode text to HTML.

        phpBB 3.2+ stores text in one of two formats:
        - Old UID BBCode: [b:abc123]text[/b:abc123]
        - XML markup:     <r><B><s>[b]</s>text<e>[/b]</e></B></r>
        Detect which format and dispatch accordingly.

        enable_smilies mirrors phpbb_posts.enable_smilies — a per-post flag
        the poster could uncheck, storing the smiley source (a code like
        ":)", or an <E> element) unconverted; False leaves that raw text
        as-is rather than resolving it to an image, matching what the post
        actually looked like when it was written.
        """
        original_text = text  # saved for trailing-attachment detection

        if text.lstrip().startswith("<r>") or text.lstrip().startswith("<t>"):
            # phpBB XML markup format — already has explicit <br/> for line breaks
            text = self._convert_xml_markup(text, post_id, enable_smilies)
        else:
            # Step 1: Strip UID suffixes from BBCode tags
            if uid:
                text = text.replace(f":{uid}]", "]")

            # Step 2: Resolve smilies (before BBCode, since they're HTML comments)
            text = self._convert_smilies(text, enable_smilies)

            # Step 3: Resolve attachments
            if post_id is not None:
                text = self._convert_attachments(text, post_id)

            # Step 4: Convert BBCode tags to HTML
            text = self._convert_bbcode(text)

            # Step 5: Apply custom BBCodes from phpbb_bbcodes table
            text = self._convert_custom_bbcodes(text)

            # Step 6: Convert newlines to <br> (BBCode path only — XML path uses <br/> already)
            parts = re.split(r'(<pre>.*?</pre>)', text, flags=re.DOTALL)
            text = "".join(
                part if part.startswith("<pre>") else part.replace("\n", "<br>\n")
                for part in parts
            )

        # Append any attachments not embedded inline via [attachment=N] tags
        if post_id is not None:
            text = self._append_trailing_attachments(text, post_id, original_text)

        return text

    def _convert_xml_markup(self, text: str, post_id: int | None = None,
                             enable_smilies: bool = True) -> str:
        """Convert phpBB 3.2+ XML markup format to HTML.

        In this format:
          <r>                        root wrapper
          <B><s>[b]</s>x<e>[/b]</e></B>   bold block; <s>/<e> hold the raw BBCode syntax
          <s>...</s>                 start-tag marker — strip content entirely
          <e>...</e>                 end-tag marker — strip content entirely
          <i>...</i>                 inline wrapper — strip tag, keep content
        """
        # Normalize <br/> (XML self-closing) to <br> and drop the newline that follows
        text = re.sub(r'<br\s*/?>\n?', '<br>', text)

        # Strip <s>...</s> and <e>...</e> (phpBB syntax markers, not content)
        text = re.sub(r'<s>[^<]*</s>', '', text)
        text = re.sub(r'<e>[^<]*</e>', '', text)
        # Strip <i> wrappers (keep inner content)
        text = re.sub(r'<i>(.*?)</i>', r'\1', text, flags=re.DOTALL)
        # <LINK_TEXT text="shortened display text">full raw URL</LINK_TEXT>
        # phpBB truncates a long auto-linked URL for display (e.g. "http://
        # example.com/... ... /page") while keeping the full URL as the
        # element's own text content — replace the whole element with just
        # the shortened text attribute, which is what should actually be
        # shown. Not caught by the generic "strip unknown XML tags" cleanup
        # further below: that regex requires an all-caps/digit tag name,
        # and LINK_TEXT's underscore falls outside that character class.
        text = re.sub(r'<LINK_TEXT text="([^"]*)">.*?</LINK_TEXT>', r'\1', text, flags=re.DOTALL)
        # Strip root <r>/<t> wrappers
        text = re.sub(r'^<[rt]>', '', text.lstrip())
        text = re.sub(r'</[rt]>$', '', text.rstrip())

        # Bracket-syntax [code]...[/code] that never got converted to the
        # XML <CODE> tag (a real, observed migration gap: a post whose
        # overall content is XML-wrapped can still carry old-style
        # unconverted bracket text inside it) — stashed before any
        # semantic tag below can reach into it, same as the old-BBCode
        # path. Without this it was rendered as literal, un-boxed
        # "[code]...[/code]" text sitting in the middle of the post.
        text, code_blocks = self._stash_code_blocks(text)
        # Stashed content is a raw substring of the XML document, where a
        # real "&"/"<"/">" the user actually typed is stored entity-
        # escaped per XML rules (e.g. "&lt;div&gt;" for a literal "<div>"
        # typed as a code example) — html.unescape() it back to real
        # characters now, so _restore_code_blocks()'s own html.escape()
        # re-escapes it exactly once. Without this, a real "<div>" typed
        # inside [code] round-tripped as literal visible "&lt;div&gt;"
        # text on the page instead of "<div>". A no-op for the one
        # deliberately pre-normalized exception, the literal "<br>" line-
        # break marker (see _restore_code_blocks) — html.unescape() only
        # touches recognized entity sequences, not bare "<"/">".
        code_blocks = [html.unescape(c) for c in code_blocks]

        # Semantic block elements → HTML
        text = re.sub(r'<B>', '<strong>', text)
        text = re.sub(r'</B>', '</strong>', text)
        text = re.sub(r'<I>', '<em>', text)
        text = re.sub(r'</I>', '</em>', text)
        text = re.sub(r'<U>', '<span style="text-decoration:underline">', text)
        text = re.sub(r'</U>', '</span>', text)
        text = re.sub(r'<S>', '<del>', text)
        text = re.sub(r'</S>', '</del>', text)
        text = re.sub(r'<CODE[^>]*>', '<div class="codebox"><pre><code>', text)
        text = re.sub(r'</CODE>', '</code></pre></div>', text)
        text = re.sub(r'<QUOTE[^>]*author="([^"]*)"[^>]*>', r'<blockquote class="uncited"><div class="quote-header">\1 wrote:</div>', text)
        text = re.sub(r'<QUOTE[^>]*>', '<blockquote class="uncited">', text)
        text = re.sub(r'</QUOTE>', '</blockquote>', text)
        # <IMG src="url">optional text</IMG> — capture src, discard inner
        # text; resolved against the locally cached copy, same as [img]
        # BBCode above. A dead or undecodable URL is dropped.
        def replace_xml_img(match):
            # html.unescape() matches find_image_urls()'s own discovery-time
            # decoding (generate.py) — an XML attribute/text URL containing
            # a real "&" is stored entity-escaped ("&amp;"), and the cache
            # dict is keyed by the decoded form on both sides.
            name = self.external_images.get(html.unescape(match.group(1).strip()))
            if not name:
                return ''
            return f'<img src="{self.assets_prefix}/external/{name}" class="postimage" alt="image" loading="lazy">'

        def replace_xml_img_url(url):
            name = self.external_images.get(html.unescape(url.strip()))
            if not name:
                return ''
            return f'<img src="{self.assets_prefix}/external/{name}" class="postimage" alt="image" loading="lazy">'

        # Migration artifacts from boards that started on phpBB2, where
        # [img]...[/img] BBCode wasn't reconverted to a proper <IMG>
        # element, leaving literal [img]/[/img] bracket text sitting
        # around content that a *different* auto-conversion pass already
        # touched. Three shapes seen in the wild (665/4/277 posts on a
        # real board), all must run before the generic <URL> conversion
        # below, which would otherwise absorb the first two:
        #
        # (a) [img]<URL url="X">...</URL>[/img] — the auto-linked bare
        #     URL inside it, sometimes further wrapped in <LINK_TEXT> for
        #     display shortening, sometimes with stray characters (seen:
        #     a bare "." between </URL> and [/img]; a mangled leading
        #     fragment like "ttp://" — a dropped "h" — between [img] and
        #     <URL). The short bounded gaps on both sides still require
        #     an actual <URL>...</URL> to follow, so this can't drift
        #     into matching unrelated prose.
        text = re.sub(r'\[img\][^<]{0,20}<URL url="([^"]*)"[^>]*>.*?</URL>[^\[]*?\[/img\]', replace_xml_img, text, flags=re.DOTALL)
        # (b) [img]<ATTACHMENT ...>...</ATTACHMENT>[/img] — the inner
        #     element already renders correctly on its own via the
        #     <ATTACHMENT> handling further below; just drop the leftover
        #     brackets around it rather than re-resolving it here.
        text = re.sub(r'\[img\](<ATTACHMENT(?:\s[^>]*)?>.*?</ATTACHMENT>)[^\[]*?\[/img\]', r'\1', text, flags=re.DOTALL)
        # (c) [img]bare-url[/img] never touched by any XML conversion at
        #     all, typically sitting inside a <URL>...</URL> whose own
        #     [url=...] wrapper WAS converted correctly (a link around an
        #     image, phpBB's "clickable thumbnail" pattern). Scoped to
        #     content that is *exactly* a bare URL and nothing else, so
        #     this can never match a post that merely discusses "[img]"
        #     as text (e.g. quoting phpBB's own BBCode config verbatim,
        #     seen in the same dump) — such content never has a bare URL
        #     as the sole span between the brackets.
        text = re.sub(r'\[img\](https?://[^\s\[\]<>]+)\[/img\]', lambda m: replace_xml_img_url(m.group(1)), text)

        # The url="..." attribute value comes straight from the raw XML
        # document, where a real "&" is stored entity-escaped as "&amp;"
        # per XML rules — html.unescape() it back to real characters
        # before any further processing (topic-id extraction, sid
        # stripping, host matching), or a multi-parameter link's own "&"
        # separators get treated as literal text instead of real ones,
        # and the final html.escape() below would escape the already-
        # escaped text a second time (confirmed: a real "?a=1&amp;b=2"
        # rendered as "?a=1&amp;amp;b=2" — a browser reads that back as
        # literal text "&amp;" glued onto "b=2", not a second parameter).
        text = re.sub(r'<URL url="([^"]*)"[^>]*>', lambda m: f'<a href="{html.escape(self._rewrite_internal_link(html.unescape(m.group(1))), quote=True)}" class="postlink">', text)
        text = re.sub(r'</URL>', '</a>', text)
        text = re.sub(r'<IMG\s+src="([^"]*)"[^>]*>.*?</IMG>', replace_xml_img, text, flags=re.DOTALL)
        # Also handle self-closing form
        text = re.sub(r'<IMG\s+src="([^"]*)"[^>]*/>', replace_xml_img, text)
        text = re.sub(r'<COLOR color="([^"]*)">', r'<span style="color:\1">', text)
        text = re.sub(r'</COLOR>', '</span>', text)
        text = re.sub(r'<SIZE size="(\d+)">', r'<span style="font-size:\1%">', text)
        text = re.sub(r'</SIZE>', '</span>', text)
        text = re.sub(r'<LIST[^>]*>', '<ul>', text)
        text = re.sub(r'</LIST>', '</ul>', text)
        text = re.sub(r'<LI>', '<li>', text)
        text = re.sub(r'</LI>', '</li>', text)
        text = re.sub(r'<SPOILER[^>]*>', '<details class="spoiler"><summary>Spoiler</summary>', text)
        text = re.sub(r'</SPOILER>', '</details>', text)

        # YouTube embeds: <YOUTUBE>video_id</YOUTUBE>
        text = re.sub(
            r'<YOUTUBE>(.*?)</YOUTUBE>',
            lambda m: (
                f'<div class="video-embed" style="margin:8px 0">'
                f'<iframe width="560" height="315" '
                f'src="https://www.youtube.com/embed/{m.group(1).strip()}" '
                f'frameborder="0" allowfullscreen></iframe></div>'
            ),
            text, flags=re.DOTALL,
        )

        # Attachments in XML format: <ATTACHMENT filename="..." index="N">...</ATTACHMENT>
        def _xml_attachment(m):
            attrs = m.group(1)
            fname_m = re.search(r'filename="([^"]*)"', attrs)
            idx_m = re.search(r'index="(\d+)"', attrs)
            filename = fname_m.group(1) if fname_m else ""
            index = int(idx_m.group(1)) if idx_m else 0
            post_attachments = self.attachments.get(post_id, []) if post_id is not None else []
            if index < len(post_attachments):
                physical = post_attachments[index]["physical_filename"]
                real = post_attachments[index]["real_filename"]
                is_image = real.lower().endswith(IMAGE_EXTENSIONS)
                if physical in self.bad_attachments:
                    return ''
                path = f"{self.assets_prefix}/attachments/{physical}/{urllib.parse.quote(real)}"
                if is_image:
                    return (f'<div class="inline-attachment">'
                            f'<a href="{path}" download="{html.escape(real)}">'
                            f'<img src="{path}" alt="{html.escape(real)}" loading="lazy" /></a>'
                            f'<br/><em>Attachment: {html.escape(real)}</em></div>')
                return f'<div class="inline-attachment">{_attachment_ext_badge(real)}<a href="{path}" download="{html.escape(real)}">Attachment: {html.escape(real)}</a></div>'
            logger.warning("XML attachment index %d out of range for post %s", index, post_id)
            if filename.lower().endswith(IMAGE_EXTENSIONS):
                return ''
            return f'<span class="attachment-missing">[Attachment: {html.escape(filename)}]</span>'

        text = re.sub(r'<ATTACHMENT(\s[^>]*)?>.*?</ATTACHMENT>', _xml_attachment, text, flags=re.DOTALL)

        # Horizontal rule — convert element, then strip any orphan [hr]/[/hr] text nodes
        # (phpBB may store <HR/>[hr] where [hr] is a bare text node, not inside <s>)
        text = re.sub(r'<HR\s*/?>', '<hr>', text)
        text = text.replace('[hr]', '').replace('[/hr]', '')

        # Smilies stored as <E>code</E> (phpBB XML markup format) — resolved
        # against the same phpbb_smilies code → filename map as the older
        # HTML-comment format below. An unrecognized code (not in the dump's
        # smilies table) is left as its raw text rather than dropped. When
        # this post has smilies disabled (enable_smilies=0), skip resolving
        # them entirely — the generic "strip unknown XML tags" cleanup
        # below removes the bare <E>/</E> wrapper either way, leaving the
        # original raw code text visible, matching what the post actually
        # looked like when it was written.
        def replace_xml_smiley(m):
            entry = self.smilies.get(m.group(1))
            if not entry:
                return m.group(1)
            filename, w, h = entry
            if filename in self.bad_smilies:
                return m.group(1)
            size_attrs = f' width="{w}" height="{h}"' if w and h else ''
            return f'<img src="{self.assets_prefix}/images/smilies/{filename}" alt="{html.escape(m.group(1))}" class="smilies"{size_attrs} />'
        if enable_smilies:
            text = re.sub(r'<E>([^<]*)</E>', replace_xml_smiley, text)

        # Smilies stored in the older HTML-comment format (mixed-era dumps)
        text = self._convert_smilies(text, enable_smilies)

        # Strip any remaining unknown XML tags (e.g. custom elements)
        text = re.sub(r'<[A-Z][A-Z0-9]*(?:\s[^>]*)?>|</[A-Z][A-Z0-9]*>', '', text)

        # Restore code blocks last (see the stash earlier in this method).
        text = self._restore_code_blocks(text, code_blocks)

        return text

    def _convert_smilies(self, text: str, enable_smilies: bool = True) -> str:
        """Replace phpBB smiley HTML comments with <img> tags. enable_smilies
        mirrors phpbb_posts.enable_smilies — False leaves the raw code text
        (the comment's own alt) as-is rather than resolving it to an image,
        same treatment already given a smiley whose image is missing."""
        def replace_smiley(match):
            full = match.group(0)
            # Extract the image filename from the existing img tag
            img_match = re.search(r'src="[^"]*?/([^/"]+)"', full)
            if img_match:
                filename = img_match.group(1)
                # phpBB's own comment format already carries the code as the
                # img's alt (e.g. alt=":)"); reuse it instead of a generic
                # label — falls back to "smiley" only if a dump lacks it.
                alt_match = re.search(r'alt="([^"]*)"', full)
                alt = html.escape(alt_match.group(1)) if alt_match else "smiley"
                if not enable_smilies or filename in self.bad_smilies:
                    return alt
                w, h = self.smiley_sizes_by_filename.get(filename, (0, 0))
                size_attrs = f' width="{w}" height="{h}"' if w and h else ''
                return f'<img src="{self.assets_prefix}/images/smilies/{filename}" alt="{alt}" class="smilies"{size_attrs} />'
            return full

        # Match one smiley at a time: <!-- s<code> --><img .../><!-- s<code> -->
        # Use [^-]* instead of .*? to avoid matching across multiple smiley blocks
        text = re.sub(
            r'<!-- s[^-]* --><img[^>]*/>\s*<!-- s[^-]* -->',
            replace_smiley,
            text
        )
        return text

    def _convert_attachments(self, text: str, post_id: int) -> str:
        """Replace [attachment=N]filename[/attachment] with links/images."""
        post_attachments = self.attachments.get(post_id, [])

        def replace_attachment(match):
            index = int(match.group(1))
            filename = match.group(2)
            if index < len(post_attachments):
                physical = post_attachments[index]["physical_filename"]
                real = post_attachments[index]["real_filename"]
            else:
                logger.warning("Attachment index %d out of range for post %d", index, post_id)
                if filename.lower().endswith(IMAGE_EXTENSIONS):
                    return ''
                return f'<span class="attachment-missing">[Attachment: {html.escape(filename)}]</span>'

            is_image = real.lower().endswith(IMAGE_EXTENSIONS)
            if physical in self.bad_attachments:
                return ''

            path = f"{self.assets_prefix}/attachments/{physical}/{urllib.parse.quote(real)}"
            # If it looks like an image, embed it; otherwise link it
            if is_image:
                return f'<div class="inline-attachment"><a href="{path}" download="{html.escape(real)}"><img src="{path}" alt="{html.escape(real)}" loading="lazy" /></a><br/><em>Attachment: {html.escape(real)}</em></div>'
            else:
                return f'<div class="inline-attachment">{_attachment_ext_badge(real)}<a href="{path}" download="{html.escape(real)}">Attachment: {html.escape(real)}</a></div>'

        text = re.sub(
            r'\[attachment=(\d+)\](.*?)\[/attachment\]',
            replace_attachment,
            text,
            flags=re.DOTALL,
        )
        return text

    @staticmethod
    def _stash_code_blocks(text: str) -> tuple[str, list[str]]:
        """Extract [code]...[/code] blocks (bracket syntax — the older
        BBCode form, and also how a code block can show up even inside
        XML-format content that never got fully migrated) into a list,
        replacing each with a unique placeholder. Must run before any
        other BBCode/XML substitution: those would otherwise also fire
        *inside* a code block's own content — confirmed:
        [code][b]literal[/b][/code] rendered as real bold text instead of
        showing the BBCode example as it was actually written, silently
        changing an archived example into a different one. See
        _restore_code_blocks for the other half."""
        code_blocks: list[str] = []

        def _stash(m):
            code_blocks.append(m.group(1))
            return f'\x00CODEBLOCK{len(code_blocks) - 1}\x00'
        return re.sub(r'\[code\](.*?)\[/code\]', _stash, text, flags=re.DOTALL), code_blocks

    @staticmethod
    def _restore_code_blocks(text: str, code_blocks: list[str]) -> str:
        """Restore placeholders from _stash_code_blocks, verbatim and
        HTML-escaped — a real code/HTML/PHP example can easily contain a
        literal < > or &, which would otherwise be interpreted as actual
        markup instead of shown as the text it is. A literal <br> is the
        one exception: the earlier <br/> normalization in
        _convert_xml_markup runs before a [code] block is stashed, so a
        <br> landing inside one is phpBB's own line-break marker, not
        code content — restored as a real line break (<pre> already
        preserves it visually) rather than shown as escaped text."""
        def _restore(m):
            escaped = html.escape(code_blocks[int(m.group(1))]).replace('&lt;br&gt;', '<br>')
            return f'<div class="codebox"><pre><code>{escaped}</code></pre></div>'
        return re.sub(r'\x00CODEBLOCK(\d+)\x00', _restore, text)

    def _convert_bbcode(self, text: str) -> str:
        """Convert standard BBCode tags to HTML."""
        text, code_blocks = self._stash_code_blocks(text)

        # Bold
        text = re.sub(r'\[b\](.*?)\[/b\]', r'<strong>\1</strong>', text, flags=re.DOTALL)
        # Italic
        text = re.sub(r'\[i\](.*?)\[/i\]', r'<em>\1</em>', text, flags=re.DOTALL)
        # Underline
        text = re.sub(r'\[u\](.*?)\[/u\]', r'<span style="text-decoration: underline">\1</span>', text, flags=re.DOTALL)
        # Strikethrough
        text = re.sub(r'\[s\](.*?)\[/s\]', r'<del>\1</del>', text, flags=re.DOTALL)

        # URL with label
        text = re.sub(
            r'\[url=([^\]]+)\](.*?)\[/url\]',
            lambda m: f'<a href="{html.escape(self._rewrite_internal_link(m.group(1)), quote=True)}" class="postlink">{m.group(2)}</a>',
            text, flags=re.DOTALL,
        )
        # URL bare
        text = re.sub(
            r'\[url\](.*?)\[/url\]',
            lambda m: f'<a href="{html.escape(self._rewrite_internal_link(m.group(1)), quote=True)}" class="postlink">{m.group(1)}</a>',
            text, flags=re.DOTALL,
        )

        # Image — resolved against the locally cached copy of the external
        # URL; a dead or undecodable URL (not in external_images) is dropped
        # rather than left as a broken hotlink. html.unescape() matches
        # find_image_urls()'s own discovery-time decoding (generate.py).
        def replace_img(match):
            name = self.external_images.get(html.unescape(match.group(1).strip()))
            if not name:
                return ''
            return f'<img src="{self.assets_prefix}/external/{name}" class="postimage" alt="image" loading="lazy" />'
        text = re.sub(r'\[img\](.*?)\[/img\]', replace_img, text, flags=re.DOTALL)

        # Quote with author
        text = re.sub(
            r'\[quote="([^"]+)"\](.*?)\[/quote\]',
            r'<blockquote class="uncited"><div class="quote-header">\1 wrote:</div>\2</blockquote>',
            text, flags=re.DOTALL,
        )
        # Quote without author
        text = re.sub(
            r'\[quote\](.*?)\[/quote\]',
            r'<blockquote class="uncited">\1</blockquote>',
            text, flags=re.DOTALL,
        )

        # Color
        text = re.sub(
            r'\[color=([^\]]+)\](.*?)\[/color\]',
            r'<span style="color: \1">\2</span>',
            text, flags=re.DOTALL,
        )

        # Size (phpBB uses percentage, map to font-size)
        text = re.sub(
            r'\[size=(\d+)\](.*?)\[/size\]',
            r'<span style="font-size: \1%">\2</span>',
            text, flags=re.DOTALL,
        )

        # List
        text = re.sub(r'\[list\](.*?)\[/list\]', lambda m: self._convert_list(m.group(1)), text, flags=re.DOTALL)
        text = re.sub(r'\[list=\d+\](.*?)\[/list\]', lambda m: self._convert_list(m.group(1), ordered=True), text, flags=re.DOTALL)

        # Spoiler (common phpBB custom BBCode)
        text = re.sub(
            r'\[spoiler\](.*?)\[/spoiler\]',
            r'<details class="spoiler"><summary>Spoiler</summary>\1</details>',
            text, flags=re.DOTALL,
        )

        # Horizontal rule — phpBB stores as [hr:uid][/hr:uid], so consume both
        text = re.sub(r'\[hr\]', '<hr>', text)
        text = text.replace('[/hr]', '')

        # Restore code blocks last (see the stash at the top of this method).
        text = self._restore_code_blocks(text, code_blocks)

        return text

    def _convert_list(self, content: str, ordered: bool = False) -> str:
        """Convert [*] items into <li> elements."""
        items = re.split(r'\[\*\]', content)
        items = [item.strip() for item in items if item.strip()]
        tag = "ol" if ordered else "ul"
        li_items = "".join(f"<li>{item}</li>" for item in items)
        return f"<{tag}>{li_items}</{tag}>"

    def _convert_custom_bbcodes(self, text: str) -> str:
        """Apply custom BBCodes defined in phpbb_bbcodes table.

        Each custom BBCode has a bbcode_match (regex-like pattern) and
        bbcode_tpl (HTML template with {SIMPLETEXT}, {TEXT}, etc. placeholders).
        Best-effort: log and skip any that fail to compile.
        """
        for bbcode in self.custom_bbcodes:
            tag = bbcode.get("bbcode_tag", "")
            match_pattern = bbcode.get("bbcode_match", "")
            template = bbcode.get("bbcode_tpl", "")
            if not tag or not match_pattern or not template:
                continue
            try:
                # Convert phpBB match pattern to regex
                # phpBB uses {TEXT}, {SIMPLETEXT}, {URL}, {NUMBER}, etc.
                regex = re.escape(match_pattern)
                regex = regex.replace(r"\{TEXT\}", "(.+?)")
                regex = regex.replace(r"\{SIMPLETEXT\}", r"([a-zA-Z0-9_\-]+)")
                regex = regex.replace(r"\{URL\}", r"(https?://[^\s\]]+)")
                regex = regex.replace(r"\{NUMBER\}", r"(\d+)")
                regex = regex.replace(r"\{COLOR\}", r"([a-zA-Z]+|#[0-9a-fA-F]{3,6})")

                # Convert phpBB template to regex replacement
                replacement = template
                group_idx = 1
                for placeholder in re.findall(r"\{[A-Z]+\}", match_pattern):
                    replacement = replacement.replace(placeholder, f"\\{group_idx}", 1)
                    group_idx += 1

                text = re.sub(regex, replacement, text, flags=re.DOTALL)
            except re.error as e:
                logger.warning("Failed to apply custom BBCode [%s]: %s", tag, e)
        return text
