# Discogs Scraper & Advanced Sorter

## Features

- Retrieves a seller's inventory and displays it as an interactive HTML table.
- Offers sorting options including Want, Have, Desire Gap, Rarity Score, Hot Buy, and more.

## Requirements

- Python 3.10, 3.11, or 3.12

## Quick Start (Recommended)

### macOS

1. Download ZIP from GitHub and unpack it.
2. Open the folder.
3. Double-click `run.command`.
4. Open `http://127.0.0.1:5000`.

### Windows

1. Download ZIP from GitHub and unpack it.
2. Open the folder.
3. Double-click `run.bat`.
4. Open `http://127.0.0.1:5000`.

The scripts create `.venv`, install dependencies, and start the app.

## Manual Start

### macOS/Linux

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ulimit -n 65535
flask --app app run
```

### Windows

```bat
py -3.10 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
flask --app app run
```

## Usage

1. Input the seller name.
2. Optionally enable **Get vinyls only**.
3. Click **Get Inventory**.
4. Browse the resulting table and sort columns.

## Troubleshooting

- `This seller does not exist` with a known seller:
  - Retry a few times. Discogs can return temporary anti-bot responses.
- `Too many open files`:
  - On macOS/Linux, run `ulimit -n 65535` in the same terminal before starting.
  - The `run.command` script already applies a higher limit automatically.

## Limitations

- May not perform optimally with very large seller inventories (exceeding 500,000 records).
- Records without a release year are not currently retrieved.
