#!/usr/bin/env python3
"""Download Tsumego Hero problems as SGF files, one directory per set."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://tsumego.com"
DEFAULT_SETS_INDEX = Path("data/sets.tsv")
DEFAULT_SETS_DIR = Path("data/sets")
DEFAULT_PROBLEMS_DIR = Path("data/problems")
DEFAULT_DELAY = 1.5
MINIMUM_DELAY = 1.0
USER_AGENT = "tsumego-villain/0.1 (polite public problem archiver)"
MAX_ATTEMPTS = 4

PROBLEM_PATH_RE = re.compile(r"^/(?P<page_id>\d+)$")
BLOB_RE = re.compile(
    r"new\s+Blob\s*\(\s*\[\s*(?P<value>\"(?:[^\"\\]|\\.)*\")\s*\]",
    re.DOTALL,
)


@dataclass(frozen=True)
class SetInfo:
    id: int
    slug: str
    name: str
    problem_count: int


@dataclass(frozen=True)
class ProblemRef:
    position: int
    page_id: int
    tsumego_id: int

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.page_id}"


class PoliteClient:
    """Sequential HTTP client with a delay, jitter, and conservative retries."""

    def __init__(self, delay: float, timeout: float) -> None:
        if delay < MINIMUM_DELAY:
            raise ValueError(
                f"delay must be at least {MINIMUM_DELAY:g} second to protect the server"
            )
        self.delay = delay
        self.timeout = timeout
        self.last_request_at: float | None = None
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.session.close()

    def get(self, url: str) -> str:
        for attempt in range(MAX_ATTEMPTS):
            self._wait_for_turn()
            self.last_request_at = time.monotonic()

            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise
                # The rate limiter still applies after this exponential backoff.
                time.sleep(2**attempt)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 == MAX_ATTEMPTS:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After", "")
                backoff = int(retry_after) if retry_after.isdigit() else 2**attempt
                time.sleep(backoff)
                continue

            # Other 4xx responses are permanent and should not be retried.
            response.raise_for_status()
            return response.text

        raise AssertionError("retry loop terminated unexpectedly")

    def _wait_for_turn(self) -> None:
        if self.last_request_at is None:
            return
        elapsed = time.monotonic() - self.last_request_at
        wait = self.delay - elapsed
        if wait > 0:
            time.sleep(wait)
        # Small jitter avoids synchronized request patterns if multiple runs exist.
        time.sleep(random.uniform(0.0, 0.2))


def read_sets(path: Path) -> list[SetInfo]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, dialect="excel-tab")
        required = {"id", "slug", "name", "problem_count"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"{path} is missing columns: {', '.join(sorted(missing))}; "
                "run scripts/scrape_sets.py first"
            )
        sets = [
            SetInfo(
                id=int(row["id"]),
                slug=row["slug"],
                name=row["name"],
                problem_count=int(row["problem_count"]),
            )
            for row in reader
        ]

    if not sets:
        raise ValueError(f"{path} contains no sets")
    if len({item.id for item in sets}) != len(sets):
        raise ValueError(f"{path} contains duplicate set IDs")
    if len({item.slug for item in sets}) != len(sets):
        raise ValueError(f"{path} contains duplicate set slugs")
    return sets


def parse_problem_refs(html: str, expected_count: int) -> list[ProblemRef]:
    soup = BeautifulSoup(html, "html.parser")
    problems: list[ProblemRef] = []

    for link in soup.select(".set-view-main a[data-tsumego-id]"):
        href = link.get("href")
        match = PROBLEM_PATH_RE.fullmatch(href) if isinstance(href, str) else None
        number = link.select_one(".problem-nav__number")
        tsumego_id = link.get("data-tsumego-id")
        if match is None or number is None or not str(tsumego_id).isdigit():
            raise ValueError("encountered an invalid problem link on the set page")
        problems.append(
            ProblemRef(
                position=int(number.get_text(strip=True)),
                page_id=int(match.group("page_id")),
                tsumego_id=int(str(tsumego_id)),
            )
        )

    if len(problems) != expected_count:
        raise ValueError(f"expected {expected_count} problems, found {len(problems)}")
    expected_positions = list(range(1, expected_count + 1))
    if [problem.position for problem in problems] != expected_positions:
        raise ValueError("problem positions are missing, duplicated, or out of order")
    return problems


def parse_sgf(html: str) -> str:
    for match in BLOB_RE.finditer(html):
        value = json.loads(match.group("value"))
        if isinstance(value, str):
            sgf = value.strip()
            if not sgf.startswith("(;") or "GM[1]" not in sgf[:100]:
                continue
            if not sgf.endswith(")"):
                raise ValueError("SGF Blob appears to be truncated")
            return sgf + "\n"
    raise ValueError("could not find an SGF Blob on the problem page")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def ensure_relative_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    relative_target = os.path.relpath(target.absolute(), start=link.parent.absolute())
    if link.is_symlink() and os.readlink(link) == relative_target:
        return

    temporary = link.with_suffix(link.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(relative_target)
    temporary.replace(link)


def sgf_filename(position: int, problem_count: int) -> str:
    width = max(3, len(str(problem_count)))
    return f"{position:0{width}d}.sgf"


def write_problem_index(
    set_dir: Path, problems: list[ProblemRef], problem_count: int
) -> None:
    path = set_dir / "index.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tsv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, dialect="excel-tab", lineterminator="\n")
        writer.writerow(["position", "filename", "page_id", "tsumego_id", "url"])
        for problem in problems:
            writer.writerow(
                [
                    problem.position,
                    sgf_filename(problem.position, problem_count),
                    problem.page_id,
                    problem.tsumego_id,
                    problem.url,
                ]
            )
    temporary.replace(path)


def scrape_set(
    set_info: SetInfo,
    client: PoliteClient,
    sets_dir: Path,
    problems_dir: Path,
    refreshed: set[int],
    force: bool,
) -> tuple[int, int]:
    print(f"[{set_info.id}] {set_info.name}: reading set index", flush=True)
    html = client.get(f"{BASE_URL}/sets/view/{set_info.id}")
    problems = parse_problem_refs(html, set_info.problem_count)
    set_dir = sets_dir / set_info.slug
    write_problem_index(set_dir, problems, set_info.problem_count)

    downloaded = reused = 0
    for problem in problems:
        link = set_dir / sgf_filename(problem.position, set_info.problem_count)
        canonical = problems_dir / f"{problem.tsumego_id}.sgf"
        should_download = force and problem.tsumego_id not in refreshed

        if should_download or not canonical.is_file():
            problem_html = client.get(problem.url)
            try:
                sgf = parse_sgf(problem_html)
            except ValueError as error:
                raise ValueError(f"{problem.url}: {error}") from error
            atomic_write_text(canonical, sgf)
            refreshed.add(problem.tsumego_id)
            downloaded += 1
        else:
            reused += 1

        ensure_relative_symlink(link, canonical)
        print(
            f"  {problem.position:>{len(str(set_info.problem_count))}}/"
            f"{set_info.problem_count} {link.name} -> {os.readlink(link)}",
            flush=True,
        )

    return downloaded, reused


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("set_id", type=int, nargs="?", help="set ID from data/sets.tsv")
    target.add_argument("--all", action="store_true", help="download every indexed set")
    parser.add_argument(
        "--sets-index",
        type=Path,
        default=DEFAULT_SETS_INDEX,
        help=f"set index path (default: {DEFAULT_SETS_INDEX})",
    )
    parser.add_argument(
        "--sets-dir",
        type=Path,
        default=DEFAULT_SETS_DIR,
        help=f"set symlink directory (default: {DEFAULT_SETS_DIR})",
    )
    parser.add_argument(
        "--problems-dir",
        type=Path,
        default=DEFAULT_PROBLEMS_DIR,
        help=f"canonical SGF directory (default: {DEFAULT_PROBLEMS_DIR})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"minimum delay between requests, at least 1 second (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="HTTP timeout in seconds (default: 30)"
    )
    parser.add_argument(
        "--force", action="store_true", help="replace SGFs that have already been downloaded"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        all_sets = read_sets(args.sets_index)
        if args.all:
            selected_sets = all_sets
        else:
            selected_sets = [item for item in all_sets if item.id == args.set_id]
            if not selected_sets:
                raise ValueError(f"set ID {args.set_id} is not in {args.sets_index}")

        refreshed: set[int] = set()
        totals = [0, 0]
        with PoliteClient(args.delay, args.timeout) as client:
            for set_info in selected_sets:
                counts = scrape_set(
                    set_info,
                    client,
                    args.sets_dir,
                    args.problems_dir,
                    refreshed,
                    args.force,
                )
                totals = [total + count for total, count in zip(totals, counts)]
    except (OSError, requests.RequestException, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"Done: {totals[0]} downloaded, {totals[1]} reused from the problem store"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
