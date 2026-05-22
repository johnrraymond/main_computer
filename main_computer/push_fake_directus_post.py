#!/usr/bin/env python3
"""
push_fake_directus_post.py

Push one fake test item into Directus `posts`.

Usage:

  export DIRECTUS_URL="http://your-directus-url"
  export DIRECTUS_TOKEN="your-static-token"

  python push_fake_directus_post.py --dry-run
  python push_fake_directus_post.py --apply

Optional:

  python push_fake_directus_post.py --collection posts --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests


def die(message: str, exit_code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def directus_request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
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
            json=json_body,
            timeout=30,
        )
    except requests.RequestException as exc:
        die(f"Request failed: {exc}")

    if not response.ok:
        print(f"\nHTTP {response.status_code} from {url}", file=sys.stderr)
        print(response.text, file=sys.stderr)
        die("Directus request failed.")

    try:
        return response.json()
    except ValueError:
        die(f"Expected JSON response from Directus, got:\n{response.text}")


def get_collection_fields(base_url: str, token: str, collection: str) -> set[str]:
    """
    Returns the field names currently configured on the collection.
    Directus endpoint: GET /fields/{collection}
    """
    url = f"{base_url}/fields/{collection}"
    payload = directus_request("GET", url, token)

    fields = set()
    for item in payload.get("data", []):
        field_name = item.get("field")
        if isinstance(field_name, str):
            fields.add(field_name)

    if not fields:
        die(f"No fields found for collection {collection!r}. Does it exist?")

    return fields


def make_fake_post() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    return {
        "status": "published",

        # These will only be sent if the fields exist in Directus:
        "title": "Fake GreatLibrary Import Test",
        "slug": "fake-greatlibrary-import-test",
        "body": (
            "This is a fake test post created by the Directus import script.\n\n"
            "If you can see this in Directus, the API token, collection name, "
            "and create permissions are working."
        ),
        "excerpt": "A fake post used to test the GreatLibrary import pipeline.",
        "source_url": "https://johnrraymond.greatlibrary.io/post/fake-greatlibrary-import-test",
        "author_name": "john raymond",
        "published_at": now,
        "read_time_minutes": 1,
        "legacy_date_label": "test import",
        "legacy_source": "greatlibrary",
        "raw_import_text": (
            "Fake raw source text.\n"
            "This simulates the text we will later extract from the old archive JSON."
        ),
    }


def filter_payload_to_existing_fields(
    payload: dict[str, Any],
    existing_fields: set[str],
) -> tuple[dict[str, Any], list[str]]:
    filtered = {
        key: value
        for key, value in payload.items()
        if key in existing_fields
    }

    skipped = [
        key
        for key in payload.keys()
        if key not in existing_fields
    ]

    return filtered, skipped


def create_item(
    base_url: str,
    token: str,
    collection: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Directus endpoint: POST /items/{collection}
    """
    url = f"{base_url}/items/{collection}"
    return directus_request("POST", url, token, json_body=payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--collection",
        default="posts",
        help="Directus collection to insert into. Default: posts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the payload but do not create anything.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create the fake item in Directus.",
    )

    args = parser.parse_args()

    if args.dry_run and args.apply:
        die("Use either --dry-run or --apply, not both.")

    if not args.dry_run and not args.apply:
        die("Choose --dry-run first, then --apply when ready.")

    directus_url = os.environ.get("DIRECTUS_URL", "").strip().rstrip("/")
    directus_token = os.environ.get("DIRECTUS_TOKEN", "").strip()

    if not directus_url:
        die("Missing DIRECTUS_URL environment variable.")

    if not directus_token:
        die("Missing DIRECTUS_TOKEN environment variable.")

    print(f"Checking fields for collection: {args.collection}")
    existing_fields = get_collection_fields(directus_url, directus_token, args.collection)

    fake_post = make_fake_post()
    payload, skipped = filter_payload_to_existing_fields(fake_post, existing_fields)

    if not payload:
        die("After filtering, no usable fields remain for this collection.")

    print("\nFields found in Directus:")
    print(", ".join(sorted(existing_fields)))

    if skipped:
        print("\nSkipped fields because they do not exist yet:")
        print(", ".join(skipped))

    print("\nPayload that will be sent:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.dry_run:
        print("\nDry run only. Nothing was created.")
        return

    print("\nCreating fake item...")
    result = create_item(directus_url, directus_token, args.collection, payload)

    print("\nCreated item:")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()