"""
Show a single Payment Sync V2 trace request (grouped by rid).

Usage:
  python commands/show_payment_sync_v2_trace_request.py 2158e6f225
  python commands/show_payment_sync_v2_trace_request.py 2158e6f225 logs/payment_sync_v2_trace.json
"""

from __future__ import annotations

import json
import sys


def iter_json_objects(path: str):
    # File currently contains multiple JSON objects concatenated (not valid JSON array).
    # We'll parse by scanning braces.
    with open(path, "r", encoding="utf-8") as f:
        s = f.read()

    depth = 0
    start = None
    in_str = False
    esc = False

    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        else:
            if ch == '"':
                in_str = True
                continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunk = s[start : i + 1]
                start = None
                try:
                    yield json.loads(chunk)
                except Exception:
                    continue


def main():
    if len(sys.argv) < 2:
        print("missing rid")
        sys.exit(1)

    rid = sys.argv[1].strip()
    path = sys.argv[2] if len(sys.argv) > 2 else "logs/payment_sync_v2_trace.json"

    events = [e for e in iter_json_objects(path) if str(e.get("rid")) == rid]
    if not events:
        print(f"no events for rid={rid} in {path}")
        return

    # Preserve file order; optionally also sort by step when needed.
    for e in events:
        print(json.dumps(e, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

