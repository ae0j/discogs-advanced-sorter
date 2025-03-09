class Config:
    SERVER_NAME = "127.0.0.1:5000"
    APPLICATION_ROOT = "/"
    PREFERRED_URL_SCHEME = "http"
    # Base URLs with placeholders for seller, format/vinyl, genre, style, page, and year
    DISCOGS_URL = "https://www.discogs.com/seller/{}/profile?sort=listed%2Cdesc&limit=250{}{}{}&page={}"
    DISCOGS_URL_ASC = "https://www.discogs.com/seller/{}/profile?sort=listed%2Casc&limit=250{}{}{}&page={}"
    DISCOGS_URL_YEAR_PAGE = "https://www.discogs.com/seller/{}/profile?sort=listed%2Cdesc&limit=250{}{}{}&year={}&page={}"
    DISCOGS_URL_YEAR_ASC_PAGE = "https://www.discogs.com/seller/{}/profile?sort=listed%2Casc&limit=250{}{}{}&year={}&page={}"
    DISCOGS_URL_YEAR_LIST = "https://www.discogs.com/sell/_mp_facets?sort=listed%2Cdesc&limit=250&seller={}{}&header_type=seller&more=year&listing_type=listing&attempt=1"
    HEADERS = {
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-GB,en;q=0.9,cs;q=0.8",
        "Referer": "https://www.google.com/",
        "Sec-Ch-Ua": '"Chromium";v="116", "Not)A;Brand";v="24", "Google Chrome";v="116"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
    }

    headers_agent = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"
    }

    max_workers = 100
