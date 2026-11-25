"""Smoke-check the dashboard: HTML, theme, tabs, and the three required sections."""
import json
import urllib.request

URL = "http://127.0.0.1:5077/"


def main() -> int:
    html = urllib.request.urlopen(URL, timeout=90).read().decode("utf-8", "replace")
    checks = {
        "bytes": len(html),
        "is_html": "<html" in html.lower(),
        "pro_theme": "PRO-THEME" in html,
        "tabs": html.count('class="tab'),
        "buy_candidates": "کاندیدهای خرید" in html,
        "positions": "پوزیشن" in html,
        "sell_candidates": "کاندیدهای فروش" in html,
        "chart": "نمودار" in html,
        "export_archive": "DeepSeekExport" in html,
    }
    print(json.dumps(checks, ensure_ascii=False, indent=1))
    return 0 if checks["is_html"] and checks["pro_theme"] and checks["buy_candidates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
