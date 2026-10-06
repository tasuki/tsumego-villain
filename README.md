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

## Problems

Download one set by its ID from `data/sets.tsv`:

```sh
mise exec -- python scripts/scrape_problems.py 122
```

Or download every indexed set:

```sh
mise exec -- python scripts/scrape_problems.py --all
```

Requests are sequential and start at least 1.5 seconds apart, with jitter and
exponential retry backoff. Existing files are skipped, so interrupted runs are
safe to resume. The delay cannot be configured below one second. If an
individual problem still returns a server error after retries, the scraper asks
before skipping it. Every additional skip requires confirmation, while a set
index failure stops immediately; this prevents a global outage from causing an
unattended stream of failed requests. Other errors stop with a nonzero exit
status. Rerunning resumes from the files already downloaded.

Each SGF is stored once under its internal tsumego ID. Numbered entries in a
set are relative symbolic links to those canonical files:

```text
data/problems/559.sgf
data/sets/gokyo-shumyo-i/001.sgf -> ../../problems/559.sgf
data/sets/gokyo-shumyo-i/index.tsv
```

This avoids committing duplicate SGFs when one problem belongs to multiple
sets. The SGF contains the board setup and complete answer tree, so it is
sufficient to render and solve the problem. It does not contain Tsumego Hero's
public page ID, internal tsumego ID, source URL, or position within the set. The
per-set `index.tsv` preserves that metadata and maps it to each symlink.
