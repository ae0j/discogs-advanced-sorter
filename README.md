# Discogs Scraper & Advanced Sorter

## Features

- Retrieves a seller's collection and displays it as an interactive HTML table.
- Offers sorting options, including:
  - Want
  - Have
  - Desire Gap
  - Rarity Score
  - Hot Buy
  - And more...

## Installation

1. `pip install -r requirements.txt`
2. `flask run`
3. Open localhost at `http://127.0.0.1:5000`

## Usage

1. Input the seller's name.
2. Choose **Get vinyls only** if you wish to get only vinyls
3. Click ‘Get Collection’.
4. Browse through the seller's collection using advanced sorting options available.

## Limitations

- May not perform optimally with very large seller collections (exceeding 500,000 records).
- Currently doesn't retrieve records that lack a release year.
