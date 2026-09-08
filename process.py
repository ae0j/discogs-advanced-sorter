import base64
import concurrent.futures
import csv
import html
import json
import math
import os
import random
import re
import threading
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

from config import Config
from curl_compat import load_curl_requests


curl_requests = load_curl_requests()


TASKS_STATUS = {}

SUPPORTED_DISCOGS_HOSTS = {"discogs.com", "www.discogs.com"}
SUPPORTED_MARKETPLACE_PATHS = {"/sell/list", "/shop/list"}
RETRYABLE_STATUS_CODES = {403, 408, 425, 429, 500, 502, 503, 504, 520, 522, 524}

MARKETPLACE_SEARCH_HASH = (
    "f0f3f63df448343350caf9d10f0dbf6f9810997bc436e01bcad03f95f8e4a624"
)
STYLES_HASH = (
    "4c514376b334ff2363ebf0c1b137de12d315ea16f6d492d20e46c89e7116843d"
)
RELEASE_DETAILS_HASH = (
    "ee9d48441023ebd1e6ca169e550581b45de216e88b40711acb6617e49e1bb0cb"
)
RELEASE_DETAILS_QUERY = """query Releases($discogsIds: [Int], $first: Int) {
  releases(discogsIds: $discogsIds) {
    inWantlistCount
    inCollectionCount
    images(first: $first) {
      edges {
        node {
          thumbnail {
            webpUrl
          }
        }
      }
    }
    ratings(first: 1) {
      averageRating
    }
    discogsId
  }
}"""

_LEGACY_FILTER_KEY_MAP = {
    "format": "formatName",
    "format_desc": "formatDescription",
    "condition": "mediaCondition",
    "sleeve_condition": "sleeveCondition",
    "ships_from": "shipsFrom",
    "price_min": "priceMin",
    "price_max": "priceMax",
    "year_min": "yearRangeLow",
    "year_max": "yearRangeHigh",
    "artist_id": "artist",
    "release_id": "release",
    "label_id": "label",
    "master_id": "master",
}

_IGNORED_URL_KEYS = {
    "after",
    "attempt",
    "header_type",
    "layout",
    "limit",
    "listing_type",
    "more",
    "page",
    "size",
    "sort",
    "sortorder",
}

_SUPPORTED_FILTER_KEYS = {
    "allowsOffers",
    "artist",
    "currency",
    "formatDescription",
    "formatName",
    "genre",
    "goodDeals",
    "hideGenericSleeves",
    "hideSleevelessMedia",
    "label",
    "master",
    "mediaCondition",
    "price",
    "priceMax",
    "priceMin",
    "q",
    "release",
    "seller",
    "shipsFrom",
    "shipsFromRegions",
    "showUnavailable",
    "sleeveCondition",
    "style",
    "styleId",
    "year",
    "yearRangeHigh",
    "yearRangeLow",
}

_ARRAY_AND_FILTERS = {
    "formatName": "formatNameAnd",
    "formatDescription": "formatDescriptionAnd",
    "genre": "genreAnd",
    "styleId": "styleIdAnd",
}

_STRING_ARRAY_FILTERS = {
    "currency": "currency",
    "formatDescription": "formatDescription",
    "formatName": "formatName",
    "genre": "genre",
    "mediaCondition": "mediaCondition",
    "shipsFrom": "shipsFrom",
    # Discogs uses a plural URL parameter but a singular GraphQL field.
    "shipsFromRegions": "shipsFromRegion",
    "sleeveCondition": "sleeveCondition",
}

_INTEGER_ARRAY_FILTERS = {"artist", "label", "master", "release", "seller"}

_BOOLEAN_FILTERS = {
    "allowsOffers": "allowsOffers",
    "goodDeals": "isDeal",
    "hideGenericSleeves": "hideGenericSleeves",
    "hideSleevelessMedia": "hideSleevelessMedia",
    "showUnavailable": "showUnavailable",
}

_CONDITION_ABBREVIATIONS = {
    "MINT": "M",
    "NEAR_MINT": "NM or M-",
    "VERY_GOOD_PLUS": "VG+",
    "VERY_GOOD": "VG",
    "GOOD_PLUS": "G+",
    "GOOD": "G",
    "FAIR": "F",
    "POOR": "P",
    "NOT_GRADED": "Not Graded",
    "GENERIC": "Generic",
    "NO_COVER": "No Cover",
}

_thread_local = threading.local()
_styles_lock = threading.Lock()
_style_ids_by_name = None
_request_gate = threading.BoundedSemaphore(
    max(1, int(Config.MARKETPLACE_MAX_IN_FLIGHT_REQUESTS))
)
_task_gate = threading.BoundedSemaphore(
    max(1, int(Config.MAX_CONCURRENT_MARKETPLACE_TASKS))
)


class DiscogsError(RuntimeError):
    """A failure that is safe and useful to show to the user."""


class DiscogsTemporaryError(DiscogsError):
    pass


class DiscogsNotFoundError(DiscogsError):
    pass


class DiscogsNoResultsError(DiscogsError):
    pass


class DiscogsUnsupportedUrlError(DiscogsError, ValueError):
    pass


class DiscogsProtocolError(DiscogsError):
    pass


def format_count(value):
    return f"{int(value):,}"


def save_uuid_to_file(unique_id):
    os.makedirs("data", exist_ok=True)
    with open("data/uuids.txt", "a", encoding="utf-8") as file:
        file.write(unique_id + "\n")


def is_valid_uuid(unique_id):
    try:
        with open("data/uuids.txt", "r", encoding="utf-8") as file:
            return unique_id + "\n" in file.readlines()
    except FileNotFoundError:
        return False


def normalize_seller_input(raw_seller):
    seller = unicodedata.normalize("NFKC", str(raw_seller or ""))
    seller = re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", seller).strip()
    if not seller:
        raise DiscogsNotFoundError("Please provide a Discogs seller name.")

    if seller.startswith(("http://", "https://")):
        parsed = urlparse(seller)
        if (parsed.hostname or "").lower() not in SUPPORTED_DISCOGS_HOSTS:
            raise DiscogsNotFoundError(
                "Only Discogs seller names or profile URLs are supported."
            )
        match = re.fullmatch(r"/seller/([^/]+)/profile/?", parsed.path)
        if not match:
            raise DiscogsNotFoundError(
                "Paste only a seller name here, or use the filtered URL field."
            )
        seller = unquote(match.group(1)).strip()

    if not seller or "/" in seller or len(seller) > 100:
        raise DiscogsNotFoundError(
            "That does not look like a valid Discogs seller name."
        )
    return seller


def _normalize_legacy_query(query_params):
    normalized = []
    for key, value in query_params:
        lower_key = key.lower()
        if lower_key in {"more", "header_type", "listing_type", "attempt"}:
            continue
        if lower_key in {"limit", "page"}:
            normalized.append((lower_key, value))
            continue
        if lower_key == "sort":
            sort_name, _, sort_direction = value.partition(",")
            normalized.append(
                ("sort", "date-listed" if sort_name == "listed" else sort_name)
            )
            if sort_direction:
                normalized.append(
                    (
                        "sortOrder",
                        "ascending" if sort_direction == "asc" else "descending",
                    )
                )
            continue
        if lower_key == "year":
            normalized.extend(
                [("yearRangeLow", value), ("yearRangeHigh", value)]
            )
            continue
        normalized.append((_LEGACY_FILTER_KEY_MAP.get(lower_key, key), value))
    return normalized


def parse_and_validate_sell_list_url(raw_url):
    if not raw_url:
        raise DiscogsUnsupportedUrlError("Please provide a Discogs filtered URL.")

    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise DiscogsUnsupportedUrlError(
            "Use a complete Discogs URL beginning with https://."
        )
    if (parsed.hostname or "").lower() not in SUPPORTED_DISCOGS_HOSTS:
        raise DiscogsUnsupportedUrlError("Only discogs.com URLs are supported.")

    path = parsed.path.rstrip("/")
    profile_match = re.fullmatch(r"/seller/([^/]+)/profile", path)
    if path not in SUPPORTED_MARKETPLACE_PATHS and profile_match is None:
        raise DiscogsUnsupportedUrlError(
            "Use a Discogs /shop/list/, legacy /sell/list, or seller profile URL."
        )

    query_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"page", "after"}
    ]
    if profile_match is not None:
        seller_name = normalize_seller_input(unquote(profile_match.group(1)))
        query_params = [
            (key, value) for key, value in query_params if key.lower() != "seller"
        ]
        query_params.insert(0, ("seller", seller_name))
        path = "/sell/list"

    if path == "/sell/list":
        query_params = _normalize_legacy_query(query_params)

    marketplace_url = urlunparse(
        (
            "https",
            "www.discogs.com",
            "/shop/list/",
            "",
            urlencode(query_params, doseq=True),
            "",
        )
    )
    return {
        "marketplace_url": marketplace_url,
        "base_query_params": query_params,
    }


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _slugged_entity_id(value):
    """Return the numeric prefix used by Discogs entity-filter slugs."""
    match = re.match(r"^(\d+)(?:-|$)", str(value or "").strip())
    return int(match.group(1)) if match else None


def _is_true(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _condition_abbreviation(value):
    if not value:
        return ""
    value = str(value)
    if value in _CONDITION_ABBREVIATIONS:
        return _CONDITION_ABBREVIATIONS[value]
    match = re.search(r"\(([^()]*)\)\s*$", value)
    return match.group(1) if match else value.replace("_", " ").title()


def _session():
    session = getattr(_thread_local, "discogs_session", None)
    if session is None:
        session = curl_requests.Session(impersonate=Config.CURL_IMPERSONATE)
        session.headers.update(
            {
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": Config.DISCOGS_API_USER_AGENT,
            }
        )
        _thread_local.discogs_session = session
    return session


def _response_detail(response):
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    message = payload.get("detail") or payload.get("message")
    return f": {message}" if message else ""


def _request_json(method, url, *, params=None, json_body=None, headers=None, context):
    last_error = None
    attempts = max(1, int(Config.REQUEST_RETRIES))

    for attempt in range(1, attempts + 1):
        response = None
        try:
            with _request_gate:
                response = _session().request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=float(Config.REQUEST_TIMEOUT_SECONDS),
                )
        except Exception as exc:
            last_error = exc
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except Exception as exc:
                    last_error = exc
            elif response.status_code == 404:
                if context == "seller lookup":
                    raise DiscogsNotFoundError(
                        "This Discogs seller does not exist. "
                        "Check the seller name and try again."
                    )
                raise DiscogsProtocolError(
                    f"Discogs no longer exposes {context} at the expected address. "
                    "Please update the sorter."
                )
            elif response.status_code not in RETRYABLE_STATUS_CODES:
                raise DiscogsProtocolError(
                    f"Discogs rejected {context} (HTTP {response.status_code})"
                    f"{_response_detail(response)}."
                )
            else:
                last_error = RuntimeError(
                    f"HTTP {response.status_code}{_response_detail(response)}"
                )
                print(
                    f"Discogs {context}: HTTP {response.status_code}; "
                    f"retry {attempt}/{attempts}"
                )

        if attempt < attempts:
            retry_after = 0.0
            if response is not None:
                try:
                    retry_after = float(response.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0.0
            backoff = float(Config.RETRY_BACKOFF_SECONDS) * (2 ** (attempt - 1))
            delay = max(retry_after, backoff)
            time.sleep(delay + random.uniform(0.0, 0.75))

    raise DiscogsTemporaryError(
        "Discogs temporarily blocked or rate-limited a Marketplace request. "
        "Please wait a moment and press search again."
    ) from last_error


def _graphql_headers(referer):
    return {
        "Accept": "application/graphql-response+json,application/json;q=0.9",
        "Content-Type": "application/json",
        "Origin": Config.DISCOGS_WEB_BASE,
        "Referer": referer,
    }


def _graphql_post(body, referer, context):
    attempts = max(1, int(Config.REQUEST_RETRIES))
    for attempt in range(1, attempts + 1):
        payload = _request_json(
            "POST",
            f"{Config.DISCOGS_WEB_BASE}/graphql",
            json_body=body,
            headers=_graphql_headers(referer),
            context=context,
        )
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if not errors:
            return payload

        messages = [str(error.get("message") or "") for error in errors]
        combined = " ".join(messages).casefold()
        transient = any(
            marker in combined
            for marker in (
                "subgraph error",
                "internal server error",
                "service unavailable",
                "temporarily unavailable",
                "timeout",
            )
        )
        if transient:
            if attempt < attempts:
                print(
                    f"Discogs {context}: transient GraphQL error; "
                    f"retry {attempt}/{attempts}"
                )
                backoff = float(Config.RETRY_BACKOFF_SECONDS) * (2 ** (attempt - 1))
                time.sleep(backoff + random.uniform(0.0, 0.75))
                continue
            raise DiscogsTemporaryError(
                "Discogs temporarily failed a Marketplace request. "
                "Please press search again."
            )

        message = messages[0] or "GraphQL query failed"
        raise DiscogsProtocolError(f"Discogs Marketplace error: {message}.")

    raise DiscogsTemporaryError(
        "Discogs temporarily failed a Marketplace request. Please press search again."
    )


def _persisted_query(operation_name, variables, sha256_hash, referer):
    body = {
        "operationName": operation_name,
        "variables": variables,
        "extensions": {
            "clientLibrary": {"name": "@apollo/client", "version": "4.2.9"},
            "persistedQuery": {"version": 1, "sha256Hash": sha256_hash},
        },
    }
    return _graphql_post(body, referer, f"{operation_name} query")


def _resolve_seller_id(username):
    username = normalize_seller_input(username)
    payload = _request_json(
        "GET",
        f"{Config.DISCOGS_API_BASE}/users/{quote(username, safe='')}",
        headers={"Accept": "application/json"},
        context="seller lookup",
    )
    seller_id = _safe_int(payload.get("id")) if isinstance(payload, dict) else 0
    if not seller_id:
        raise DiscogsNotFoundError(
            "This Discogs seller does not exist. Check the seller name and try again."
        )
    return seller_id


def _get_style_ids_by_name(referer):
    global _style_ids_by_name
    if _style_ids_by_name is not None:
        return _style_ids_by_name

    with _styles_lock:
        if _style_ids_by_name is not None:
            return _style_ids_by_name
        payload = _persisted_query("Styles", {}, STYLES_HASH, referer)
        styles = (payload.get("data") or {}).get("styles") or []
        mapping = defaultdict(list)
        for style in styles:
            name = str(style.get("name") or "").strip()
            style_id = style.get("discogsId")
            if name and style_id is not None:
                mapping[name.casefold()].append(str(style_id))
        if not mapping:
            raise DiscogsProtocolError(
                "Discogs changed its style data unexpectedly. Please update the sorter."
            )
        _style_ids_by_name = dict(mapping)
    return _style_ids_by_name


def _parse_price_range(value):
    text = str(value or "").strip()
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return None, None
    if text.startswith("-") or text.lower().startswith("under"):
        return None, round(float(numbers[0]) - 0.01, 2)
    if (
        text.endswith("-")
        or text.lower().endswith("-up")
        or "and up" in text.lower()
        or text.endswith("+")
    ):
        return float(numbers[0]), None
    if len(numbers) == 1:
        value = float(numbers[0])
        return value, value
    return float(numbers[0]), round(float(numbers[1]) + 0.99, 2)


def _year_bounds(values):
    years = []
    for value in values:
        text = str(value).strip()
        decade = re.fullmatch(r"(\d{4})s", text)
        if decade:
            start = int(decade.group(1))
            years.extend([start, start + 9])
        elif re.fullmatch(r"\d{1,4}", text):
            years.append(int(text))
    if not years:
        return None, None
    return min(years), max(years)


def _group_url_params(raw_url):
    parsed = urlparse(raw_url)
    grouped = defaultdict(list)
    unknown = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in _IGNORED_URL_KEYS:
            continue
        if key not in _SUPPORTED_FILTER_KEYS:
            unknown.append(key)
            continue
        if value != "":
            grouped[key].append(value)
    if unknown:
        names = ", ".join(sorted(set(unknown)))
        raise DiscogsUnsupportedUrlError(
            f"This Discogs URL uses an unsupported filter: {names}."
        )
    return grouped


def marketplace_query_from_url(raw_url):
    grouped = _group_url_params(raw_url)
    referer = raw_url
    graph_filter = {}

    for url_key, filter_key in _STRING_ARRAY_FILTERS.items():
        values = grouped.get(url_key)
        if values:
            graph_filter[filter_key] = values
            and_key = _ARRAY_AND_FILTERS.get(filter_key)
            if and_key:
                graph_filter[and_key] = True

    style_ids = list(grouped.get("styleId") or [])
    style_names = grouped.get("style") or []
    if style_names:
        mapping = _get_style_ids_by_name(referer)
        for style_name in style_names:
            matches = mapping.get(style_name.casefold())
            if not matches:
                raise DiscogsUnsupportedUrlError(
                    f'Discogs does not recognize the style "{style_name}".'
                )
            style_ids.extend(matches)
    if style_ids:
        graph_filter["styleId"] = list(dict.fromkeys(map(str, style_ids)))
        graph_filter["styleIdAnd"] = True

    for key in _INTEGER_ARRAY_FILTERS:
        values = grouped.get(key) or []
        converted = []
        for value in values:
            if key == "seller" and not str(value).isdigit():
                converted.append(_resolve_seller_id(value))
            else:
                entity_id = _slugged_entity_id(value)
                if entity_id is None:
                    raise DiscogsUnsupportedUrlError(
                        f'The {key} filter value "{value}" is not valid.'
                    )
                converted.append(entity_id)
        if converted:
            graph_filter[key] = converted

    low = grouped.get("priceMin", [None])[-1]
    high = grouped.get("priceMax", [None])[-1]
    if grouped.get("price") and low is None and high is None:
        low, high = _parse_price_range(grouped["price"][-1])
    if low not in (None, ""):
        graph_filter["priceRangeLow"] = _safe_float(low)
    if high not in (None, ""):
        graph_filter["priceRangeHigh"] = _safe_float(high)

    year_low = grouped.get("yearRangeLow", [None])[-1]
    year_high = grouped.get("yearRangeHigh", [None])[-1]
    if grouped.get("year") and year_low is None and year_high is None:
        year_low, year_high = _year_bounds(grouped["year"])
    if year_low not in (None, ""):
        graph_filter["yearRangeLow"] = _safe_int(year_low)
    if year_high not in (None, ""):
        graph_filter["yearRangeHigh"] = _safe_int(year_high)

    for url_key, filter_key in _BOOLEAN_FILTERS.items():
        values = grouped.get(url_key)
        if values and _is_true(values[-1]):
            graph_filter[filter_key] = True

    query = (grouped.get("q") or [None])[-1]
    return {"filter": graph_filter, "query": query, "referer": referer}


def marketplace_query_for_seller(username):
    seller_id = _resolve_seller_id(username)
    referer = f"{Config.DISCOGS_WEB_BASE}/shop/list/?seller={seller_id}"
    return {"filter": {"seller": [seller_id]}, "query": None, "referer": referer}


def _marketplace_search(search_query, *, first=250, after=None, direction="DESCENDING"):
    variables = {
        "first": int(first),
        "sort": {"dimension": "LISTED_DATE", "direction": direction},
    }
    if search_query.get("filter"):
        variables["filter"] = search_query["filter"]
    if search_query.get("query"):
        variables["query"] = search_query["query"]
    if after:
        variables["after"] = after

    payload = _persisted_query(
        "MarketplaceSearch",
        variables,
        MARKETPLACE_SEARCH_HASH,
        search_query["referer"],
    )
    search = (payload.get("data") or {}).get("marketplaceSearch")
    if not isinstance(search, dict):
        raise DiscogsProtocolError(
            "Discogs changed its Marketplace response unexpectedly. Please update the sorter."
        )
    return search


def _release_details(release_ids, referer):
    unique_ids = list(dict.fromkeys(_safe_int(value) for value in release_ids if value))
    if not unique_ids:
        return {}
    payload = {
        "query": RELEASE_DETAILS_QUERY,
        "variables": {"discogsIds": unique_ids, "first": 1},
        "extensions": {
            "persistedQuery": {"version": 1, "sha256Hash": RELEASE_DETAILS_HASH}
        },
    }
    response = _graphql_post(payload, referer, "release community-count query")

    details = {}
    for release in (response.get("data") or {}).get("releases") or []:
        if not release or release.get("discogsId") is None:
            continue
        details[_safe_int(release["discogsId"])] = {
            "want": _safe_int(release.get("inWantlistCount")),
            "have": _safe_int(release.get("inCollectionCount")),
        }
    return details


def _calculate_scores(want, have, price):
    want = max(0, _safe_int(want))
    have = max(0, _safe_int(have))
    price = max(0.0, _safe_float(price))
    desire_gap = want - have
    rarity_score = round(desire_gap / (have + 1), 5)
    hot_buy = round(
        ((want + (100 * rarity_score)) / (price + 1)) * (1 + (1 / (have + 1))),
        5,
    )
    return hot_buy, rarity_score, desire_gap


def marketplace_node_to_record(node, detail):
    release = node.get("release") or {}
    artists = [
        artist.get("displayName")
        for artist in (release.get("primaryArtists") or [])
        if artist.get("displayName")
    ]
    format_summary = release.get("formatSummary") or {}
    format_parts = [format_summary.get("primaryFormat")]
    format_parts.extend(format_summary.get("recordingTypes") or [])
    format_value = ", ".join(str(value) for value in format_parts if value)
    price = node.get("price") or {}
    price_value = _safe_float(price.get("amount"))
    currency = price.get("currency") or ""
    want = detail.get("want", 0)
    have = detail.get("have", 0)
    hot_buy, rarity_score, desire_gap = _calculate_scores(want, have, price_value)
    listing_id = node.get("id")
    return {
        "hot_buy": hot_buy,
        "rarity_score": rarity_score,
        "desire_gap": desire_gap,
        "have": have,
        "want": want,
        "artist": html.escape(", ".join(artists) or "Unknown Artist"),
        "title": html.escape(str(release.get("title") or "Untitled")),
        "format": format_value,
        "condition": _condition_abbreviation(node.get("mediaCondition")),
        "price": f"{price_value:,.2f} {currency}".strip(),
        "href": f"/sell/item/{listing_id}",
    }


def _parse_listed_date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError) as exc:
        raise DiscogsProtocolError(
            "Discogs returned a listing with an invalid listed date."
        ) from exc


def _cursor_before(moment):
    return _cursor_for_key((int(moment.timestamp() * 1000), 9223372036854775807))


def _cursor_for_key(key):
    raw = json.dumps([int(key[0]), int(key[1])], separators=(",", ":")).encode(
        "utf-8"
    )
    return base64.b64encode(raw).decode("ascii")


def _cursor_key(cursor):
    try:
        value = json.loads(base64.b64decode(cursor))
        return int(value[0]), int(value[1])
    except (TypeError, ValueError, IndexError, json.JSONDecodeError) as exc:
        raise DiscogsProtocolError(
            "Discogs returned an invalid Marketplace cursor. Please retry."
        ) from exc


def _listing_key(node):
    listed_at = _parse_listed_date(node.get("listedDate"))
    return int(listed_at.timestamp() * 1000), _safe_int(node.get("id"))


def _records_for_nodes(nodes, referer):
    release_ids = [
        (node.get("release") or {}).get("discogsId")
        for node in nodes
        if node.get("release")
    ]
    details = _release_details(release_ids, referer)
    return [
        marketplace_node_to_record(
            node,
            details.get(_safe_int((node.get("release") or {}).get("discogsId")), {}),
        )
        for node in nodes
    ]


def _dense_id_segments(
    lower_key,
    upper_key,
    timestamp_ms,
    current_after=None,
    observed_max_id=None,
):
    min_id = lower_key[1] if lower_key[0] == timestamp_ms else 0
    max_id = upper_key[1] if upper_key[0] == timestamp_ms else None
    if current_after:
        after_key = _cursor_key(current_after)
        if after_key[0] == timestamp_ms:
            max_id = after_key[1] if max_id is None else min(max_id, after_key[1])
    if max_id is None and observed_max_id is not None:
        # The cursor bound is exclusive. Starting one above the largest ID on
        # the detected page covers current IDs of any width without creating
        # enormous, badly balanced 64-bit ranges.
        max_id = int(observed_max_id) + 1
    if max_id is None:
        max_id = 9223372036854775807

    if max_id <= min_id:
        raise DiscogsProtocolError(
            "Discogs returned an invalid dense Marketplace cursor range."
        )

    requested = max(1, int(Config.MARKETPLACE_DENSE_ID_SLICES))
    step = max(1, math.ceil((max_id - min_id) / requested))
    boundaries = list(range(max_id, min_id, -step))
    if not boundaries or boundaries[-1] != min_id:
        boundaries.append(min_id)

    segments = []
    for high_id, low_id in zip(boundaries, boundaries[1:]):
        segments.append(
            {
                "lower_key": (timestamp_ms, low_id),
                "upper_key": (timestamp_ms, high_id),
                "is_newest": False,
                "detect_dense": False,
            }
        )

    older_upper = (timestamp_ms, min_id)
    if lower_key < older_upper:
        segments.append(
            {
                "lower_key": lower_key,
                "upper_key": older_upper,
                "is_newest": False,
                "detect_dense": True,
            }
        )
    return segments


def _fetch_cursor_segment(
    search_query,
    lower_key,
    upper_key,
    *,
    is_newest=False,
    detect_dense=True,
    page_progress_callback=None,
):
    after = None if is_newest else _cursor_for_key(upper_key)
    records = []
    seen_cursors = set()

    while True:
        search = _marketplace_search(search_query, after=after)
        edges = search.get("edges") or []
        if not edges:
            break
        nodes = [edge.get("node") or {} for edge in edges if edge.get("node")]
        keyed_nodes = [(node, _listing_key(node)) for node in nodes]
        page_info = search.get("pageInfo") or {}

        if (
            detect_dense
            and len(keyed_nodes) >= int(Config.RESULTS_PER_PAGE)
            and page_info.get("hasNextPage")
            and keyed_nodes[0][1][0] == keyed_nodes[-1][1][0]
        ):
            next_cursor = page_info.get("endCursor")
            probe = _marketplace_search(search_query, first=1, after=next_cursor)
            probe_edges = probe.get("edges") or []
            probe_nodes = [
                edge.get("node") or {} for edge in probe_edges if edge.get("node")
            ]
            timestamp_ms = keyed_nodes[0][1][0]
            if probe_nodes and _listing_key(probe_nodes[0])[0] == timestamp_ms:
                print(
                    "Marketplace dense listing timestamp detected; "
                    "splitting it across listing-ID workers"
                )
                return records, _dense_id_segments(
                    lower_key,
                    upper_key,
                    timestamp_ms,
                    current_after=after,
                    observed_max_id=max(key[1] for _, key in keyed_nodes),
                )

        selected = [
            node for node, key in keyed_nodes if lower_key <= key < upper_key
        ]
        if selected:
            page_records = _records_for_nodes(selected, search_query["referer"])
            records.extend(page_records)
            if page_progress_callback:
                page_progress_callback(len(page_records))

        oldest_on_page = keyed_nodes[-1][1] if keyed_nodes else None
        if (
            not page_info.get("hasNextPage")
            or oldest_on_page is None
            or oldest_on_page < lower_key
        ):
            break

        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor in seen_cursors:
            raise DiscogsProtocolError(
                "Discogs stopped advancing Marketplace pagination. Please retry."
            )
        seen_cursors.add(next_cursor)
        after = next_cursor

    return records, []


def _fetch_time_slice(search_query, lower, upper, is_newest_slice=False):
    lower_key = (int(lower.timestamp() * 1000), 0)
    upper_key = (int(upper.timestamp() * 1000), 0)
    records, _ = _fetch_cursor_segment(
        search_query,
        lower_key,
        upper_key,
        is_newest=is_newest_slice,
        detect_dense=False,
    )
    return records


def _time_slices(oldest, newest, total_count):
    lower_bound = oldest - timedelta(milliseconds=1)
    upper_bound = newest + timedelta(milliseconds=1)
    if total_count <= int(Config.MARKETPLACE_PARALLEL_THRESHOLD):
        return [(lower_bound, upper_bound, True)]

    span = upper_bound - lower_bound
    span_seconds = max(span.total_seconds(), 0.001)
    max_step_seconds = max(
        1.0, float(Config.MARKETPLACE_TIME_SLICE_DAYS) * 24 * 60 * 60
    )
    worker_target = max(1, int(Config.PAGE_FETCH_MAX_WORKERS)) * max(
        1, int(Config.MARKETPLACE_TIME_SLICES_PER_WORKER)
    )
    density_target = math.ceil(
        total_count / max(1, int(Config.MARKETPLACE_TARGET_ITEMS_PER_SLICE))
    )
    max_step_target = math.ceil(span_seconds / max_step_seconds)
    target_slices = max(worker_target, density_target, max_step_target)
    step = timedelta(seconds=max(0.001, span_seconds / target_slices))
    slices = []
    upper = upper_bound
    is_newest = True
    while upper > lower_bound:
        lower = max(lower_bound, upper - step)
        slices.append((lower, upper, is_newest))
        upper = lower
        is_newest = False
    return slices


def fetch_marketplace_inventory(search_query, progress_callback=None):
    newest_search = _marketplace_search(search_query, first=1)
    total_count = _safe_int(newest_search.get("totalCount"))
    if total_count <= 0:
        raise DiscogsNoResultsError(
            "This Discogs filter is valid but currently has no records for sale."
        )
    if total_count > int(Config.URL_MODE_MAX_TOTAL_ITEMS):
        raise DiscogsError(
            f"This Discogs list contains {format_count(total_count)} items. "
            f"Narrow it to {format_count(Config.URL_MODE_MAX_TOTAL_ITEMS)} or fewer."
        )
    if progress_callback:
        progress_callback(0, total_count)

    newest_edges = newest_search.get("edges") or []
    oldest_search = _marketplace_search(search_query, first=1, direction="ASCENDING")
    oldest_edges = oldest_search.get("edges") or []
    if not newest_edges or not oldest_edges:
        raise DiscogsProtocolError(
            "Discogs reported listings but did not return the first and last listing."
        )
    newest = _parse_listed_date((newest_edges[0].get("node") or {}).get("listedDate"))
    oldest = _parse_listed_date((oldest_edges[0].get("node") or {}).get("listedDate"))
    slices = _time_slices(oldest, newest, total_count)
    segments = [
        {
            "lower_key": (int(lower.timestamp() * 1000), 0),
            "upper_key": (int(upper.timestamp() * 1000), 0),
            "is_newest": is_newest,
            "detect_dense": True,
        }
        for lower, upper, is_newest in slices
    ]
    workers = min(len(segments), max(1, int(Config.PAGE_FETCH_MAX_WORKERS)))
    print(
        f"Marketplace: {format_count(total_count)} listings, "
        f"{len(slices)} time slice(s), {workers} worker(s)"
    )

    all_records = []
    processed_records = 0
    progress_lock = threading.Lock()

    def page_progress(record_count):
        nonlocal processed_records
        with progress_lock:
            processed_records += record_count
            current = min(processed_records, total_count)
        if progress_callback:
            progress_callback(current, total_count)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _fetch_cursor_segment,
                search_query,
                segment["lower_key"],
                segment["upper_key"],
                is_newest=segment["is_newest"],
                detect_dense=segment["detect_dense"],
                page_progress_callback=page_progress,
            )
            for segment in segments
        }
        completed = 0
        submitted = len(futures)
        while futures:
            done, futures = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                records, split_segments = future.result()
                all_records.extend(records)
                completed += 1
                for segment in split_segments:
                    futures.add(
                        executor.submit(
                            _fetch_cursor_segment,
                            search_query,
                            segment["lower_key"],
                            segment["upper_key"],
                            is_newest=segment["is_newest"],
                            detect_dense=segment["detect_dense"],
                            page_progress_callback=page_progress,
                        )
                    )
                    submitted += 1
                if completed % max(1, submitted // 10) == 0:
                    print(f"Marketplace segments: {completed}/{submitted}")

    unique_records = {}
    for record in all_records:
        unique_records[record["href"]] = record
    records = list(unique_records.values())
    if not records:
        raise DiscogsNoResultsError(
            "This Discogs filter is valid but currently has no records for sale."
        )

    # Live inventories can change while a large scrape is running. A material
    # shortfall indicates a broken cursor boundary and must not be presented as
    # a complete result.
    shortfall = total_count - len(records)
    allowed_drift = max(10, math.ceil(total_count * 0.01))
    if shortfall > allowed_drift:
        raise DiscogsTemporaryError(
            f"Discogs returned only {format_count(len(records))} of about "
            f"{format_count(total_count)} listings. Please press search again."
        )
    print(f"Marketplace complete: {format_count(len(records))} unique listings")
    if progress_callback:
        # The inventory can gain or lose listings during a long scrape. Once all
        # pages are collected, report the exact result count instead of leaving
        # the final UI at values such as 43,678 / 43,677.
        progress_callback(len(records), len(records))
    return records


def save_records_to_csv(records, unique_id):
    if not records:
        raise DiscogsNoResultsError("Discogs returned no records for this search.")
    os.makedirs("data/pages", exist_ok=True)
    file_path = f"data/pages/{unique_id}.csv"
    with open(file_path, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = [
            "hot_buy",
            "rarity_score",
            "desire_gap",
            "have",
            "want",
            "artist",
            "title",
            "format",
            "condition",
            "price",
            "href",
        ]
        writer = csv.DictWriter(csvfile, fieldnames)
        writer.writeheader()
        writer.writerows(records)


def initiate_task(form_data, app_instance, unique_id):
    with app_instance.app_context():
        try:
            with _task_gate:
                task_status = TASKS_STATUS.get(unique_id)
                if task_status is not None:
                    task_status["phase"] = "checking"
                    task_status["updated_at"] = time.time()

                def update_progress(processed, total):
                    task_status = TASKS_STATUS.get(unique_id)
                    if task_status is not None:
                        task_status["processed"] = processed
                        task_status["total"] = total
                        task_status["phase"] = "fetching"
                        task_status["updated_at"] = time.time()

                if form_data.get("mode") == "url":
                    search_query = marketplace_query_from_url(
                        form_data["marketplace_url"]
                    )
                else:
                    search_query = marketplace_query_for_seller(
                        normalize_seller_input(form_data.get("user_input"))
                    )
                records = fetch_marketplace_inventory(
                    search_query, progress_callback=update_progress
                )
                TASKS_STATUS[unique_id]["phase"] = "finalizing"
                TASKS_STATUS[unique_id]["updated_at"] = time.time()
                save_records_to_csv(records, unique_id)
                TASKS_STATUS[unique_id]["phase"] = "complete"
                TASKS_STATUS[unique_id]["updated_at"] = time.time()
                TASKS_STATUS[unique_id]["completed"] = True
        except DiscogsError as exc:
            print(f"Discogs task failed: {exc}")
            TASKS_STATUS[unique_id]["error"] = str(exc)
            TASKS_STATUS[unique_id]["phase"] = "error"
            TASKS_STATUS[unique_id]["updated_at"] = time.time()
            TASKS_STATUS[unique_id]["completed"] = True
        except Exception as exc:
            print(f"Unexpected Discogs task failure: {exc}")
            TASKS_STATUS[unique_id]["error"] = (
                "The sorter hit an unexpected error while processing Discogs data. "
                "Please retry; if it keeps happening, update the app."
            )
            TASKS_STATUS[unique_id]["phase"] = "error"
            TASKS_STATUS[unique_id]["updated_at"] = time.time()
            TASKS_STATUS[unique_id]["completed"] = True
