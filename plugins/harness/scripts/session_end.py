#!/usr/bin/env python3
"""SessionEnd hook: write one line to the ledger about what the session cost.

Recording happens at the end rather than per turn because that is when the
transcript is complete and the counters have stopped moving. A session that
changed nothing is skipped — a ledger full of conversational turns hides the
sessions that actually did work.
"""

from __future__ import annotations

import sys

from ledger import record
from state import gates_disabled, guard, load_session, read_event


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    session = load_session(event.get("session_id", "unknown"))
    if not session.get("files_touched"):
        return 0

    record(session, event.get("transcript_path"))
    return 0


if __name__ == "__main__":
    with guard("session_end"):
        sys.exit(main())
