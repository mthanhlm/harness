#!/usr/bin/env python3
"""Kill switch for the harness gates.

Gates are off when HARNESS_OFF=1 is set in the environment or when the marker
file exists in the plugin data directory. Env var wins and cannot be cleared
from here, since it belongs to whoever launched the session.
"""

import sys

from state import data_dir, env_disabled, off_marker


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()

    if action == "off":
        data_dir().mkdir(parents=True, exist_ok=True)
        marker = off_marker()
        marker.write_text("disabled via switch.py off\n", encoding="utf-8")
        print("Harness gates OFF. No hook will block anything.")
        print(f"Marker: {marker}")
        return 0

    if action == "on":
        off_marker().unlink(missing_ok=True)
        if env_disabled():
            print("Marker removed, but HARNESS_OFF=1 is set in the environment.")
            print("Gates stay off until that variable is unset in the shell that")
            print("launched Claude Code.")
            return 0
        print("Harness gates ON.")
        return 0

    if action == "status":
        if env_disabled():
            print("OFF (HARNESS_OFF=1 in environment)")
        elif off_marker().exists():
            print(f"OFF (marker file present: {off_marker()})")
        else:
            print("ON — session bootstrap, per-edit checks, and the end-of-turn gate are active.")
        print(f"State directory: {data_dir()}")
        return 0

    print(f"Unknown action {action!r}. Use: on | off | status", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
