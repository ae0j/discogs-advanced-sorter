import concurrent.futures
import csv
import html
import queue
import re
import time
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

import cloudscraper
from selectolax.parser import HTMLParser

from config import Config

try:
    import resource
except ImportError:
    resource = None

TASKS_STATUS = {}
SUPPORTED_DISCOGS_HOSTS = {"discogs.com", "www.discogs.com"}
REMOVED_FILTER_KEYS = {"page", "limit", "sort"}
RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504, 520, 522, 524}


def format_count(value):
    return f"{int(value):,}"


def get_mode_limits(form_data):
    mode = get_request_mode(form_data)
    if mode == "url":
        max_pages_per_segment = int(Config.URL_MODE_MAX_PAGES_PER_SEGMENT)
    else:
        max_pages_per_segment = int(Config.SELLER_MODE_MAX_PAGES_PER_SEGMENT)

    single_pass_limit = max_pages_per_segment * int(Config.RESULTS_PER_PAGE)
    dual_pass_limit = single_pass_limit * 2
    return {
        "mode": mode,
        "max_pages_per_segment": max_pages_per_segment,
        "single_pass_limit": single_pass_limit,
        "dual_pass_limit": dual_pass_limit,
    }


def save_uuid_to_file(unique_id):
    with open("data/uuids.txt", "a") as file:
        file.write(unique_id + "\n")


def is_valid_uuid(unique_id):
    with open("data/uuids.txt", "r") as file:
        lines = file.readlines()
        return unique_id + "\n" in lines


def parse_and_validate_sell_list_url(raw_url):
    if not raw_url:
        raise ValueError("Please provide a Discogs filtered URL")

    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Use valid Discogs URL.")

    if (parsed.hostname or "").lower() not in SUPPORTED_DISCOGS_HOSTS:
        raise ValueError("Only discogs.com URLs are supported")

    path = parsed.path.rstrip("/")
    profile_match = re.fullmatch(r"/seller/([^/]+)/profile", path)
    is_sell_list_url = path == "/sell/list"
    is_profile_url = profile_match is not None
    if not is_sell_list_url and not is_profile_url:
        raise ValueError(
            "Supported URLs: /sell/list or /seller/username/profile on Discogs"
        )

    base_query_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in REMOVED_FILTER_KEYS:
            continue
        if is_profile_url and key.lower() == "seller":
            continue
        base_query_params.append((key, value))

    if is_profile_url:
        seller_name = unquote(profile_match.group(1)).strip()
        if not seller_name:
            raise ValueError("Could not determine seller from Discogs profile URL")
        # Canonicalize profile URLs into /sell/list query mode.
        base_query_params.insert(0, ("seller", seller_name))

    return {"base_query_params": base_query_params}


def build_sell_list_page_url(base_query_params, page, sort, year=None):
    query_params = list(base_query_params)
    has_user_year_filter = any(key.lower() == "year" for key, _ in query_params)
    query_params.extend([("sort", sort), ("limit", "250")])
    if year not in (None, 0, "0") and not has_user_year_filter:
        query_params.append(("year", str(year)))
    query_params.append(("page", str(page)))
    return f"{Config.DISCOGS_SELL_LIST_URL}?{urlencode(query_params, doseq=True)}"


def build_sell_list_year_facets_url(base_query_params):
    query_params = list(base_query_params)
    query_params.extend(
        [
            ("sort", "listed,desc"),
            ("limit", "250"),
            ("more", "year"),
            ("listing_type", "listing"),
            ("attempt", "1"),
        ]
    )
    return f"{Config.DISCOGS_SELL_FACETS_URL}?{urlencode(query_params, doseq=True)}"


def get_request_mode(form_data):
    return form_data.get("mode", "seller")


def build_seller_page_url(form_data, page, sort, year=0):
    if year == 0:
        if sort == "listed,asc":
            return Config.DISCOGS_URL_ASC.format(
                form_data["user_input"],
                form_data["vinyls"],
                form_data.get("genre", ""),
                form_data.get("style", ""),
                page,
            )
        return Config.DISCOGS_URL.format(
            form_data["user_input"],
            form_data["vinyls"],
            form_data.get("genre", ""),
            form_data.get("style", ""),
            page,
        )

    if sort == "listed,asc":
        return Config.DISCOGS_URL_YEAR_ASC_PAGE.format(
            form_data["user_input"],
            form_data["vinyls"],
            form_data.get("genre", ""),
            form_data.get("style", ""),
            year,
            page,
        )
    return Config.DISCOGS_URL_YEAR_PAGE.format(
        form_data["user_input"],
        form_data["vinyls"],
        form_data.get("genre", ""),
        form_data.get("style", ""),
        year,
        page,
    )


def build_marketplace_page_url(form_data, page, sort, year=0):
    if get_request_mode(form_data) == "url":
        return build_sell_list_page_url(
            form_data["url_query_params"], page=page, sort=sort, year=year
        )
    return build_seller_page_url(form_data, page=page, sort=sort, year=year)


def build_year_facets_url(form_data):
    if get_request_mode(form_data) == "url":
        return build_sell_list_year_facets_url(form_data["url_query_params"])
    return Config.DISCOGS_URL_YEAR_LIST.format(
        form_data["user_input"],
        form_data["vinyls"],
        form_data.get("genre", ""),
        form_data.get("style", ""),
    )


def get_open_file_soft_limit():
    if resource is None or not hasattr(resource, "RLIMIT_NOFILE"):
        return None
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        return int(soft_limit)
    except Exception:
        return None


def get_safe_page_worker_count(requested_workers):
    requested_workers = max(int(Config.MIN_WORKERS), int(requested_workers))
    soft_limit = get_open_file_soft_limit()
    if soft_limit is None:
        return requested_workers

    available_fds = max(0, soft_limit - int(Config.FD_RESERVE))
    if available_fds == 0:
        return int(Config.MIN_WORKERS)

    # Keep a conservative fd budget per worker to avoid EMFILE on low-limit machines.
    max_workers_from_fds = max(int(Config.MIN_WORKERS), available_fds // 4)
    return max(int(Config.MIN_WORKERS), min(requested_workers, max_workers_from_fds))


def fetch_with_retries(scraper, url, *, headers=None, context="request"):
    last_error = None
    max_retries = max(1, int(Config.REQUEST_RETRIES))

    for attempt in range(1, max_retries + 1):
        try:
            response = scraper.get(
                url, headers=headers, timeout=Config.REQUEST_TIMEOUT_SECONDS
            )
            if response.status_code == 200:
                return response

            if response.status_code not in RETRYABLE_STATUS_CODES:
                return response

            last_error = RuntimeError(
                f"{context} got retryable status {response.status_code}"
            )
            print(
                f"{context} attempt {attempt}/{max_retries} failed with status {response.status_code}: {url}"
            )
        except Exception as exc:
            last_error = exc
            print(f"{context} attempt {attempt}/{max_retries} failed: {exc} | {url}")

        if attempt < max_retries:
            delay = float(Config.RETRY_BACKOFF_SECONDS) * attempt
            time.sleep(max(0.0, delay))

    if last_error:
        raise RuntimeError(
            f"{context} failed after {max_retries} attempts: {last_error}"
        )
    raise RuntimeError(f"{context} failed after {max_retries} attempts")


def worker(q, form_data, results_queue, year=0, count=0):
    while not q.empty():
        try:
            page_number = q.get_nowait()
            records = scrap_and_process(
                form_data, start_page=page_number, year=year, count=count
            )
            results_queue.put(records or [])
        except queue.Empty:
            break


def threaded_task(user_input, app_instance, year=0, count=0):
    return run_task(user_input, app_instance, year, count)


def initiate_task(form_data, app_instance, unique_id):
    with app_instance.app_context():
        all_records = []
        total_items = 0

        try:
            total_items = get_items(form_data)
            limits = get_mode_limits(form_data)

            if limits["mode"] == "url" and total_items > int(
                Config.URL_MODE_MAX_TOTAL_ITEMS
            ):
                raise ValueError(
                    "URL too broad. Use only Discogs lists with 500,000 items or fewer "
                    "(check the top line: '1 - 25 of X')."
                )

            if total_items <= limits["single_pass_limit"]:
                records = run_task(form_data, app_instance)
                all_records.extend(records)
            elif total_items <= limits["dual_pass_limit"]:
                records = run_task(form_data, app_instance)
                all_records.extend(records)
                records = run_task(form_data, app_instance, 0, total_items)
                all_records.extend(records)
            else:
                year_data = get_years(form_data)
                if not year_data:
                    if get_request_mode(form_data) == "url":
                        raise ValueError(
                            "URL too broad. Use only Discogs lists with 500,000 items or fewer."
                        )
                    raise ValueError(
                        "Large seller inventory could not be split by year. Please retry."
                    )

                if limits["mode"] == "url":
                    oversized_years = [
                        (year, count)
                        for year, count in year_data
                        if count > limits["dual_pass_limit"]
                    ]
                    if oversized_years:
                        raise ValueError(
                            "URL too broad. Use only Discogs lists with 500,000 items or fewer."
                        )

                year_workers = min(
                    max(1, int(Config.YEAR_TASK_MAX_WORKERS)),
                    max(1, len(year_data)),
                )
                print(
                    f"Running year split with {year_workers} concurrent year task(s) across {len(year_data)} buckets"
                )
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=year_workers
                ) as executor:
                    futures = []

                    for year, count in year_data:
                        if count <= limits["single_pass_limit"]:
                            future = executor.submit(
                                threaded_task, form_data, app_instance, year, count
                            )
                            futures.append(future)
                        else:
                            future = executor.submit(
                                threaded_task, form_data, app_instance, year, count
                            )
                            futures.append(future)
                            future = executor.submit(
                                threaded_task, form_data, app_instance, year, count=0
                            )
                            futures.append(future)

                    for future in concurrent.futures.as_completed(futures):
                        records = future.result()
                        all_records.extend(records)

            save_records_to_csv(all_records, unique_id)
            TASKS_STATUS[unique_id]["completed"] = True
            # return jsonify({"unique_id": unique_id})

        except Exception as e:
            print(f"Error in initiate_task function: {e}")
            TASKS_STATUS[unique_id]["error"] = str(e) or "Failed to scrape data"
            TASKS_STATUS[unique_id]["completed"] = True


def run_task(form_data, app_instance, year=0, count=0):
    print("run_task start")
    with app_instance.app_context():
        all_records_task = []
        try:
            total_pages = get_threads(form_data, 1, year)
            if total_pages == 0:
                print("run_task end (no pages)")
                return all_records_task

            page_workers = min(
                total_pages, get_safe_page_worker_count(Config.PAGE_FETCH_MAX_WORKERS)
            )
            print(
                f"run_task year={year} total_pages={total_pages} using {page_workers} page worker(s)"
            )
            task_queue = queue.Queue()
            results_queue = queue.Queue()

            for page_number in range(1, total_pages + 1):
                task_queue.put(page_number)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=page_workers
            ) as executor:
                for _ in range(page_workers):
                    executor.submit(
                        worker,
                        task_queue,
                        form_data,
                        results_queue,
                        year,
                        count,
                    )

            while not results_queue.empty():
                records = results_queue.get()
                all_records_task.extend(records)

        except Exception as e:
            print(f"Error in run_task function: {e}")
        print("run_task end")
        return all_records_task


def verify_seller(seller):
    try:
        print(f"Input: {seller}")
        scraper = cloudscraper.create_scraper()
        response = fetch_with_retries(
            scraper,
            Config.DISCOGS_URL.format(
                seller, "", "", "", 1
            ),  # Empty strings for vinyls, genre, and style
            headers=Config.headers_agent,
            context="verify_seller",
        )
        print(response)
        if response.status_code == 200:
            html = HTMLParser(response.text)
            # Check for items in the marketplace
            records = html.css(".no_marketplace_results")

            if records:
                print(".no_marketplace_results exists!")
                return False
            else:
                print(".no_marketplace_results does not exist!")
                return True
        else:
            print("Wrong status code")
            return False
    except Exception as e:
        print(f"Error in verify_seller function: {e}")
        return False


def verify_filtered_url(base_query_params):
    try:
        scraper = cloudscraper.create_scraper()
        url = build_sell_list_page_url(base_query_params, 1, "listed,desc")
        print(f"Verifying filtered URL: {url}")
        response = fetch_with_retries(
            scraper, url, headers=Config.headers_agent, context="verify_filtered_url"
        )
        print(response)
        if response.status_code != 200:
            print("Wrong status code")
            return False

        html = HTMLParser(response.text)
        records = html.css(".no_marketplace_results")
        if records:
            print(".no_marketplace_results exists!")
            return False

        print(".no_marketplace_results does not exist!")
        return True
    except Exception as e:
        print(f"Error in verify_filtered_url function: {e}")
        return False


def save_records_to_csv(records, unique_id):
    seen_hrefs = set()

    with open(
        f"data/pages/{unique_id}.csv", "w", newline="", encoding="utf-8"
    ) as csvfile:
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
        writer.writeheader()  # writes the headers

        for record in records:
            if record["href"] not in seen_hrefs:
                seen_hrefs.add(record["href"])
                writer.writerow(record)


def scrap_and_process(form_data, start_page=1, year=0, count=0):
    print("scrap_and_process start")
    scraper = cloudscraper.create_scraper()
    try:
        print(
            f"Processing with params: mode={get_request_mode(form_data)}, page={start_page}, year={year}, count={count}"
        )
        limits = get_mode_limits(form_data)
        url = ""
        label = "Scraping page"
        if year == 0 and count == 0:
            url = build_marketplace_page_url(
                form_data, page=start_page, sort="listed,desc"
            )
            label = "Scraping page"

        elif year == 0 and count != 0:
            url = build_marketplace_page_url(
                form_data, page=start_page, sort="listed,asc"
            )
            label = "Scraping LARGE page"

        elif year != 0:
            if count <= limits["single_pass_limit"]:
                url = build_marketplace_page_url(
                    form_data, page=start_page, sort="listed,desc", year=year
                )
                label = "Scraping YEAR"
            else:
                url = build_marketplace_page_url(
                    form_data, page=start_page, sort="listed,asc", year=year
                )
                label = "Scraping YEAR OVER 10 000"

        print(f"{label}: {url}")
        response = fetch_with_retries(
            scraper,
            url,
            headers=Config.headers_agent,
            context=f"scrap_and_process page={start_page} year={year}",
        )
        if response.status_code != 200:
            print(f"Non-200 page response ({response.status_code}) for {url}")
            return []

        htmlParser = HTMLParser(response.text)

        titles = htmlParser.css(".item_description_title")
        hrefs = [node.attributes.get("href") for node in titles]
        prices = htmlParser.css(".item_price .price")
        conditions = htmlParser.css(".item_condition > span:nth-child(3)")
        rows = htmlParser.css("tbody tr")
        weight = 100

        records = []

        for row, title, href, price, condition in zip(
            rows, titles, hrefs, prices, conditions
        ):
            want = row.css_first(".community_summary .want_indicator .community_number")
            have = row.css_first(".community_summary .have_indicator .community_number")

            want_value = int(want.text()) if want else 0
            have_value = int(have.text()) if have else 0
            desire_gap = want_value - have_value

            price_value = price.text()
            price_numeric = float(re.sub(r"[^\d\.]", "", price_value))
            rarity_score = round(desire_gap / (have_value + 1), 5)
            item_condition = re.search(r"\((.*?)\)", condition.text()).group(1)
            hot_buy = round(
                ((want_value + (weight * rarity_score)) / (price_numeric + 1))
                * (1 + (1 / (have_value + 1))),
                5,
            )

            record = {
                "hot_buy": hot_buy,
                "rarity_score": rarity_score,
                "desire_gap": desire_gap,
                "have": have.text() if have else 0,
                "want": want.text() if want else 0,
                "artist": re.sub(r" - .*$", "", title.text()),
                "title": html.escape(
                    re.sub(r"\([^\(]*\)$", "", re.sub(r"^[^-]*- ", "", title.text()))
                ),
                "format": (
                    re.search(r"\(([^()]+)\)\s*$", title.text()).group(1)
                    if re.search(r"\(([^()]+)\)\s*$", title.text())
                    else None
                ),
                "condition": item_condition,
                "price": price.text(),
                "href": href,
            }
            records.append(record)
        print("scrap_and_process end")
        return records
    except Exception as e:
        print(f"Error in scrap_and_process function: {e}")
        return []


def calculate_pages(value, max_pages_per_segment):
    x = int(value / int(Config.RESULTS_PER_PAGE))
    if value % int(Config.RESULTS_PER_PAGE) > 0:
        x += 1
    x = min(x, int(max_pages_per_segment))
    return x


def get_years(form_data):
    print("get_years start")
    scraper = cloudscraper.create_scraper()
    year_facets_url = build_year_facets_url(form_data)
    print(f"Year facets URL: {year_facets_url}")
    response = fetch_with_retries(
        scraper,
        year_facets_url,
        headers=Config.headers_agent,
        context="get_years",
    )
    if response.status_code != 200:
        raise RuntimeError(f"get_years returned status {response.status_code}")

    html = HTMLParser(response.text)

    all_years = [node.text() for node in html.css("a .link_text")]
    all_counts = [
        int(node.text().replace(",", "")) for node in html.css("a .facet_count")
    ]

    if len(all_years) != len(all_counts):
        raise ValueError("Mismatch in the number of years and counts")

    year_data = list(zip(all_years, all_counts))
    print("get_years end")
    return year_data


def get_threads(form_data, start_page=1, year=0):
    print("get_threads start")
    scraper = cloudscraper.create_scraper()
    response = fetch_with_retries(
        scraper,
        build_marketplace_page_url(
            form_data, page=start_page, sort="listed,desc", year=year
        ),
        headers=Config.headers_agent,
        context=f"get_threads year={year}",
    )
    if response.status_code != 200:
        raise RuntimeError(f"get_threads returned status {response.status_code}")

    html = HTMLParser(response.text)
    pagination_total = html.css(".pagination.top .pagination_total")
    total_items = 0

    for node in pagination_total:
        total_items = int(node.text().split("of")[-1].strip().replace(",", ""))
    print("get_threads end")
    limits = get_mode_limits(form_data)
    return calculate_pages(total_items, limits["max_pages_per_segment"])


def get_items(form_data, start_page=1):
    print("get_items start")
    scraper = cloudscraper.create_scraper()
    response = fetch_with_retries(
        scraper,
        build_marketplace_page_url(form_data, page=start_page, sort="listed,desc"),
        headers=Config.headers_agent,
        context="get_items",
    )
    if response.status_code != 200:
        raise RuntimeError(f"get_items returned status {response.status_code}")

    html = HTMLParser(response.text)
    pagination_total = html.css(".pagination.top .pagination_total")

    total_items = 0  # Initialize with default value
    for node in pagination_total:
        try:
            total_items = int(node.text().split("of")[-1].strip().replace(",", ""))
        except (ValueError, IndexError) as e:
            print(f"Error parsing pagination: {e}")
            total_items = 0

    print(f"get_items end - found {total_items} items")
    return total_items
