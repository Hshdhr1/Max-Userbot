"""Тесты для core.site_dump (валидация URL, имя файла, fallback-цепочка)."""

import asyncio
import unittest
from pathlib import Path
from unittest import mock

from core.site_dump import (
    DumpResult,
    RendererUnavailableError,
    SiteDumpError,
    UrlValidationError,
    dump_url,
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


if __name__ == "__main__":
    unittest.main()
