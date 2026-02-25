# Discogs Scraper & Advanced Sorter

## Features

- Retrieves a seller's inventory and displays it as an interactive HTML table.
- Offers sorting options including Want, Have, Desire Gap, Rarity Score, Hot Buy, and more.

## Requirements

- Python 3.10, 3.11, 3.12, or 3.13

## Quick Start (Recommended)

### macOS

1. Download ZIP from GitHub and unpack it.
2. Open the folder.
3. Double-click `run.command`.
4. Open `http://127.0.0.1:5080`.

### Windows

1. Download ZIP from GitHub and unpack it.
2. Open the folder.
3. Double-click `run.bat`.
4. Open `http://127.0.0.1:5080`.

The scripts create `.venv`, install dependencies, and start the app.

## Manual Start

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ulimit -n 65535
flask --app app run --host 127.0.0.1 --port 5080
```

### Windows

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
flask --app app run --host 127.0.0.1 --port 5080
```

## Usage

1. Input the seller name, or paste a full Discogs `/sell/list` URL or `/seller/username/profile` URL.
2. If using URL mode, use only Discogs lists with `500,000` items or fewer (check the top line: `1 - 25 of X`).
3. Click **Get Inventory**.
4. Browse the resulting table and sort columns.

If both seller and URL are filled, URL mode is used.

## Troubleshooting

- `This seller does not exist` with a known seller:
  - Retry a few times. Discogs can return temporary anti-bot responses.
- `Too many open files`:
  - On macOS/Linux, run `ulimit -n 65535` in the same terminal before starting.
  - The `run.command` script already applies a higher limit automatically.
- Windows says Python is not found / opens Microsoft Store / shows unsupported version:
  - Install Python 3.10, 3.11, 3.12, or 3.13 from `python.org`.
  - Disable `python.exe` and `python3.exe` in **App execution aliases**.
  - Re-run `run.bat`.
- URL mode with very broad filters can fail if Discogs does not expose year facets:
  - Narrow your filters directly on Discogs and retry with the new URL.
- URL mode says the URL is too broad:
  - On Discogs, look at the top counter line (`1 - 25 of X`).
  - Use only lists where `X` is `500,000` or less.
- Browser shows `403` on localhost:
  - Use the exact printed URL from terminal (default is `http://127.0.0.1:5080`).
  - On macOS, port `5000` can be used by system services, so this project defaults to `5080`.

## Limitations

- May not perform optimally with very large seller inventories (exceeding 500,000 records).
- Records without a release year are not currently retrieved.
