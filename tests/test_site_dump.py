"""Тесты для core.site_dump (валидация URL, имя файла, fallback-цепочка)."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.site_dump import (
    DumpResult,
    RendererUnavailableError,
    SiteDumpError,
    UrlValidationError,
    _LinkExtractor,
    _normalize_link,
    dump_url,
    merge_pdfs,
    publish_pdf,
    render_pdf,
    safe_filename_for,
    validate_url,
)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ValidateUrlTests(unittest.TestCase):
    def test_adds_https_scheme(self):
        # example.com резолвится в публичный IP — нормальный happy-path.
        self.assertTrue(validate_url("example.com").startswith("https://"))

    def test_blocks_localhost(self):
        with self.assertRaises(UrlValidationError):
            validate_url("http://localhost/x")

    def test_blocks_127_0_0_1(self):
        with self.assertRaises(UrlValidationError):
            validate_url("http://127.0.0.1:8080/")

    def test_blocks_link_local(self):
        with self.assertRaises(UrlValidationError):
            validate_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_private_10(self):
        with self.assertRaises(UrlValidationError):
            validate_url("http://10.0.0.1/")

    def test_blocks_private_192(self):
        with self.assertRaises(UrlValidationError):
            validate_url("http://192.168.1.1/")

    def test_blocks_ipv6_loopback(self):
        with self.assertRaises(UrlValidationError):
            validate_url("http://[::1]/")

    def test_rejects_file_scheme(self):
        with self.assertRaises(UrlValidationError):
            validate_url("file:///etc/passwd")

    def test_rejects_ftp(self):
        with self.assertRaises(UrlValidationError):
            validate_url("ftp://example.com/x")

    def test_rejects_empty(self):
        with self.assertRaises(UrlValidationError):
            validate_url("")


class SafeFilenameTests(unittest.TestCase):
    def test_extracts_host_and_path(self):
        n = safe_filename_for("https://example.com/foo/bar")
        self.assertTrue(n.startswith("sdump-example_com-foo-bar-"))
        self.assertTrue(n.endswith(".pdf"))

    def test_strips_special_chars(self):
        n = safe_filename_for("https://x.com/?q=hello world&a=b")
        # никаких пробелов/амперсандов в имени файла
        self.assertNotIn(" ", n)
        self.assertNotIn("&", n)

    def test_handles_no_path(self):
        n = safe_filename_for("https://example.com/")
        self.assertIn("example_com", n)


class RenderFallbackTests(unittest.TestCase):
    def test_renderer_unavailable_propagates_error(self):
        # обоих рендереров нет → SiteDumpError с обеими попытками внутри.
        async def case():
            with mock.patch("core.site_dump.render_with_playwright", side_effect=RendererUnavailableError("no pw")):
                with mock.patch("core.site_dump.render_with_weasyprint", side_effect=RendererUnavailableError("no wp")):
                    with self.assertRaises(SiteDumpError) as cm:
                        await render_pdf("https://example.com/", out_dir="/tmp/sd-test")
                    self.assertIn("playwright", str(cm.exception))
                    self.assertIn("weasyprint", str(cm.exception))

        run(case())

    def test_falls_back_to_weasyprint(self):
        async def case():
            fake_path = Path("/tmp/x.pdf")
            with mock.patch(
                "core.site_dump.render_with_playwright",
                side_effect=RendererUnavailableError("no pw"),
            ):
                with mock.patch(
                    "core.site_dump.render_with_weasyprint",
                    return_value=fake_path,
                ) as wp:
                    out = await render_pdf("https://example.com/", out_dir="/tmp/sd-test")
                    self.assertEqual(out, fake_path)
                    wp.assert_awaited_once()

        run(case())


class PublishTests(unittest.TestCase):
    def test_provider_none_returns_local(self):
        async def case():
            tmp = Path("/tmp/sd-publish-test.pdf")
            tmp.write_bytes(b"%PDF-1.4 stub")
            try:
                res = await publish_pdf(tmp, provider="none")
                self.assertEqual(res.provider, "local")
                self.assertTrue(res.url.startswith("file://"))
            finally:
                tmp.unlink(missing_ok=True)

        run(case())

    def test_unknown_provider(self):
        async def case():
            with self.assertRaises(SiteDumpError):
                await publish_pdf(Path("/tmp/nope"), provider="weirdcloud")

        run(case())


class DumpUrlPipelineTests(unittest.TestCase):
    def test_pipeline_calls_render_then_publish(self):
        async def case():
            fake_pdf = Path("/tmp/fake-pdf-test.pdf")
            fake_pdf.write_bytes(b"%PDF-1.4 stub-content")
            try:
                with mock.patch("core.site_dump.render_pdf", return_value=fake_pdf):
                    res = await dump_url(
                        "https://example.com/",
                        upload="none",
                    )
                self.assertIsInstance(res, DumpResult)
                self.assertEqual(res.pdf_path, fake_pdf)
                self.assertEqual(res.provider, "local")
                self.assertGreater(res.bytes, 0)
            finally:
                fake_pdf.unlink(missing_ok=True)

        run(case())


class LinkExtractorTests(unittest.TestCase):
    def test_extracts_anchors(self):
        ex = _LinkExtractor()
        ex.feed('<html><a href="/a">A</a><a href="https://x.test/b">B</a><div>no</div></html>')
        self.assertEqual(ex.links, ["/a", "https://x.test/b"])

    def test_ignores_non_anchor_tags(self):
        ex = _LinkExtractor()
        ex.feed('<link href="x.css"><script src="y.js"></script>')
        self.assertEqual(ex.links, [])


class NormalizeLinkTests(unittest.TestCase):
    def test_relative_to_absolute(self):
        self.assertEqual(
            _normalize_link("https://example.com/foo/", "/bar"),
            "https://example.com/bar",
        )

    def test_strips_fragment(self):
        self.assertEqual(
            _normalize_link("https://example.com/", "page#anchor"),
            "https://example.com/page",
        )

    def test_skips_javascript(self):
        self.assertIsNone(_normalize_link("https://example.com/", "javascript:void(0)"))

    def test_skips_mailto(self):
        self.assertIsNone(_normalize_link("https://example.com/", "mailto:a@b"))

    def test_skips_static_ext(self):
        self.assertIsNone(_normalize_link("https://example.com/", "logo.png"))
        self.assertIsNone(_normalize_link("https://example.com/", "/dist/app.js"))

    def test_skips_non_http(self):
        self.assertIsNone(_normalize_link("https://example.com/", "ftp://files/x"))

    def test_passes_https(self):
        self.assertEqual(
            _normalize_link("https://example.com/", "https://other.test/page"),
            "https://other.test/page",
        )


class MergePdfsTests(unittest.TestCase):
    def _stub_pdf(self, path: Path) -> Path:
        # Минимально валидный PDF с одной пустой страницей через pypdf.
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf not installed")
        w = PdfWriter()
        w.add_blank_page(width=72, height=72)
        with open(path, "wb") as f:
            w.write(f)
        w.close()
        return path

    def test_merges_two_pdfs(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            a = self._stub_pdf(tdp / "a.pdf")
            b = self._stub_pdf(tdp / "b.pdf")
            out = tdp / "merged.pdf"
            merge_pdfs([a, b], out)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)

            # Проверим, что в результате 2 страницы.
            from pypdf import PdfReader
            r = PdfReader(str(out))
            self.assertEqual(len(r.pages), 2)

    def test_skips_missing_parts(self):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            a = self._stub_pdf(tdp / "a.pdf")
            ghost = tdp / "ghost.pdf"  # не существует
            out = tdp / "merged.pdf"
            merge_pdfs([a, ghost], out)
            self.assertTrue(out.exists())

    def test_empty_list_raises(self):
        with self.assertRaises(SiteDumpError):
            merge_pdfs([], Path("/tmp/x.pdf"))


class FullModeDumpUrlTests(unittest.TestCase):
    """Проверяем что mode=full вызывает render_full_site, а не одну страницу."""

    def test_full_mode_calls_render_full_site(self):
        async def case():
            fake_pdf = Path("/tmp/fake-full.pdf")
            fake_pdf.write_bytes(b"%PDF-1.4 stub")
            try:
                from core.site_dump import FullSiteResult
                fsr = FullSiteResult(
                    pdf_path=fake_pdf,
                    pages=["https://example.com/", "https://example.com/a"],
                    rendered=2,
                    skipped=0,
                )
                with mock.patch("core.site_dump.render_full_site", return_value=fsr) as m:
                    res = await dump_url(
                        "https://example.com/",
                        upload="none",
                        mode="full",
                        max_pages=10,
                        max_depth=1,
                    )
                m.assert_awaited_once()
                self.assertEqual(res.pages, 2)
                self.assertEqual(res.pdf_path, fake_pdf)
            finally:
                fake_pdf.unlink(missing_ok=True)

        run(case())

    def test_single_mode_does_not_crawl(self):
        async def case():
            fake_pdf = Path("/tmp/fake-single.pdf")
            fake_pdf.write_bytes(b"%PDF-1.4 stub")
            try:
                with mock.patch("core.site_dump.render_pdf", return_value=fake_pdf) as r, \
                     mock.patch("core.site_dump.render_full_site") as fs:
                    res = await dump_url(
                        "https://example.com/",
                        upload="none",
                        mode="single",
                    )
                r.assert_awaited_once()
                fs.assert_not_called()
                self.assertEqual(res.pages, 1)
            finally:
                fake_pdf.unlink(missing_ok=True)

        run(case())


class CrawlPagesTests(unittest.TestCase):
    """Краулер: same-domain only, валидация SSRF на каждой ссылке."""

    def test_crawl_starts_from_validated_url(self):
        from core.site_dump import crawl_pages

        async def case():
            # mock aiohttp ClientSession → HTML с одной локальной ссылкой и одной внешней.
            html = (
                '<html><a href="/b">B</a>'
                '<a href="https://other.test/x">other</a>'
                '<a href="javascript:1">js</a>'
                "</html>"
            )

            class FakeResp:
                headers = {"Content-Type": "text/html; charset=utf-8"}

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def text(self, errors="strict"):
                    return html

            class FakeSession:
                def __init__(self, *a, **kw):
                    pass

                def get(self, url, **kw):
                    return FakeResp()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

            with mock.patch("core.site_dump.aiohttp.ClientSession", FakeSession):
                pages = await crawl_pages(
                    "https://example.com/",
                    max_pages=5,
                    max_depth=1,
                    same_domain_only=True,
                )

            # Краулер должен взять start + /b, но не other.test (другой домен).
            self.assertIn("https://example.com/", pages)
            self.assertIn("https://example.com/b", pages)
            self.assertNotIn("https://other.test/x", pages)

        run(case())


if __name__ == "__main__":
    unittest.main()
