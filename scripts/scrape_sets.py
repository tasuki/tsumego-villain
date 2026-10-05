#!/usr/bin/env python3
"""Scrape the public Tsumego Hero collection index into a TSV file."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://tsumego.com"
SETS_URL = f"{BASE_URL}/sets"
COLLECTION_SIZE_URL = f"{SETS_URL}/changeCollectionSize"
DEFAULT_OUTPUT = Path("data/sets.tsv")
USER_AGENT = "tsumego-villain/0.1 (collection index scraper)"

SET_PATH_RE = re.compile(r"^/sets/view/(?P<id>\d+)(?:/\d+)?$")
PROBLEM_COUNT_RE = re.compile(r"^(?P<count>[\d,]+)\s+problems?$")


@dataclass(frozen=True)
class Collection:
    id: int
    name: str
    problem_count: int
    average_difficulty: str


def element_text(card: Tag, selector: str) -> str:
    element = card.select_one(selector)
    if element is None:
        raise ValueError(f"collection card is missing {selector!r}")
    return element.get_text(" ", strip=True)


def parse_collections(html: str) -> list[Collection]:
    soup = BeautifulSoup(html, "html.parser")
    collections: list[Collection] = []

    for card in soup.select("a.set-card__link"):
        href = card.get("href")
        if not isinstance(href, str) or (match := SET_PATH_RE.fullmatch(href)) is None:
            raise ValueError(f"unexpected collection URL: {href!r}")

        count_text = element_text(card, ".set-card__middle-left")
        count_match = PROBLEM_COUNT_RE.fullmatch(count_text)
        if count_match is None:
            raise ValueError(f"unexpected problem count: {count_text!r}")

        collections.append(
            Collection(
                id=int(match.group("id")),
                name=element_text(card, ".set-card__top"),
                problem_count=int(count_match.group("count").replace(",", "")),
                average_difficulty=element_text(card, ".set-card__middle-right"),
            )
        )

    if not collections:
        raise ValueError("no collection cards found")

    duplicate_ids = sorted(
        collection_id
        for collection_id in {collection.id for collection in collections}
        if sum(collection.id == collection_id for collection in collections) > 1
    )
    if duplicate_ids:
        raise ValueError(
            "collections are still split into multiple cards; duplicate IDs: "
            + ", ".join(map(str, duplicate_ids))
        )

    return collections


def fetch_collections(collection_size: int, timeout: float) -> list[Collection]:
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        response = session.post(
            COLLECTION_SIZE_URL,
            data={"collection_size": collection_size},
            timeout=timeout,
        )
        response.raise_for_status()

        expected_url = urljoin(BASE_URL, "/sets")
        if response.url.rstrip("/") != expected_url:
            raise ValueError(f"unexpected redirect target: {response.url!r}")

        soup = BeautifulSoup(response.text, "html.parser")
        size_input = soup.select_one('input[name="collection_size"]')
        actual_size = size_input.get("value") if size_input else None
        if actual_size != str(collection_size):
            raise ValueError(
                f"server did not accept collection size {collection_size}; got {actual_size!r}"
            )

        return parse_collections(response.text)


def write_tsv(collections: list[Collection], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")

    with temporary_output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[field.name for field in fields(Collection)],
            dialect="excel-tab",
            lineterminator="\n",
        )
        writer.writeheader()
        for collection in collections:
            writer.writerow(
                {
                    "id": collection.id,
                    "name": collection.name,
                    "problem_count": collection.problem_count,
                    "average_difficulty": collection.average_difficulty,
                }
            )

    temporary_output.replace(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output TSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--collection-size",
        type=int,
        default=1000,
        choices=range(10, 1001, 10),
        metavar="10..1000",
        help="Tsumego Hero collection size (default: 1000)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds (default: 30)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        collections = fetch_collections(args.collection_size, args.timeout)
        write_tsv(collections, args.output)
    except (OSError, requests.RequestException, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {len(collections)} collections to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
