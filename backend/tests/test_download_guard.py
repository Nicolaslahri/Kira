"""HTTP-200 error-body guard for subtitle/artwork downloads (R6)."""
from __future__ import annotations

from kira.download_guard import looks_like_error_page, sniff_image


def test_sniff_image_detects_real_formats():
    assert sniff_image(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "jpeg"
    assert sniff_image(b"\x89PNG\r\n\x1a\n\x00\x00") == "png"
    assert sniff_image(b"GIF89a\x01\x00") == "gif"
    assert sniff_image(b"GIF87a\x01\x00") == "gif"
    assert sniff_image(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff_image(b"BM\x00\x00") == "bmp"


def test_sniff_image_rejects_non_images():
    assert sniff_image(b"") is None
    assert sniff_image(b"<!DOCTYPE html><html><body>404</body></html>") is None
    assert sniff_image(b'{"status_code":404,"status_message":"Not found"}') is None
    assert sniff_image(b"Just some text") is None


def test_error_page_detected_by_content_type():
    assert looks_like_error_page(b"whatever", "text/html; charset=utf-8") is True
    assert looks_like_error_page(b"whatever", "application/json") is True
    assert looks_like_error_page(b"whatever", "application/problem+json") is True


def test_error_page_detected_by_body():
    assert looks_like_error_page(b"<!DOCTYPE html><html>...", "") is True
    assert looks_like_error_page(b"   \n  <html><head>", "") is True
    assert looks_like_error_page(b"\xef\xbb\xbf<html>", "") is True          # BOM-prefixed
    assert looks_like_error_page(b'{"error":"too many requests"}', "") is True
    assert looks_like_error_page(b'{"status":429}', "application/octet-stream") is True


def test_real_subtitles_are_not_rejected():
    srt = b"1\r\n00:00:01,000 --> 00:00:04,000\r\nHello world\r\n"
    assert looks_like_error_page(srt, "") is False
    assert looks_like_error_page(srt, "application/x-subrip") is False

    ass = b"[Script Info]\nTitle: Example\n[V4+ Styles]\n"
    assert looks_like_error_page(ass, "") is False        # "[" is not error JSON

    vtt = b"WEBVTT\n\n00:01.000 --> 00:04.000\nHi\n"
    assert looks_like_error_page(vtt, "") is False

    microdvd = b"{1}{1}25.000\n{100}{200}Hello\n"           # "{" but not valid JSON
    assert looks_like_error_page(microdvd, "") is False

    assert looks_like_error_page(b"", "") is False
