#!/usr/bin/env python3
"""
import_greatlibrary_to_directus.py

Reads the combined GreatLibrary archive JSON or ZIP, extracts blog posts,
rectifies dates, and creates/updates Directus posts by slug.

This script intentionally does NOT store the old source URL in Directus.
The old URL is used only in memory to derive the slug.

Expected Directus `posts` fields:

  status
  title
  slug
  body
  excerpt
  published_on
  read_time_minutes
  is_legacy

For all imported GreatLibrary posts:

  is_legacy = "yes"

Usage:

  python -m pip install requests

  export DIRECTUS_URL="https://directus-johnrraymond.greatlibrary.io"
  export DIRECTUS_TOKEN="paste-token-here"

  python import_greatlibrary_to_directus.py \
    --archive combined_url_text_archive.json \
    --dry-run

  python import_greatlibrary_to_directus.py \
    --archive combined_url_text_archive.json \
    --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests


GREATLIBRARY_HOST = "www.johnrraymond.greatlibrary.io"

# This archive was captured on 2026-04-16.
# Month/day labels like "Apr 3" use this year.
# Relative labels like "3 days ago" use this as the default anchor.
DEFAULT_RELATIVE_ANCHOR = "2026-04-16T17:55:24"

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


@dataclass
class ParsedPost:
    slug: str
    title: str
    body: str
    excerpt: str
    published_on: str
    read_time_minutes: int | None
    status: str
    date_label: str
    date_confidence: str
    is_legacy: str = "yes"
    action: str = "pending"
    directus_id: int | str | None = None
    error: str | None = None


@dataclass
class SkippedRecord:
    url: str
    reason: str


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_anchor(value: str) -> datetime:
    value = value.strip()

    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

        return datetime.combine(date.fromisoformat(value), datetime.min.time())
    except ValueError:
        die(f"Invalid date anchor: {value!r}. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS.")


def load_archive(path: Path) -> tuple[dict[str, Any], datetime | None]:
    """
    Accepts either:

      combined_url_text_archive.json
      combined(1).zip

    The JSON format should be:

      {
        "https://www.johnrraymond.greatlibrary.io/post/...": "extracted text...",
        ...
      }
    """

    if not path.exists():
        die(f"Archive path does not exist: {path}")

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            json_names = [
                name
                for name in zf.namelist()
                if name.lower().endswith(".json") and not name.endswith("/")
            ]

            if not json_names:
                die(f"No JSON file found inside ZIP: {path}")

            json_names.sort(
                key=lambda name: (
                    Path(name).name != "combined_url_text_archive.json",
                    name,
                )
            )

            chosen = json_names[0]
            info = zf.getinfo(chosen)
            archive_dt = datetime(*info.date_time)

            try:
                data = json.loads(zf.read(chosen).decode("utf-8"))
            except Exception as exc:
                die(f"Could not read JSON from ZIP member {chosen!r}: {exc}")

            if not isinstance(data, dict):
                die("Archive JSON must be an object mapping URL to text.")

            print(f"Loaded JSON from ZIP member: {chosen}")
            print(f"Inferred archive timestamp: {archive_dt.isoformat(sep=' ')}")

            return data, archive_dt

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"Could not read JSON file {path}: {exc}")

    if not isinstance(data, dict):
        die("Archive JSON must be an object mapping URL to text.")

    return data, None


def is_greatlibrary_post_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == GREATLIBRARY_HOST and parsed.path.startswith("/post/")


def slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    raw_slug = parsed.path.rstrip("/").split("/")[-1].strip()

    if not raw_slug:
        raise ValueError("missing_slug")

    return unquote(raw_slug)


def is_bad_gateway_text(text: str) -> bool:
    lowered = text.lower().strip()

    return (
        "502 bad gateway" in lowered
        or "503 service unavailable" in lowered
        or ("bad gateway" in lowered and "nginx" in lowered)
    )


def nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_read_time(label: str) -> int | None:
    match = re.search(r"(\d+)\s+min\s+read", label, flags=re.IGNORECASE)

    if not match:
        return None

    return int(match.group(1))


def rectify_date_label(
    label: str,
    *,
    current_year: int,
    relative_anchor: datetime,
) -> tuple[str, str]:
    """
    Examples:

      Nov 24, 2025 -> 2025-11-24
      Apr 3        -> 2026-04-03
      3 days ago   -> 2026-04-13
      9 hours ago  -> 2026-04-16
      1 minute ago -> 2026-04-16
    """

    label = label.strip()

    explicit = re.fullmatch(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{4})",
        label,
    )

    if explicit:
        month = MONTHS[explicit.group(1)]
        day = int(explicit.group(2))
        year = int(explicit.group(3))
        return date(year, month, day).isoformat(), "explicit_year"

    month_day = re.fullmatch(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})",
        label,
    )

    if month_day:
        month = MONTHS[month_day.group(1)]
        day = int(month_day.group(2))
        return date(current_year, month, day).isoformat(), "current_year"

    relative = re.fullmatch(
        r"(\d+)\s+(minute|minutes|hour|hours|day|days)\s+ago",
        label,
        flags=re.IGNORECASE,
    )

    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()

        if unit.startswith("minute"):
            computed = relative_anchor - timedelta(minutes=amount)
        elif unit.startswith("hour"):
            computed = relative_anchor - timedelta(hours=amount)
        elif unit.startswith("day"):
            computed = relative_anchor - timedelta(days=amount)
        else:
            raise ValueError(f"unsupported_relative_unit:{unit}")

        return computed.date().isoformat(), "relative_anchor"

    raise ValueError(f"unrecognized_date_label:{label}")


def strip_trailing_boilerplate(lines: list[str]) -> list[str]:
    """
    Removes repeated end-of-post social/link boilerplate only when it appears
    at the very end of the body.
    """

    def is_boilerplate(line: str) -> bool:
        lowered = line.strip().lower()

        return (
            lowered.startswith("continue the conversation on bluesky:")
            or lowered == "https://bsky.app/profile/johnrraymond.bsky.social"
            or lowered.startswith("join john r raymond on discord at:")
            or lowered.startswith("john r raymond invites you to join him in discord:")
            or lowered.startswith("join john r raymond in discord:")
            or lowered.startswith("because the truth matters: https://discord.gg/")
            or lowered.startswith("https://discord.gg/")
            or lowered == "https://johnrraymondesq.medium.com/"
            or lowered == "https://www.johnrraymond.greatlibrary.io/blog"
        )

    cleaned = list(lines)

    while cleaned and is_boilerplate(cleaned[-1]):
        cleaned.pop()

    return cleaned


def make_excerpt(body: str, max_chars: int = 260) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

    if not paragraphs:
        return ""

    first = re.sub(r"\s+", " ", paragraphs[0]).strip()

    if len(first) <= max_chars:
        return first

    shortened = first[: max_chars - 1].rstrip()

    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]

    return shortened.rstrip(".,;:") + "…"


def parse_one_post(
    url: str,
    text: str,
    *,
    current_year: int,
    relative_anchor: datetime,
    status: str,
    keep_boilerplate: bool,
    allow_empty_body: bool,
) -> ParsedPost:
    if is_bad_gateway_text(text):
        raise ValueError("bad_gateway_502_or_503_skipped")

    slug = slug_from_url(url)
    lines = nonempty_lines(text)

    try:
        search_index = lines.index("Search")
    except ValueError:
        raise ValueError("could_not_find_Search_marker")

    try:
        title = lines[search_index + 1].strip()
        author = lines[search_index + 2].strip()
        date_label = lines[search_index + 3].strip()
        read_time_label = lines[search_index + 4].strip()
    except IndexError:
        raise ValueError("post_header_incomplete")

    if not title:
        raise ValueError("missing_title")

    if author.lower() != "john raymond":
        raise ValueError(f"unexpected_author_line:{author!r}")

    published_on, date_confidence = rectify_date_label(
        date_label,
        current_year=current_year,
        relative_anchor=relative_anchor,
    )

    read_time_minutes = parse_read_time(read_time_label)

    body_start = search_index + 5
    recent_posts_index = None

    for i in range(body_start, len(lines)):
        if lines[i] == "Recent Posts":
            recent_posts_index = i
            break

    if recent_posts_index is None:
        body_lines = lines[body_start:]
    else:
        body_lines = lines[body_start:recent_posts_index]

    if not keep_boilerplate:
        body_lines = strip_trailing_boilerplate(body_lines)

    body = "\n\n".join(line for line in body_lines if line.strip()).strip()

    if not body and not allow_empty_body:
        raise ValueError("missing_body")

    return ParsedPost(
        slug=slug,
        title=title,
        body=body,
        excerpt=make_excerpt(body),
        published_on=published_on,
        read_time_minutes=read_time_minutes,
        status=status,
        date_label=date_label,
        date_confidence=date_confidence,
        is_legacy="yes",
    )


def directus_request(
    method: str,
    url: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    if json_body is not None:
        headers["Content-Type"] = "application/json"

    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"request_failed:{exc}") from exc

    if not response.ok:
        raise RuntimeError(f"http_{response.status_code}:{response.text}")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"expected_json_response:{response.text}") from exc


def get_directus_fields(
    directus_url: str,
    token: str,
    collection: str,
    *,
    timeout: int,
) -> set[str]:
    result = directus_request(
        "GET",
        f"{directus_url}/fields/{collection}",
        token,
        timeout=timeout,
    )

    return {
        item["field"]
        for item in result.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("field"), str)
    }


def find_post_by_slug(
    directus_url: str,
    token: str,
    collection: str,
    slug: str,
    *,
    timeout: int,
) -> dict[str, Any] | None:
    result = directus_request(
        "GET",
        f"{directus_url}/items/{collection}",
        token,
        params={
            "filter[slug][_eq]": slug,
            "limit": 1,
            "fields": "id,slug",
        },
        timeout=timeout,
    )

    rows = result.get("data", [])

    if not rows:
        return None

    return rows[0]


def post_payload(post: ParsedPost) -> dict[str, Any]:
    return {
        "status": post.status,
        "title": post.title,
        "slug": post.slug,
        "body": post.body,
        "excerpt": post.excerpt,
        "published_on": post.published_on,
        "read_time_minutes": post.read_time_minutes,
        "is_legacy": "yes",
    }


def create_post(
    directus_url: str,
    token: str,
    collection: str,
    payload: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    return directus_request(
        "POST",
        f"{directus_url}/items/{collection}",
        token,
        json_body=payload,
        timeout=timeout,
    )


def update_post(
    directus_url: str,
    token: str,
    collection: str,
    item_id: int | str,
    payload: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    return directus_request(
        "PATCH",
        f"{directus_url}/items/{collection}/{item_id}",
        token,
        json_body=payload,
        timeout=timeout,
    )


def write_reports(
    posts: list[ParsedPost],
    skipped: list[SkippedRecord],
    report_json: Path,
    report_csv: Path,
) -> None:
    report_json.write_text(
        json.dumps(
            {
                "summary": {
                    "parsed_posts": len(posts),
                    "skipped_records": len(skipped),
                },
                "posts": [asdict(post) for post in posts],
                "skipped": [asdict(item) for item in skipped],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with report_csv.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "slug",
            "title",
            "published_on",
            "date_label",
            "date_confidence",
            "read_time_minutes",
            "status",
            "is_legacy",
            "action",
            "directus_id",
            "error",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for post in posts:
            row = asdict(post)
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        required=True,
        help="Path to combined_url_text_archive.json or ZIP containing it.",
    )
    parser.add_argument("--collection", default="posts")
    parser.add_argument(
        "--directus-url",
        default=os.environ.get("DIRECTUS_URL", ""),
        help="Directus base URL. Can also use DIRECTUS_URL.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("DIRECTUS_TOKEN", ""),
        help="Directus static token. Can also use DIRECTUS_TOKEN.",
    )
    parser.add_argument(
        "--relative-anchor",
        default="",
        help=(
            "Anchor for labels like '3 days ago'. "
            "Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS. "
            "Defaults to ZIP timestamp if available, otherwise 2026-04-16T17:55:24."
        ),
    )
    parser.add_argument(
        "--current-year",
        type=int,
        default=0,
        help="Year for labels like 'Apr 3'. Defaults to the relative anchor year.",
    )
    parser.add_argument("--status", default="published")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--keep-boilerplate", action="store_true")
    parser.add_argument("--allow-empty-body", action="store_true")
    parser.add_argument("--report-json", default="greatlibrary_import_report.json")
    parser.add_argument("--report-csv", default="greatlibrary_import_report.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")

    args = parser.parse_args()

    if args.dry_run and args.apply:
        die("Use either --dry-run or --apply, not both.")

    if not args.dry_run and not args.apply:
        die("Choose --dry-run first, then --apply when ready.")

    archive_data, inferred_archive_dt = load_archive(Path(args.archive))

    if args.relative_anchor.strip():
        relative_anchor = parse_anchor(args.relative_anchor)
    elif inferred_archive_dt is not None:
        relative_anchor = inferred_archive_dt
    else:
        relative_anchor = parse_anchor(DEFAULT_RELATIVE_ANCHOR)

    current_year = args.current_year or relative_anchor.year

    posts: list[ParsedPost] = []
    skipped: list[SkippedRecord] = []

    for url, text in archive_data.items():
        if not isinstance(url, str) or not isinstance(text, str):
            continue

        if not is_greatlibrary_post_url(url):
            continue

        try:
            post = parse_one_post(
                url,
                text,
                current_year=current_year,
                relative_anchor=relative_anchor,
                status=args.status,
                keep_boilerplate=args.keep_boilerplate,
                allow_empty_body=args.allow_empty_body,
            )
            posts.append(post)
        except Exception as exc:
            skipped.append(SkippedRecord(url=url, reason=str(exc)))

    posts.sort(key=lambda p: (p.published_on, p.slug), reverse=True)

    if args.limit:
        posts = posts[: args.limit]

    print()
    print(f"GreatLibrary posts parsed: {len(posts)}")
    print(f"GreatLibrary records skipped: {len(skipped)}")
    print(f"Relative date anchor: {relative_anchor.isoformat(sep=' ')}")
    print(f"Current year for month/day labels: {current_year}")
    print('Legacy marker: is_legacy = "yes"')

    print()
    print("Date preview:")
    for post in posts[:12]:
        print(f"  {post.published_on}  {post.date_label:<14}  {post.slug}")

    if args.apply:
        directus_url = args.directus_url.strip().rstrip("/")
        token = args.token.strip()

        if not directus_url:
            die("Missing Directus URL. Use --directus-url or DIRECTUS_URL.")

        if not token:
            die("Missing Directus token. Use --token or DIRECTUS_TOKEN.")

        required_fields = {
            "status",
            "title",
            "slug",
            "body",
            "excerpt",
            "published_on",
            "read_time_minutes",
            "is_legacy",
        }

        existing_fields = get_directus_fields(
            directus_url,
            token,
            args.collection,
            timeout=args.timeout,
        )

        missing = sorted(required_fields - existing_fields)

        if missing:
            die(
                f"Directus collection {args.collection!r} is missing fields: "
                + ", ".join(missing)
            )

        for index, post in enumerate(posts, start=1):
            try:
                existing = find_post_by_slug(
                    directus_url,
                    token,
                    args.collection,
                    post.slug,
                    timeout=args.timeout,
                )

                payload = post_payload(post)

                if existing:
                    post.action = "update"
                    post.directus_id = existing["id"]

                    result = update_post(
                        directus_url,
                        token,
                        args.collection,
                        existing["id"],
                        payload,
                        timeout=args.timeout,
                    )
                else:
                    post.action = "create"

                    result = create_post(
                        directus_url,
                        token,
                        args.collection,
                        payload,
                        timeout=args.timeout,
                    )

                returned = result.get("data", {})

                if isinstance(returned, dict):
                    post.directus_id = returned.get("id", post.directus_id)

                print(f"[{index}/{len(posts)}] {post.action}: {post.slug}")

            except Exception as exc:
                post.action = "error"
                post.error = str(exc)
                print(f"[{index}/{len(posts)}] ERROR {post.slug}: {exc}", file=sys.stderr)

    else:
        for post in posts:
            post.action = "dry-run"

        print()
        print("Dry run only. Nothing was sent to Directus.")

    report_json = Path(args.report_json)
    report_csv = Path(args.report_csv)

    write_reports(posts, skipped, report_json, report_csv)

    print()
    print(f"Wrote JSON report: {report_json}")
    print(f"Wrote CSV report:  {report_csv}")

    if skipped:
        print()
        print("Skipped records:")
        for item in skipped[:30]:
            print(f"  {item.reason}: {item.url}")

        if len(skipped) > 30:
            print(f"  ... plus {len(skipped) - 30} more")

    errors = [post for post in posts if post.action == "error"]

    if errors:
        die(f"{len(errors)} Directus item(s) failed. See the reports for details.")

    print()
    print("Done.")


if __name__ == "__main__":
    main()