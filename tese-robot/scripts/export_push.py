"""Push a response into the DeepSeekExport tab.

Usage:
    python scripts/export_push.py --title "عنوان" --file reply.md
    python scripts/export_push.py --title "عنوان" --text "..."
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state" / "deepseek_export.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="پاسخ")
    ap.add_argument("--text", default="")
    ap.add_argument("--file", default="")
    args = ap.parse_args()

    content = args.text
    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    if not content.strip():
        print("empty content")
        return 1

    items = []
    if STATE.exists():
        try:
            items = json.loads(STATE.read_text(encoding="utf-8-sig"))
        except Exception:
            items = []   # unreadable file: start clean
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    items.append({"id": uuid.uuid4().hex[:12], "ts": ts, "title": args.title, "content": content})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"pushed: {args.title} ({len(content)} chars) · total {len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
