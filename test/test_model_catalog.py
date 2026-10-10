import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proxy"))
import model_catalog as catalog


class FakeOpener:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.pages.pop(0)).encode())


class ModelCatalog(unittest.TestCase):
    def test_endpoints_preserve_prefixes_and_strip_inference_suffix(self):
        cases = [("https://example.com/v1/responses", "responses", "https://example.com/v1/models"),
                 ("https://example.com/api/v1", "openai", "https://example.com/api/v1/models"),
                 ("https://api.anthropic.com", "anthropic", "https://api.anthropic.com/v1/models"),
                 ("https://api.deepseek.com/anthropic", "anthropic", "https://api.deepseek.com/models"),
                 ("http://127.0.0.1:11434/v1", "openai", "http://127.0.0.1:11434/v1/models")]
        for base, protocol, expected in cases:
            self.assertEqual(catalog.models_endpoint(base, protocol), expected)

    def test_unsafe_urls_and_mismatched_protocol_fail(self):
        for base in ("http://example.com", "https://user:key@example.com", "https://example.com?secret=1", "https://example.com/responses"):
            with self.assertRaises(ValueError):
                catalog.models_endpoint(base, "openai")

    def test_chat_and_responses_auth_dedupe_and_sort(self):
        for protocol in ("openai", "responses"):
            opener = FakeOpener([{"data": [{"id": "z"}, {"id": "a", "name": "Alpha"}, {"id": "z"}, {}, {"id": "bad\n"}]}])
            self.assertEqual(catalog.fetch_models("https://example.com/v1", protocol, "test-key", opener), [{"id": "a", "name": "Alpha"}, {"id": "z", "name": "z"}])
            self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer test-key")
            self.assertNotIn("test-key", opener.requests[0].full_url)

    def test_anthropic_pagination_uses_same_origin(self):
        opener = FakeOpener([{"data": [{"id": "b"}], "has_more": True, "last_id": "b", "next_url": "https://evil.com"}, {"data": [{"id": "a", "display_name": "Alpha"}], "has_more": False}])
        self.assertEqual(len(catalog.fetch_models("https://example.com", "anthropic", "test-key", opener)), 2)
        self.assertEqual(opener.requests[0].get_header("X-api-key"), "test-key")
        self.assertEqual(opener.requests[1].full_url, "https://example.com/v1/models?limit=100&after_id=b")

    def test_empty_malformed_and_looping_pagination_are_errors(self):
        for response in ({"data": []}, {"models": []}, {"data": [{"id": "a"}], "has_more": True}):
            with self.assertRaises(ValueError):
                catalog.fetch_models("https://example.com", "anthropic", "test-key", FakeOpener([response]))

    def test_redirects_are_not_followed(self):
        self.assertIsNone(catalog.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.com"))

    def test_deepseek_uses_bearer_for_catalog(self):
        opener = FakeOpener([{"data": [{"id": "deepseek-chat"}]}])
        catalog.fetch_models("https://api.deepseek.com/anthropic", "anthropic", "test-key", opener)
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer test-key")


if __name__ == "__main__":
    unittest.main()
