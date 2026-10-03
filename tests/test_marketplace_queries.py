import concurrent.futures
import hashlib
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app as app_module
import process
from marketplace_queries import (
    MAX_CLIENT_ASSETS,
    QueryDiscoveryError,
    client_asset_urls,
    query_definitions_from_bundle,
)


PAGE_URL = "https://www.discogs.com/shop/list"
ASSET_URL = PAGE_URL + "/assets/index-new.js"
PAGE = '<script type="module" src="/shop/list/assets/index-new.js"></script>'
BUNDLE = (Path(__file__).parent / "fixtures/marketplace-client-20261003.js").read_text()
EXPIRED = {
    "errors": [{"message": "PersistedQueryNotFound", "extensions": {
        "code": "PERSISTED_QUERY_NOT_FOUND"
    }}]
}
OLD_HASH = "f0f3f63df448343350caf9d10f0dbf6f9810997bc436e01bcad03f95f8e4a624"


class QueryDiscoveryTests(unittest.TestCase):
    def test_real_published_queries_match_the_registered_browser_hashes(self):
        queries = query_definitions_from_bundle(BUNDLE)
        self.assertEqual(set(queries), {"MarketplaceSearch", "Styles"})
        self.assertEqual(queries["MarketplaceSearch"].sha256_hash,
                         process.MARKETPLACE_SEARCH_HASH)
        self.assertEqual(queries["Styles"].sha256_hash, process.STYLES_HASH)
        for definition in queries.values():
            self.assertEqual(hashlib.sha256(definition.document.encode()).hexdigest(),
                             definition.sha256_hash)
            self.assertIn("__typename", definition.document)

    def test_formatting_and_comments_do_not_change_the_hash(self):
        changed = BUNDLE.replace("query Styles {", "query   Styles {\n# comment\n")
        self.assertEqual(query_definitions_from_bundle(changed),
                         query_definitions_from_bundle(BUNDLE))

    def test_unregistered_documents_are_not_used(self):
        self.assertEqual(query_definitions_from_bundle(
            BUNDLE.replace(process.MARKETPLACE_SEARCH_HASH, "0" * 64)
                  .replace(process.STYLES_HASH, "1" * 64)), {})

    def test_javascript_interpolation_and_other_operations_are_not_evaluated(self):
        bundle = "const x=gql`query Styles { ${doSomething()} }`;"
        bundle += "const y=gql`mutation Styles { deleteListing }`;"
        bundle += "const z=gql`query Unrelated { viewer { username } }`;"
        self.assertEqual(query_definitions_from_bundle(bundle), {})

    def test_ambiguous_registered_operations_are_rejected(self):
        definition = query_definitions_from_bundle(BUNDLE)["Styles"]
        changed = definition.document.replace("    name", "    title: name", 1)
        digest = hashlib.sha256(changed.encode()).hexdigest()
        with self.assertRaises(QueryDiscoveryError):
            query_definitions_from_bundle(BUNDLE + f"const q=gql`{changed}`;`{digest}`;")

    def test_only_same_origin_marketplace_assets_are_selected(self):
        page = PAGE + '''
          <script src="https://evil.test/shop/list/assets/a.js"></script>
          <script src="http://www.discogs.com/shop/list/assets/a.js"></script>
          <script src="https://user@www.discogs.com/shop/list/assets/a.js"></script>
          <script src="/shop/list/assets/../../private.js"></script>
          <script src="/shop/list/assets/%2e%2e/private.js"></script>
          <script src="/shop/list/assets/a.js?redirect=elsewhere"></script>
          <script src="/other/app.js"></script>
          <link rel="modulepreload" href="/shop/list/assets/shared.js">
        ''' + PAGE
        self.assertEqual(client_asset_urls(page, PAGE_URL),
                         [ASSET_URL, PAGE_URL + "/assets/shared.js"])

    def test_missing_or_excessive_assets_fail_closed(self):
        for page in ("<html>Unexpected page</html>", "".join(
            f'<script src="/shop/list/assets/{n}.js"></script>' for n in range(MAX_CLIENT_ASSETS + 1)
        )):
            with self.subTest(page=page), self.assertRaises(QueryDiscoveryError):
                client_asset_urls(page, PAGE_URL)


class ClientAssetTransportTests(unittest.TestCase):
    @patch("process._session")
    def test_client_text_is_read_without_following_redirects(self, session_factory):
        response = Mock(status_code=200, content=PAGE.encode(), text=PAGE)
        session_factory.return_value.request.return_value = response
        self.assertEqual(process._request_client_text(PAGE_URL), PAGE)
        request = session_factory.return_value.request
        self.assertEqual(request.call_args.args, ("GET", PAGE_URL))
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        response.json.assert_not_called()

    @patch("process._session")
    def test_client_asset_redirect_is_rejected(self, session_factory):
        response = Mock(status_code=302, headers={"Location": "https://other.test/"})
        response.json.side_effect = ValueError
        session_factory.return_value.request.return_value = response
        with self.assertRaisesRegex(process.DiscogsProtocolError, "HTTP 302"):
            process._request_client_text(ASSET_URL)
        self.assertEqual(session_factory.return_value.request.call_count, 1)

    @patch("process._session")
    def test_oversized_client_asset_is_rejected(self, session_factory):
        session_factory.return_value.request.return_value = Mock(
            status_code=200, content=b"x" * (4 * 1024 * 1024 + 1)
        )
        with self.assertRaisesRegex(process.DiscogsProtocolError, "unexpectedly large"):
            process._request_client_text(ASSET_URL)

    @patch.object(process.Config, "REQUEST_RETRIES", 2)
    @patch("process.time.sleep")
    @patch("process._session")
    def test_client_assets_reuse_bounded_http_retries(self, session_factory, sleep):
        blocked = Mock(status_code=403, headers={})
        blocked.json.side_effect = ValueError
        session_factory.return_value.request.side_effect = [
            blocked, Mock(status_code=200, content=PAGE.encode(), text=PAGE)
        ]
        self.assertEqual(process._request_client_text(PAGE_URL), PAGE)
        self.assertEqual(session_factory.return_value.request.call_count, 2)
        sleep.assert_called_once()


class PersistedQueryRecoveryTests(unittest.TestCase):
    def setUp(self):
        for mocked in (
            patch.dict(process._persisted_queries, {}, clear=True),
            patch.object(process, "_persisted_refresh_failure", None),
            patch.object(process, "_style_ids_by_name", None),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.queries = query_definitions_from_bundle(BUNDLE)

    @patch("process._request_client_text", side_effect=[PAGE, BUNDLE])
    @patch("process._request_json")
    def test_expired_hash_refreshes_once_and_caches_the_replacement(self, request, text):
        request.side_effect = [EXPIRED, {"data": {}}, {"data": {}}]
        variables = {"first": 250, "after": "cursor", "filter": {"seller": [42]}}
        process._persisted_query("MarketplaceSearch", variables, OLD_HASH, PAGE_URL)
        process._persisted_query("MarketplaceSearch", variables, OLD_HASH, PAGE_URL)
        bodies = [call.kwargs["json_body"] for call in request.call_args_list]
        self.assertEqual(bodies[0]["extensions"]["persistedQuery"]["sha256Hash"], OLD_HASH)
        self.assertNotIn("query", bodies[0])
        self.assertEqual(bodies[1]["query"], self.queries["MarketplaceSearch"].document)
        for body in bodies[1:]:
            self.assertEqual(body["variables"], variables)
            self.assertEqual(body["extensions"]["persistedQuery"]["sha256Hash"],
                             self.queries["MarketplaceSearch"].sha256_hash)
        self.assertNotIn("query", bodies[2])
        self.assertEqual([call.args[0] for call in text.call_args_list], [PAGE_URL, ASSET_URL])

    @patch("process._request_client_text", side_effect=[PAGE, BUNDLE])
    @patch("process._request_json")
    def test_same_hash_cache_miss_resends_the_approved_document(self, request, text):
        request.side_effect = [EXPIRED, {"data": {}}]
        process._persisted_query("MarketplaceSearch", {}, process.MARKETPLACE_SEARCH_HASH, PAGE_URL)
        body = request.call_args.kwargs["json_body"]
        self.assertEqual(body["query"], self.queries["MarketplaceSearch"].document)
        self.assertEqual(hashlib.sha256(body["query"].encode()).hexdigest(),
                         body["extensions"]["persistedQuery"]["sha256Hash"])

    @patch("process._request_client_text", side_effect=[PAGE, BUNDLE])
    @patch("process._request_json")
    def test_styles_recover_when_only_the_error_code_is_provided(self, request, text):
        request.side_effect = [
            {"errors": [{"message": "Query cache miss", "extensions": {
                "code": "PERSISTED_QUERY_NOT_FOUND"}}]},
            {"data": {"styles": [{"discogsId": 11, "name": "Ambient"}]}},
        ]
        self.assertEqual(process._get_style_ids_by_name(PAGE_URL), {"ambient": ["11"]})
        self.assertEqual(request.call_args.kwargs["json_body"]["query"],
                         self.queries["Styles"].document)

    @patch("process._request_client_text", side_effect=[PAGE, BUNDLE])
    @patch("process._request_json")
    def test_message_only_error_recovers_but_retry_is_bounded(self, request, text):
        request.return_value = {"errors": [{"message": "PersistedQueryNotFound"}]}
        with self.assertRaises(process.DiscogsPersistedQueryError):
            process._persisted_query("MarketplaceSearch", {}, OLD_HASH, PAGE_URL)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(text.call_count, 2)

    @patch("process._request_client_text")
    @patch("process._request_json")
    def test_unrelated_graphql_error_does_not_refresh(self, request, text):
        request.return_value = {"errors": [{"message": "Invalid filter"}]}
        with self.assertRaisesRegex(process.DiscogsProtocolError, "Invalid filter"):
            process._persisted_query("MarketplaceSearch", {}, OLD_HASH, PAGE_URL)
        text.assert_not_called()
        self.assertEqual(request.call_count, 1)

    @patch("process._request_client_text", side_effect=[PAGE, BUNDLE])
    @patch("process._request_json")
    def test_concurrent_workers_share_one_refresh(self, request, text):
        barrier = threading.Barrier(8)

        def reply(method, url, **kwargs):
            body = kwargs["json_body"]
            if body["extensions"]["persistedQuery"]["sha256Hash"] == OLD_HASH:
                barrier.wait(timeout=5)
                return EXPIRED
            return {"data": {}}

        request.side_effect = reply
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(process._persisted_query, "MarketplaceSearch",
                                       {}, OLD_HASH, PAGE_URL) for _ in range(8)]
            self.assertTrue(all(f.result(timeout=10) == {"data": {}} for f in futures))
        self.assertEqual(text.call_count, 2)

    @patch("process._request_client_text")
    @patch("process._request_json")
    def test_a_later_frontend_rotation_refreshes_the_cached_definition(self, request, text):
        previous = self.queries["MarketplaceSearch"]
        process._persisted_queries["MarketplaceSearch"] = previous
        document = previous.document.replace("        offerOk\n", "")
        self.assertNotEqual(document, previous.document)
        digest = hashlib.sha256(document.encode()).hexdigest()
        text.side_effect = [PAGE, f"const q=gql`{document}`;const registry=[`{digest}`];"]
        request.side_effect = [EXPIRED, {"data": {}}]
        process._persisted_query("MarketplaceSearch", {}, OLD_HASH, PAGE_URL)
        self.assertEqual(process._persisted_queries["MarketplaceSearch"].sha256_hash, digest)

    @patch("process._request_client_text", side_effect=[PAGE, "no query definitions"])
    @patch("process._request_json", return_value=EXPIRED)
    def test_failed_refresh_is_not_repeated_by_every_worker(self, request, text):
        for _ in range(3):
            with self.assertRaisesRegex(process.DiscogsProtocolError, "could not refresh"):
                process._persisted_query("MarketplaceSearch", {}, OLD_HASH, PAGE_URL)
        self.assertEqual(text.call_count, 2)
        self.assertEqual(process._persisted_queries, {})

    @patch("process.time.monotonic", return_value=100)
    @patch("process._request_client_text")
    def test_failed_refresh_can_recover_after_cooldown(self, text, clock):
        text.side_effect = process.DiscogsTemporaryError("Temporary block")
        with self.assertRaises(process.DiscogsTemporaryError):
            process._refresh_persisted_query("MarketplaceSearch", None)
        clock.return_value = 131
        text.side_effect = [PAGE, BUNDLE]
        self.assertEqual(process._refresh_persisted_query("MarketplaceSearch", None),
                         self.queries["MarketplaceSearch"])

    @patch("process.save_records_to_csv")
    @patch("process._request_client_text", side_effect=[PAGE, BUNDLE])
    @patch("process._request_json")
    def test_both_form_modes_finish_after_a_realistic_query_miss(self, request, text, save):
        node = {"id": "4330867839", "listedDate": "2026-10-02T12:00:00Z",
                "price": {"amount": 4, "currency": "EUR"}, "release": {
                    "discogsId": 6023515, "title": "Example", "primaryArtists": [],
                    "formatSummary": {"primaryFormat": "Vinyl"}}}

        def reply(method, url, **kwargs):
            if method == "GET":
                return {"id": 42}
            body = kwargs["json_body"]
            if body.get("operationName") == "MarketplaceSearch":
                if body["extensions"]["persistedQuery"]["sha256Hash"] == OLD_HASH:
                    return EXPIRED
                return {"data": {"marketplaceSearch": {"totalCount": 1,
                    "edges": [{"node": node}], "pageInfo": {"hasNextPage": False}}}}
            return {"data": {"releases": [{"discogsId": 6023515,
                "inWantlistCount": 7, "inCollectionCount": 3}]}}

        pending_tasks = []

        class InlineTask:
            def __init__(self, target, args, **kwargs):
                self.target, self.args = target, args

            def start(self):
                pending_tasks.append((self.target, self.args))

        request.side_effect = reply
        app_module._in_flight_tasks.clear()
        with (
            patch.object(process, "MARKETPLACE_SEARCH_HASH", OLD_HASH),
            patch.object(app_module, "threading", SimpleNamespace(Thread=InlineTask)),
            app_module.app.test_client() as client,
        ):
            for form in ({"user_input": "koreadisco"}, {"filtered_url":
                         PAGE_URL + "?release=6023515-Example-Release"}):
                process._persisted_queries.clear()
                text.side_effect = [PAGE, BUNDLE]
                result = client.post("/", data=form).get_json()
                self.assertTrue(result["success"])
                target, args = pending_tasks.pop(0)
                target(*args)
                status = client.get("/task_status/" + result["unique_id"]).get_json()
                self.assertTrue(status["completed"])
                self.assertIsNone(status["error"])
                self.assertEqual(status["processed"], 1)
                self.assertEqual(status["phase"], "complete")
        self.assertEqual(save.call_count, 2)
        self.assertEqual(text.call_count, 4)
        self.assertEqual(save.call_args.args[0][0]["want"], 7)
        self.assertEqual(save.call_args.args[0][0]["have"], 3)


if __name__ == "__main__":
    unittest.main()
