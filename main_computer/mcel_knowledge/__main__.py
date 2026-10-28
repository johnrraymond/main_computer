"""Read-only semantic catalog exploration. Never invokes TaskManagerService actions."""
import argparse
import json
from pathlib import Path
from . import explain, interpret, load_catalog


def main() -> None:
    p = argparse.ArgumentParser(description="Main Computer semantic knowledge database (read-only)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List semantic concepts and observations")
    q = sub.add_parser("explain", help="Explain a concept or observation")
    q.add_argument("id")
    s = sub.add_parser("inspect", help="Interpret a saved source-shaped computer observation envelope (read-only)")
    s.add_argument("json_file", type=Path)
    args = p.parse_args()
    if args.command == "list":
        cat = load_catalog()
        output = {key: sorted(cat[key]) for key in ("concepts", "observations", "operations")}
    elif args.command == "explain":
        output = explain(args.id)
    else:
        output = interpret(json.loads(args.json_file.read_text(encoding="utf-8")))
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
