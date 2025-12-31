"""
Tail the Payment Sync V2 trace file (JSONL).

Usage:
  python commands/tail_payment_sync_v2_trace.py
  python commands/tail_payment_sync_v2_trace.py 200
"""

from __future__ import annotations

import json
import os
import sys


def main():
    n = 80
    if len(sys.argv) > 1:
        try:
            n = int(sys.argv[1])
        except Exception:
            n = 80

    path = os.getenv("PAYMENT_SYNC_V2_TRACE_PATH") or "logs/payment_sync_v2_trace.jsonl"

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"missing: {path}")
        return

    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            print(line)
            continue
        print(json.dumps(obj, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

