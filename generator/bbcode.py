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


def _attachment_ext_badge(filename: str) -> str:
    """A small extension badge for a non-image attachment link. A bare SQL
    dump carries no per-filetype icon set (phpBB's own mimetype icons live
    alongside the software install, not in the database), so this uses the
    file's own extension rather than a fabricated icon."""
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    if not ext or len(ext) > 5:
        return ""
    return f'<span class="attachment-ext">{html.escape(ext.upper())}</span> '


class PhpbbBBCodeParser:
    def __init__(self, smilies: list[dict], attachments: dict[int, list[dict]],
                 custom_bbcodes: list[dict] | None = None,
                 assets_prefix: str = "../assets",
                 bad_attachments: set[str] | None = None,
                 external_images: dict[str, str] | None = None,
                 internal_topic_ids: set[int] | None = None,
                 bad_smilies: set[str] | None = None):
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
        self.topics_prefix = re.sub(r'assets$', 'topics', assets_prefix)

    def _rewrite_internal_link(self, url: str) -> str:
        """If url points at this board's own viewtopic.php for a topic that's
        actually in this archive, rewrite it to a relative topics/N.html
        link (preserving a #pNNNN post anchor if present) so cross-topic
        references stay working inside the static archive. Otherwise
        returns url with any phpBB session id stripped (see _strip_sid) —
        including a viewtopic.php link to a topic that isn't in this
        archive (excluded, or a different board), which stays a normal
        external link rather than becoming a broken one."""
        topic_match = re.search(r'viewtopic\.php\?[^"#]*\bt=(\d+)', url)
        if topic_match:
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

        # Collect indices already embedded in the original text
        embedded: set[int] = set()
        for m in re.finditer(r'\[attachment=(\d+)\]', original_text):
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

    def convert(self, text: str, uid: str, post_id: int | None = None) -> str:
        """Convert phpBB BBCode text to HTML.

        phpBB 3.2+ stores text in one of two formats:
        - Old UID BBCode: [b:abc123]text[/b:abc123]
        - XML markup:     <r><B><s>[b]</s>text<e>[/b]</e></B></r>
        Detect which format and dispatch accordingly.
        """
        original_text = text  # saved for trailing-attachment detection

        if text.lstrip().startswith("<r>") or text.lstrip().startswith("<t>"):
            # phpBB XML markup format — already has explicit <br/> for line breaks
            text = self._convert_xml_markup(text, post_id)
        else:
            # Step 1: Strip UID suffixes from BBCode tags
            if uid:
                text = text.replace(f":{uid}]", "]")

            # Step 2: Resolve smilies (before BBCode, since they're HTML comments)
            text = self._convert_smilies(text)

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

    def _convert_xml_markup(self, text: str, post_id: int | None = None) -> str:
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
            name = self.external_images.get(match.group(1).strip())
            if not name:
                return ''
            return f'<img src="{self.assets_prefix}/external/{name}" class="postimage" alt="image" loading="lazy">'

        def replace_xml_img_url(url):
            name = self.external_images.get(url.strip())
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

        text = re.sub(r'<URL url="([^"]*)"[^>]*>', lambda m: f'<a href="{html.escape(self._rewrite_internal_link(m.group(1)), quote=True)}" class="postlink">', text)
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
        # smilies table) is left as its raw text rather than dropped.
        def replace_xml_smiley(m):
            entry = self.smilies.get(m.group(1))
            if not entry:
                return m.group(1)
            filename, w, h = entry
            if filename in self.bad_smilies:
                return m.group(1)
            size_attrs = f' width="{w}" height="{h}"' if w and h else ''
            return f'<img src="{self.assets_prefix}/images/smilies/{filename}" alt="{html.escape(m.group(1))}" class="smilies"{size_attrs} />'
        text = re.sub(r'<E>([^<]*)</E>', replace_xml_smiley, text)

        # Smilies stored in the older HTML-comment format (mixed-era dumps)
        text = self._convert_smilies(text)

        # Strip any remaining unknown XML tags (e.g. custom elements)
        text = re.sub(r'<[A-Z][A-Z0-9]*(?:\s[^>]*)?>|</[A-Z][A-Z0-9]*>', '', text)

        return text

    def _convert_smilies(self, text: str) -> str:
        """Replace phpBB smiley HTML comments with <img> tags."""
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
                if filename in self.bad_smilies:
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

    def _convert_bbcode(self, text: str) -> str:
        """Convert standard BBCode tags to HTML."""
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
        # rather than left as a broken hotlink.
        def replace_img(match):
            name = self.external_images.get(match.group(1).strip())
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

        # Code
        text = re.sub(
            r'\[code\](.*?)\[/code\]',
            r'<div class="codebox"><pre><code>\1</code></pre></div>',
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
