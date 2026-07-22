from __future__ import annotations

import io
import time
import zipfile

import pytest

from tradingbot.data.corp_codes import (
    CorpCodeStore,
    extract_corp_code_xml,
    parse_corp_code_xml,
)
from tradingbot.data.fundamentals_panel import MissingApiKeyError

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list>
    <corp_code>00126380</corp_code>
    <corp_name>삼성전자</corp_name>
    <stock_code>005930</stock_code>
    <modify_date>20240101</modify_date>
  </list>
  <list>
    <corp_code>00164779</corp_code>
    <corp_name>SK하이닉스</corp_name>
    <stock_code>000660</stock_code>
    <modify_date>20240101</modify_date>
  </list>
  <list>
    <corp_code>00999999</corp_code>
    <corp_name>비상장회사</corp_name>
    <stock_code> </stock_code>
    <modify_date>20240101</modify_date>
  </list>
</result>
""".encode("utf-8")


def make_zip(xml: bytes = SAMPLE_XML, name: str = "CORPCODE.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, xml)
    return buffer.getvalue()


class TestParseCorpCodeXml:
    def test_maps_listed_companies_by_stock_code(self):
        mapping = parse_corp_code_xml(SAMPLE_XML)
        assert mapping["005930"] == "00126380"
        assert mapping["000660"] == "00164779"

    def test_unlisted_companies_are_dropped(self):
        # A blank stock_code means no ticker — nothing to look up by.
        mapping = parse_corp_code_xml(SAMPLE_XML)
        assert "00999999" not in mapping.values()
        assert len(mapping) == 2

    def test_malformed_xml_raises(self):
        with pytest.raises(ValueError, match="corp code"):
            parse_corp_code_xml(b"not xml at all")


class TestExtractCorpCodeXml:
    def test_reads_the_xml_member_out_of_the_zip(self):
        assert extract_corp_code_xml(make_zip()) == SAMPLE_XML

    def test_finds_the_xml_regardless_of_member_name(self):
        assert extract_corp_code_xml(make_zip(name="other.xml")) == SAMPLE_XML

    def test_zip_without_xml_member_raises(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", b"nope")
        with pytest.raises(ValueError, match="xml"):
            extract_corp_code_xml(buffer.getvalue())

    def test_non_zip_payload_raises(self):
        # DART returns an XML error document instead of a ZIP on a bad key.
        with pytest.raises(ValueError, match="ZIP"):
            extract_corp_code_xml(b"<result><status>013</status></result>")


@pytest.fixture
def store(tmp_path):
    return CorpCodeStore(tmp_path, downloader=lambda: make_zip())


class TestCorpCodeStore:
    def test_downloads_and_caches(self, store, tmp_path):
        mapping = store.load()
        assert mapping["005930"] == "00126380"
        assert (tmp_path / "_listings" / "corp_codes.csv").exists()

    def test_second_load_uses_the_cache(self, tmp_path):
        calls = {"n": 0}

        def counting_downloader():
            calls["n"] += 1
            return make_zip()

        store = CorpCodeStore(tmp_path, downloader=counting_downloader)
        store.load()
        store.load()
        assert calls["n"] == 1

    def test_stale_cache_triggers_a_refresh(self, tmp_path):
        calls = {"n": 0}

        def counting_downloader():
            calls["n"] += 1
            return make_zip()

        store = CorpCodeStore(tmp_path, downloader=counting_downloader)
        store.load()
        cache = tmp_path / "_listings" / "corp_codes.csv"
        stale = time.time() - (400 * 24 * 3600)
        import os

        os.utime(cache, (stale, stale))
        store.load()
        assert calls["n"] == 2

    def test_refresh_failure_falls_back_to_stale_cache(self, tmp_path):
        state = {"fail": False}

        def flaky_downloader():
            if state["fail"]:
                raise RuntimeError("DART unreachable")
            return make_zip()

        store = CorpCodeStore(tmp_path, downloader=flaky_downloader)
        store.load()
        cache = tmp_path / "_listings" / "corp_codes.csv"
        stale = time.time() - (400 * 24 * 3600)
        import os

        os.utime(cache, (stale, stale))
        state["fail"] = True

        # A months-old corp-code map is still overwhelmingly correct; losing it
        # because DART blipped would stop fundamentals collection for no reason.
        assert store.load()["005930"] == "00126380"

    def test_download_failure_without_cache_raises(self, tmp_path):
        def broken():
            raise RuntimeError("DART unreachable")

        store = CorpCodeStore(tmp_path, downloader=broken)
        with pytest.raises(RuntimeError, match="unreachable"):
            store.load()

    def test_fetch_false_never_downloads(self, tmp_path):
        def forbidden():
            raise AssertionError("must not download")

        store = CorpCodeStore(tmp_path, downloader=forbidden)
        assert store.load(fetch=False) == {}

    def test_corp_code_lookup(self, store):
        assert store.corp_code("005930") == "00126380"
        assert store.corp_code("5930") is None

    def test_corp_code_for_returns_only_known_symbols(self, store):
        mapping = store.corp_code_for(["005930", "999999"])
        assert mapping == {"005930": "00126380"}

    def test_missing_api_key_surfaces_when_downloading(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DART_API_KEY", raising=False)
        # Default downloader needs the key; it must fail as a credential problem
        # so the pipeline reports fundamentals as skipped, not failed.
        store = CorpCodeStore(tmp_path)
        with pytest.raises(MissingApiKeyError):
            store.load()
