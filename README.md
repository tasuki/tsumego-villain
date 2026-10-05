# Tsumego Villain

Small, reusable scrapers for the public [Tsumego Hero](https://tsumego.com/) collection.

## Setup

Python is pinned with [mise](https://mise.jdx.dev/), and mise activates the local virtual environment.

```sh
mise install
mise exec -- python -m pip install -r requirements.txt
```

## Collection index

Scrape `/sets` with the maximum collection size (`1000`):

```sh
mise exec -- python scripts/scrape_sets.py
```

The script writes `data/sets.tsv` with one collection per row and these columns:

- `id`
- `slug`
- `name`
- `problem_count`
- `average_difficulty`

TSV is used because the index is flat tabular data: it is human-readable, diff-friendly, and directly supported by spreadsheets and data-analysis tools.

Use `--help` to see options such as a different output path or HTTP timeout.
