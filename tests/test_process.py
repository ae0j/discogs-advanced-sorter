import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import process
from process import (
    DiscogsNotFoundError,
    DiscogsTemporaryError,
    DiscogsUnsupportedUrlError,
    _cursor_before,
    _cursor_for_key,
    _dense_id_segments,
    _fetch_cursor_segment,
    _graphql_post,
    _persisted_query,
    _request_json,
    _time_slices,
    fetch_marketplace_inventory,
    marketplace_node_to_record,
    marketplace_query_from_url,
    normalize_seller_input,
    parse_and_validate_sell_list_url,
)


class UrlParsingTests(unittest.TestCase):
    def test_current_shop_url_is_supported_and_page_is_removed(self):
        result = parse_and_validate_sell_list_url(
            "https://www.discogs.com/shop/list/?seller=2372731&"
            "formatName=Vinyl&size=250&page=4"
        )
        parsed = urlparse(result["marketplace_url"])
        self.assertEqual(parsed.path, "/shop/list/")
        self.assertEqual(
            parse_qs(parsed.query),
            {"seller": ["2372731"], "formatName": ["Vinyl"], "size": ["250"]},
        )

    def test_legacy_url_is_mapped_to_current_marketplace_parameters(self):
        result = parse_and_validate_sell_list_url(
            "https://www.discogs.com/sell/list?format=Vinyl&genre=Electronic&"
            "condition=Near+Mint+%28NM+or+M-%29&year=1995&"
            "sort=listed%2Cdesc&limit=250&page=2"
        )
        query = parse_qs(urlparse(result["marketplace_url"]).query)
        self.assertEqual(query["formatName"], ["Vinyl"])
        self.assertEqual(query["mediaCondition"], ["Near Mint (NM or M-)"])
        self.assertEqual(query["yearRangeLow"], ["1995"])
        self.assertEqual(query["yearRangeHigh"], ["1995"])
        self.assertEqual(query["sort"], ["date-listed"])
        self.assertEqual(query["sortOrder"], ["descending"])
        self.assertNotIn("page", query)

    def test_profile_url_becomes_current_seller_filter(self):
        result = parse_and_validate_sell_list_url(
            "https://www.discogs.com/seller/koreadisco/profile?limit=250"
        )
        self.assertEqual(
            parse_qs(urlparse(result["marketplace_url"]).query)["seller"],
            ["koreadisco"],
        )

    def test_rejects_non_discogs_and_unrelated_discogs_urls(self):
        with self.assertRaises(DiscogsUnsupportedUrlError):
            parse_and_validate_sell_list_url("https://example.com/shop/list/")
        with self.assertRaises(DiscogsUnsupportedUrlError):
            parse_and_validate_sell_list_url("https://www.discogs.com/release/1")

    @patch("process._get_style_ids_by_name", return_value={"ambient": ["11"]})
    @patch("process._resolve_seller_id", return_value=2372731)
    def test_current_and_legacy_filters_become_graphql_filter(self, _seller, _styles):
        parsed = parse_and_validate_sell_list_url(
            "https://www.discogs.com/sell/list?format=Vinyl&genre=Electronic&"
            "style=Ambient&seller=koreadisco&year=1995"
        )
        query = marketplace_query_from_url(parsed["marketplace_url"])
        self.assertEqual(query["filter"]["seller"], [2372731])
        self.assertEqual(query["filter"]["styleId"], ["11"])
        self.assertEqual(query["filter"]["yearRangeLow"], 1995)
        self.assertTrue(query["filter"]["formatNameAnd"])
        self.assertTrue(query["filter"]["genreAnd"])

    def test_unknown_filter_is_not_silently_ignored(self):
        with self.assertRaises(DiscogsUnsupportedUrlError):
            marketplace_query_from_url(
                "https://www.discogs.com/shop/list/?newDiscogsFilter=value"
            )

    def test_current_price_bucket_syntax_matches_discogs_boundaries(self):
        between = marketplace_query_from_url(
            "https://www.discogs.com/shop/list/?price=5-25"
        )["filter"]
        under = marketplace_query_from_url(
            "https://www.discogs.com/shop/list/?price=under-5"
        )["filter"]
        and_up = marketplace_query_from_url(
            "https://www.discogs.com/shop/list/?price=1001-up"
        )["filter"]
        self.assertEqual(between["priceRangeLow"], 5.0)
        self.assertEqual(between["priceRangeHigh"], 25.99)
        self.assertEqual(under["priceRangeHigh"], 4.99)
        self.assertEqual(and_up["priceRangeLow"], 1001.0)
        self.assertNotIn("priceRangeHigh", and_up)

    def test_slugged_entity_filters_use_their_numeric_prefixes(self):
        graph_filter = marketplace_query_from_url(
            "https://www.discogs.com/shop/list/?"
            "release=6023515-Example-Release&artist=42-Example-Artist&"
            "label=123-Example-Label&master=987-Example-Master"
        )["filter"]
        self.assertEqual(graph_filter["release"], [6023515])
        self.assertEqual(graph_filter["artist"], [42])
        self.assertEqual(graph_filter["label"], [123])
        self.assertEqual(graph_filter["master"], [987])

    def test_entity_filter_rejects_non_slugged_non_numeric_values(self):
        with self.assertRaises(DiscogsUnsupportedUrlError):
            marketplace_query_from_url(
                "https://www.discogs.com/shop/list/?release=6023515invalid"
            )

    def test_region_url_filter_uses_singular_graphql_field(self):
        graph_filter = marketplace_query_from_url(
            "https://www.discogs.com/shop/list/?shipsFromRegions=Asia"
        )["filter"]
        self.assertEqual(graph_filter["shipsFromRegion"], ["Asia"])
        self.assertNotIn("shipsFromRegions", graph_filter)


class SellerInputTests(unittest.TestCase):
    def test_removes_invisible_copy_paste_characters(self):
        self.assertEqual(normalize_seller_input("\ufeff  koreadisco\u200b "), "koreadisco")

    def test_accepts_profile_url_in_seller_field(self):
        self.assertEqual(
            normalize_seller_input(
                "https://www.discogs.com/seller/koreadisco/profile?sort=listed"
            ),
            "koreadisco",
        )


class TransportTests(unittest.TestCase):
    @patch("process._request_json")
    def test_persisted_graphql_queries_use_post_bodies(self, request_json):
        request_json.return_value = {"data": {"marketplaceSearch": {}}}
        _persisted_query(
            "MarketplaceSearch",
            {"first": 1},
            "abc123",
            "https://www.discogs.com/shop/list/",
        )
        args, kwargs = request_json.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(kwargs["json_body"]["variables"], {"first": 1})
        self.assertNotIn("params", kwargs)

    @patch("process.time.sleep", return_value=None)
    @patch("process._request_json")
    def test_transient_graphql_errors_are_retried(self, request_json, _sleep):
        request_json.side_effect = [
            {"errors": [{"message": "Subgraph errors redacted"}]},
            {"data": {"marketplaceSearch": {}}},
        ]
        result = _graphql_post(
            {"operationName": "MarketplaceSearch"},
            "https://www.discogs.com/shop/list/",
            "MarketplaceSearch query",
        )
        self.assertIn("data", result)
        self.assertEqual(request_json.call_count, 2)

    @patch("process.time.sleep", return_value=None)
    @patch("process._session")
    def test_404_is_missing_seller_but_403_is_retryable(self, session_factory, _sleep):
        session = Mock()
        session_factory.return_value = session

        not_found = Mock(status_code=404)
        not_found.json.return_value = {"message": "User not found"}
        session.request.return_value = not_found
        with self.assertRaises(DiscogsNotFoundError):
            _request_json("GET", "https://example.test", context="seller lookup")

        challenged = Mock(status_code=403)
        challenged.json.side_effect = ValueError
        session.request.return_value = challenged
        with self.assertRaises(DiscogsTemporaryError):
            _request_json("GET", "https://example.test", context="Marketplace query")


class ParallelPaginationTests(unittest.TestCase):
    def test_cursor_encodes_timestamp_and_high_listing_id(self):
        moment = datetime(2026, 8, 1, tzinfo=timezone.utc)
        decoded = json.loads(base64.b64decode(_cursor_before(moment)))
        self.assertEqual(decoded[0], 1785542400000)
        self.assertEqual(decoded[1], 9223372036854775807)

    def test_large_search_is_split_into_non_overlapping_parallel_slices(self):
        oldest = datetime(2026, 1, 1, tzinfo=timezone.utc)
        newest = datetime(2026, 2, 1, tzinfo=timezone.utc)
        slices = _time_slices(oldest, newest, 20000)
        self.assertGreaterEqual(len(slices), 160)
        self.assertTrue(slices[0][2])
        self.assertTrue(all(not item[2] for item in slices[1:]))
        for previous, current in zip(slices, slices[1:]):
            self.assertEqual(previous[0], current[1])

    def test_small_search_uses_one_cursor_chain(self):
        oldest = datetime.now(timezone.utc) - timedelta(days=100)
        newest = datetime.now(timezone.utc)
        self.assertEqual(len(_time_slices(oldest, newest, 9999)), 1)

    def test_dense_timestamp_is_split_into_non_overlapping_id_ranges(self):
        timestamp_ms = 1788265108000
        observed_max_id = 4_330_867_839
        lower = (timestamp_ms - 1, 0)
        upper = (timestamp_ms + 1, 0)
        segments = _dense_id_segments(
            lower,
            upper,
            timestamp_ms,
            observed_max_id=observed_max_id,
        )
        dense = [item for item in segments if not item["detect_dense"]]
        self.assertGreaterEqual(len(dense), 40)
        self.assertEqual(
            dense[0]["upper_key"], (timestamp_ms, observed_max_id + 1)
        )
        self.assertEqual(dense[-1]["lower_key"], (timestamp_ms, 0))
        for previous, current in zip(dense, dense[1:]):
            self.assertEqual(previous["lower_key"], current["upper_key"])
        target = (timestamp_ms, observed_max_id)
        containing = [
            item
            for item in dense
            if item["lower_key"] <= target < item["upper_key"]
        ]
        self.assertEqual(len(containing), 1)

    @patch("process._dense_id_segments", return_value=[{"dense": True}])
    @patch("process._marketplace_search")
    @patch.object(process.Config, "RESULTS_PER_PAGE", 2)
    def test_dense_detection_passes_the_highest_observed_listing_id(
        self, marketplace_search, dense_segments
    ):
        listed_date = "2026-09-02T12:00:00Z"
        marketplace_search.side_effect = [
            {
                "edges": [
                    {"node": {"id": "4330867839", "listedDate": listed_date}},
                    {"node": {"id": "4330867000", "listedDate": listed_date}},
                ],
                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
            },
            {
                "edges": [
                    {"node": {"id": "4330866999", "listedDate": listed_date}}
                ]
            },
        ]
        timestamp_ms = int(
            datetime.fromisoformat(listed_date.replace("Z", "+00:00")).timestamp()
            * 1000
        )

        records, segments = _fetch_cursor_segment(
            {"referer": "https://www.discogs.com/shop/list/"},
            (timestamp_ms - 1, 0),
            (timestamp_ms + 1, 0),
            detect_dense=True,
        )

        self.assertEqual(records, [])
        self.assertEqual(segments, [{"dense": True}])
        self.assertEqual(
            dense_segments.call_args.kwargs["observed_max_id"], 4_330_867_839
        )

    def test_dense_cursor_above_32_bits_remains_the_upper_bound(self):
        timestamp_ms = 1788265108000
        after_id = 4_500_000_000
        target_id = 4_330_867_839
        segments = _dense_id_segments(
            (timestamp_ms - 1, 0),
            (timestamp_ms + 1, 0),
            timestamp_ms,
            current_after=_cursor_for_key((timestamp_ms, after_id)),
            observed_max_id=target_id,
        )
        dense = [item for item in segments if not item["detect_dense"]]
        self.assertEqual(dense[0]["upper_key"], (timestamp_ms, after_id))
        target = (timestamp_ms, target_id)
        self.assertEqual(
            sum(
                item["lower_key"] <= target < item["upper_key"]
                for item in dense
            ),
            1,
        )

    @patch("process._fetch_cursor_segment")
    @patch("process._marketplace_search")
    def test_final_progress_uses_exact_result_count_when_inventory_changes(
        self, marketplace_search, fetch_segment
    ):
        newest = {
            "totalCount": 1,
            "edges": [{"node": {"listedDate": "2026-09-02T12:00:00Z"}}],
        }
        oldest = {
            "totalCount": 1,
            "edges": [{"node": {"listedDate": "2026-09-01T12:00:00Z"}}],
        }
        marketplace_search.side_effect = [newest, oldest]
        fetch_segment.return_value = (
            [{"href": "/sell/item/1"}, {"href": "/sell/item/2"}],
            [],
        )
        progress = []

        records = fetch_marketplace_inventory(
            {"filter": {}}, progress_callback=lambda done, total: progress.append((done, total))
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(progress[-1], (2, 2))


class RecordTransformationTests(unittest.TestCase):
    def test_marketplace_record_keeps_community_counts_and_scores(self):
        record = marketplace_node_to_record(
            {
                "id": "123",
                "mediaCondition": "NEAR_MINT",
                "price": {"amount": 20, "currency": "USD"},
                "release": {
                    "title": "Title",
                    "primaryArtists": [{"displayName": "Artist"}],
                    "formatSummary": {
                        "primaryFormat": "Vinyl",
                        "recordingTypes": ["LP"],
                    },
                },
            },
            {"want": 48, "have": 7},
        )
        self.assertEqual(record["want"], 48)
        self.assertEqual(record["have"], 7)
        self.assertEqual(record["desire_gap"], 41)
        self.assertEqual(record["condition"], "NM or M-")
        self.assertEqual(record["href"], "/sell/item/123")
        self.assertGreater(record["hot_buy"], 0)


if __name__ == "__main__":
    unittest.main()
