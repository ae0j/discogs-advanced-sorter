import concurrent.futures
import csv
import html
import queue
import re

import cloudscraper
from selectolax.parser import HTMLParser

from config import Config

TASKS_STATUS = {}


def save_uuid_to_file(unique_id):
    with open("data/uuids.txt", "a") as file:
        file.write(unique_id + "\n")


def is_valid_uuid(unique_id):
    with open("data/uuids.txt", "r") as file:
        lines = file.readlines()
        return unique_id + "\n" in lines


def worker(q, form_data, results_queue, year=0, count=0):
    while not q.empty():
        try:
            page_number = q.get_nowait()
            records = scrap_and_process(
                form_data, start_page=page_number, year=year, count=count
            )
            results_queue.put(records)
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

            if total_items <= 10000:
                records = run_task(form_data, app_instance)
                all_records.extend(records)
            elif total_items <= 20000:
                records = run_task(form_data, app_instance)
                all_records.extend(records)
                records = run_task(form_data, app_instance, 0, total_items)
                all_records.extend(records)
            else:
                year_data = get_years(form_data)

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    futures = []

                    for year, count in year_data:
                        if count <= 10000:
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


def run_task(form_data, app_instance, year=0, count=0):
    print("run_task start")
    with app_instance.app_context():
        all_records_task = []
        try:
            total_pages = get_threads(form_data, 1, year)
            task_queue = queue.Queue()
            results_queue = queue.Queue()

            for page_number in range(1, total_pages + 1):
                task_queue.put(page_number)

            with concurrent.futures.ThreadPoolExecutor(max_workers=40) as executor:
                for _ in range(100):
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
        response = scraper.get(
            Config.DISCOGS_URL.format(
                seller, "", "", "", 1
            ),  # Empty strings for vinyls, genre, and style
            headers=Config.headers_agent,
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
        # Debug print to see what parameters we're working with
        print(
            f"Processing with params: vinyls={form_data['vinyls']}, genre={form_data.get('genre', '')}, style={form_data.get('style', '')}, page={start_page}"
        )

        if year == 0 and count == 0:
            url = Config.DISCOGS_URL.format(
                form_data["user_input"],
                form_data["vinyls"],
                form_data.get("genre", ""),
                form_data.get("style", ""),
                start_page,
            )
            print(f"Scraping page: {url}")
            response = scraper.get(url, headers=Config.headers_agent)

        elif year == 0 and count != 0:
            url = Config.DISCOGS_URL_ASC.format(
                form_data["user_input"],
                form_data["vinyls"],
                form_data.get("genre", ""),
                form_data.get("style", ""),
                start_page,
            )
            print(f"Scraping LARGE page: {url}")
            response = scraper.get(url, headers=Config.headers_agent)

        elif year != 0:
            if count <= 10000:
                url = Config.DISCOGS_URL_YEAR_PAGE.format(
                    form_data["user_input"],
                    form_data["vinyls"],
                    form_data.get("genre", ""),
                    form_data.get("style", ""),
                    year,
                    start_page,
                )
                print(f"Scraping YEAR: {url}")
                response = scraper.get(url, headers=Config.headers_agent)
            else:
                url = Config.DISCOGS_URL_YEAR_ASC_PAGE.format(
                    form_data["user_input"],
                    form_data["vinyls"],
                    form_data.get("genre", ""),
                    form_data.get("style", ""),
                    year,
                    start_page,
                )
                print(f"Scraping YEAR OVER 10 000: {url}")
                response = scraper.get(url, headers=Config.headers_agent)

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


def calculate_pages(value):
    x = int(value / 250)
    if value % 250 > 0:
        x += 1
    x = min(x, 40)
    return x


def get_years(form_data):
    print("get_years start")
    scraper = cloudscraper.create_scraper()
    response = scraper.get(
        Config.DISCOGS_URL_YEAR_LIST.format(
            form_data["user_input"],
            form_data["vinyls"],
            form_data.get("genre", ""),
            form_data.get("style", ""),
        ),
        headers=Config.headers_agent,
    )

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
    if year == 0:
        response = scraper.get(
            Config.DISCOGS_URL.format(
                form_data["user_input"],
                form_data["vinyls"],
                form_data.get("genre", ""),
                form_data.get("style", ""),
                start_page,
            ),
            headers=Config.headers_agent,
        )
    elif year != 0:
        response = scraper.get(
            Config.DISCOGS_URL_YEAR_PAGE.format(
                form_data["user_input"],
                form_data["vinyls"],
                form_data.get("genre", ""),
                form_data.get("style", ""),
                year,
                start_page,
            ),
            headers=Config.headers_agent,
        )

    html = HTMLParser(response.text)
    pagination_total = html.css(".pagination.top .pagination_total")
    total_items = 0

    for node in pagination_total:
        total_items = int(node.text().split("of")[-1].strip().replace(",", ""))
    print("get_threads end")
    return calculate_pages(total_items)


def get_items(form_data, start_page=1):
    print("get_items start")
    scraper = cloudscraper.create_scraper()
    response = scraper.get(
        Config.DISCOGS_URL.format(
            form_data["user_input"],
            form_data["vinyls"],
            form_data.get("genre", ""),
            form_data.get("style", ""),
            start_page,
        ),
        headers=Config.headers_agent,
    )

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
