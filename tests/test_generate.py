from pathlib import Path

from generator.generate import _apache_redirects, _nginx_redirects, render_redirects


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
        assert "RewriteRule ^viewtopic\\.php$ /topics/%1.html#p%2? [R=301,L]" in result
        assert "RewriteRule ^viewforum\\.php$ /forums/%1.html? [R=301,L]" in result
        assert "RewriteRule ^memberlist\\.php$ /users/%1.html? [R=301,L]" in result

    def test_apache_with_prefix(self):
        result = _apache_redirects("board", "/")
        assert "RewriteRule ^board/viewtopic\\.php$ /topics/%1.html#p%2? [R=301,L]" in result
        assert "RewriteRule ^board/viewforum\\.php$ /forums/%1.html? [R=301,L]" in result
        assert "RewriteRule ^board/memberlist\\.php$ /users/%1.html? [R=301,L]" in result

    def test_apache_absolute_base_url(self):
        # A redirect rule that must run on a different host than the
        # archive itself (e.g. the old board's own subdomain) needs an
        # absolute target — a root-relative "/topics/..." would resolve
        # against that other host instead.
        result = _apache_redirects("", "https://archive.example.com/")
        assert "RewriteRule ^viewtopic\\.php$ https://archive.example.com/topics/%1.html#p%2? [R=301,L]" in result

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
