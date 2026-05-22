from __future__ import annotations

import unittest

from main_computer.tor_web_search import (
    DEFAULT_DDG_ONION_URL,
    TorOnlySearchError,
    parse_duckduckgo_html_results,
    parse_tor_proxy,
    resolve_duckduckgo_onion_url,
    resolve_tor_proxy,
)


class TorWebSearchConfigTests(unittest.TestCase):
    def test_parse_tor_proxy_requires_socks5h_loopback(self) -> None:
        proxy = parse_tor_proxy("socks5h://127.0.0.1:9150")

        self.assertEqual(proxy.host, "127.0.0.1")
        self.assertEqual(proxy.port, 9150)

    def test_parse_tor_proxy_rejects_plain_socks5(self) -> None:
        with self.assertRaises(TorOnlySearchError):
            parse_tor_proxy("socks5://127.0.0.1:9150")

    def test_parse_tor_proxy_rejects_non_loopback(self) -> None:
        with self.assertRaises(TorOnlySearchError):
            parse_tor_proxy("socks5h://192.0.2.10:9050")

    def test_resolve_tor_proxy_fails_closed_without_config(self) -> None:
        with self.assertRaises(TorOnlySearchError):
            resolve_tor_proxy(env={})

    def test_duckduckgo_url_must_be_https_onion(self) -> None:
        self.assertEqual(resolve_duckduckgo_onion_url(DEFAULT_DDG_ONION_URL), DEFAULT_DDG_ONION_URL)

        with self.assertRaises(TorOnlySearchError):
            resolve_duckduckgo_onion_url("https://duckduckgo.com")

        with self.assertRaises(TorOnlySearchError):
            resolve_duckduckgo_onion_url("http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion")


class DuckDuckGoHtmlParserTests(unittest.TestCase):
    def test_parse_results_extracts_real_target_from_uddg(self) -> None:
        body = """
        <div class="result">
          <a rel="nofollow" class="result__a"
             href="/l/?kh=-1&amp;uddg=https%3A%2F%2Fexample.com%2Fpage">
             Example &amp; Result
          </a>
          <a class="result__snippet" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">
             This is <b>the</b> snippet.
          </a>
        </div>
        """

        results = parse_duckduckgo_html_results(body, DEFAULT_DDG_ONION_URL, max_results=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Example & Result")
        self.assertEqual(results[0].url, "https://example.com/page")
        self.assertEqual(results[0].content, "This is the snippet.")

    def test_parse_results_deduplicates_urls_and_respects_limit(self) -> None:
        body = """
        <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fone">One</a>
        <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fone">One Duplicate</a>
        <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Ftwo">Two</a>
        """

        results = parse_duckduckgo_html_results(body, DEFAULT_DDG_ONION_URL, max_results=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "One")
        self.assertEqual(results[0].url, "https://example.com/one")


if __name__ == "__main__":
    unittest.main()
