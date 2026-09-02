class Config:
    APPLICATION_ROOT = "/"
    PREFERRED_URL_SCHEME = "http"

    # Discogs' current Marketplace feed returns 250 listings per cursor page.
    # Large searches are fanned out across non-overlapping listed-date slices.
    RESULTS_PER_PAGE = 250
    URL_MODE_MAX_TOTAL_ITEMS = 500000
    PAGE_FETCH_MAX_WORKERS = 40
    MARKETPLACE_MAX_IN_FLIGHT_REQUESTS = 40
    MARKETPLACE_PARALLEL_THRESHOLD = 10000
    MARKETPLACE_TIME_SLICE_DAYS = 7
    MARKETPLACE_TARGET_ITEMS_PER_SLICE = 2000
    MARKETPLACE_DENSE_ID_SLICES = 40
    MARKETPLACE_TIME_SLICES_PER_WORKER = 4
    MAX_CONCURRENT_MARKETPLACE_TASKS = 1

    REQUEST_TIMEOUT_SECONDS = 30
    REQUEST_RETRIES = 6
    RETRY_BACKOFF_SECONDS = 1.5
    CURL_IMPERSONATE = "chrome"

    DISCOGS_API_BASE = "https://api.discogs.com"
    DISCOGS_WEB_BASE = "https://www.discogs.com"
    DISCOGS_API_USER_AGENT = (
        "DiscogsAdvancedSorter/2.0 "
        "+https://github.com/ae0j/discogs-advanced-sorter"
    )
