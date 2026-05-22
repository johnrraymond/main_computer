from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))

from main_computer.website_project_manifest import WebsiteProjectError, publish_website, website_publish_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a registered website into the local Docker platform.")
    parser.add_argument("site_id", help="Website id from runtime/websites/<site-id>/site.json")
    parser.add_argument(
        "--lane",
        default="local",
        choices=["local", "local-prod", "dev", "prod", "production"],
        help="Publish lane to rebuild/restart. local/prod/production/local-prod all mean Local Server.",
    )
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--dry-run", action="store_true", help="Print the publish plan without running Docker.")
    parser.add_argument("--no-verify", action="store_true", help="Do not probe the status URL after Docker starts.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    try:
        if args.dry_run:
            print(json.dumps(website_publish_plan(repo_root, args.site_id, args.lane), indent=2, sort_keys=True))
            return 0
        result = publish_website(repo_root, args.site_id, lane=args.lane, verify=not args.no_verify)
    except WebsiteProjectError as exc:
        print(f"FAIL: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
