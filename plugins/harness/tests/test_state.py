"""Session state under more than one writer.

Until parallel workers existed, exactly one process edited files in a session, so
a read-modify-write of a single JSON file was safe by construction. It is not
safe now. What makes it worse than an ordinary lost update is where the loss
lands: `files_touched` is what the end-of-turn gate checks against the plan's
scope fence, so an edit that falls out of this file is an edit the scope check
never sees, and the gate reports clean.
"""

from __future__ import annotations

import json

import pytest

from state import load_session, save_session, session_state, shard_path, shards_dir

# Long enough that every child is guaranteed to have loaded before any saves.
CHILD = """
import time
from state import session_state
agent = sys.argv[1]
with session_state("sess", writer=agent) as s:
    time.sleep(0.4)
    s["files_touched"] = sorted(set(s.get("files_touched") or []) | {"/repo/" + agent + ".py"})
    s["lines_changed"] = int(s.get("lines_changed") or 0) + 10
    checks = s.setdefault("checks", {"run": 0, "failed": 0})
    checks["run"] = int(checks.get("run", 0)) + 2
"""

WRITERS = 8


def test_concurrent_writers_do_not_lose_edits(data_dir, run_child):
    procs = [run_child(CHILD, f"w{i}") for i in range(WRITERS)]
    for proc in procs:
        assert proc.wait() == 0

    session = load_session("sess")

    assert sorted(session["files_touched"]) == [f"/repo/w{i}.py" for i in range(WRITERS)]
    assert session["lines_changed"] == WRITERS * 10
    assert session["checks"]["run"] == WRITERS * 2


SAME_WRITER_PROCS = 12
SAME_WRITER_STEPS = 50

# One writer id, many processes. The test above hands each child a distinct id,
# so every one writes a shard of its own and nothing *can* be lost — which is
# exactly why it stayed green while this was broken.
SAME_WRITER = f"""
from state import session_state
for _ in range({SAME_WRITER_STEPS}):
    with session_state("sess", writer="main") as s:
        s["lines_changed"] = int(s.get("lines_changed") or 0) + 1
        s["shell_changes"] = int(s.get("shell_changes") or 0) + 1
"""


def test_one_writer_running_more_than_once_at_a_time_loses_nothing(data_dir, run_child):
    """Recording a delta is a read-modify-write, and it needs a lock.

    One session id does not mean one process. Hooks fire per tool call, Claude
    Code issues tool calls in parallel, and `bash_watch` writes to the same
    shard from both its Pre and its Post hook — so two processes carrying the
    same writer id read the same total and the second one overwrites the first.

    Measured before the fix: 129 of 600 increments survived. What is being lost
    is `files_touched`, which is what the scope fence and the end-of-turn gate
    read, so a lost update is an edit no gate ever sees and the turn is reported
    clean. Counters stand in for it here only because an integer makes the size
    of the loss visible.
    """
    procs = [run_child(SAME_WRITER) for _ in range(SAME_WRITER_PROCS)]
    for proc in procs:
        assert proc.wait() == 0, proc.stderr.read().decode() if proc.stderr else ""

    session = load_session("sess")
    expected = SAME_WRITER_PROCS * SAME_WRITER_STEPS
    assert session["lines_changed"] == expected, f"lost {expected - session['lines_changed']}"
    assert session["shell_changes"] == expected, f"lost {expected - session['shell_changes']}"


def test_a_counter_only_update_leaves_the_session_scalars_alone(data_dir):
    """`ACCUMULATORS` decides per-writer counter from per-session fact.

    Take `shell_changes` out of that tuple and the counter's *value* still comes
    out right — `_blank_accumulators` and `_merge_shards` both name it outright
    — which is why every assertion above stays green either way. What changes is
    who writes the session file: `bash_watch`, which on most commands mutates
    nothing else, becomes a writer of the whole scalar record, rewriting it from
    a snapshot it read before its own work. The Stop hook holds that same file
    open across the entire project suite, so the window is seconds wide.
    """
    with session_state("sess", writer="main") as session:
        session["edit_gate_prompted"] = True

    scalars = data_dir / "sessions" / "sess.json"
    before = json.loads(scalars.read_text(encoding="utf-8"))

    with session_state("sess", writer="w1") as session:
        session["shell_changes"] = int(session.get("shell_changes") or 0) + 3

    assert json.loads(scalars.read_text(encoding="utf-8")) == before, (
        "a counter-only update must not rewrite the session's shared facts"
    )
    assert load_session("sess")["shell_changes"] == 3


def test_writer_records_only_its_own_delta(data_dir):
    """A writer must not re-record what it merely read from another writer.

    The merged view a caller sees includes every writer's files. Writing that
    view straight back into this writer's own record would count the other
    writers' lines a second time on every edit.
    """
    with session_state("sess", writer="a") as session:
        session["files_touched"] = ["/repo/a.py"]
        session["lines_changed"] = 10

    with session_state("sess", writer="b") as session:
        assert session["files_touched"] == ["/repo/a.py"]  # sees a's work
        session["files_touched"] = sorted(set(session["files_touched"]) | {"/repo/b.py"})
        session["lines_changed"] = int(session["lines_changed"]) + 10

    merged = load_session("sess")
    assert merged["files_touched"] == ["/repo/a.py", "/repo/b.py"]
    assert merged["lines_changed"] == 20


def test_scalar_fields_are_shared_across_writers(data_dir):
    """Per-session facts stay per-session; they are not summed or unioned."""
    with session_state("sess", writer="main") as session:
        session["edit_gate_prompted"] = True
        session["repo_root"] = "/repo"

    assert load_session("sess")["edit_gate_prompted"] is True

    with session_state("sess", writer="worker-1") as session:
        assert session["edit_gate_prompted"] is True
        assert session["repo_root"] == "/repo"

    assert load_session("sess")["edit_gate_prompted"] is True


def test_default_writer_keeps_the_single_process_case_working(data_dir):
    """Callers that never pass a writer behave exactly as they did before."""
    with session_state("sess") as session:
        session["files_touched"] = ["/repo/a.py"]
        session["lines_changed"] = 5

    session = load_session("sess")
    assert session["files_touched"] == ["/repo/a.py"]
    assert session["lines_changed"] == 5
    assert session["session_id"] == "sess"


def test_unknown_session_returns_defaults(data_dir):
    session = load_session("never-seen")
    assert session["files_touched"] == []
    assert session["lines_changed"] == 0
    assert session["session_id"] == "never-seen"


@pytest.mark.parametrize(
    "writer",
    ["../../pwned", "/tmp/harness-pwned", "../../../../etc/pwned", "a/../b", "with space", ""],
)
def test_writer_ids_cannot_escape_the_session_directory(data_dir, writer):
    """Writer ids come from a hook payload, so they are treated as untrusted.

    `_safe` is the only thing between an id and the filesystem, and the first
    three cases are what prove it: without it `../../pwned` writes into the
    plugin data root, and `/tmp/harness-pwned` escapes entirely, because
    `Path("sessions/x.d") / "/tmp/y"` discards the left-hand side.
    """
    with session_state("sess", writer=writer) as session:
        session["files_touched"] = ["/repo/x.py"]

    assert load_session("sess")["files_touched"] == ["/repo/x.py"]
    written = shard_path("sess", writer).resolve()
    assert written.is_file(), "expected the write to land somewhere"
    assert written.parent == shards_dir("sess").resolve()


def test_counters_survive_the_upgrade_from_the_single_file_layout(data_dir):
    """A session in flight when the plugin updates keeps its scope fence.

    Counters used to live in the session file. The first hook to open
    `session_state` afterwards must not replace them with zeros: `files_touched`
    is the scope fence, and emptying it makes the end-of-turn gate skip itself
    and report clean on work it never saw.
    """
    legacy = data_dir / "sessions" / "sess.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "session_id": "sess",
                "files_touched": ["/repo/a.py", "/repo/b.py"],
                "lines_changed": 400,
                "checks": {"run": 12, "failed": 1},
            }
        ),
        encoding="utf-8",
    )

    with session_state("sess", writer="main"):
        pass  # a hook that touches no accumulator at all, such as the prompt gate

    session = load_session("sess")
    assert session["files_touched"] == ["/repo/a.py", "/repo/b.py"]
    assert session["lines_changed"] == 400
    assert session["checks"]["run"] == 12


def test_a_fresh_session_start_clears_the_previous_runs_shards(data_dir):
    with session_state("sess", writer="worker-1") as session:
        session["files_touched"] = ["/repo/stale.py"]

    save_session({"session_id": "sess", "files_touched": [], "lines_changed": 0}, reset=True)

    assert load_session("sess")["files_touched"] == []
    assert not list(shards_dir("sess").glob("worker-*.json"))


def test_a_resumed_session_keeps_each_workers_record(data_dir):
    """A resume can land mid-fan-out, while a worker is still running.

    Deleting its shard leaves the worker's own stop check unable to find the
    files it wrote, so its slice is reported as empty and never verified.
    """
    with session_state("sess", writer="worker-1") as session:
        session["files_touched"] = ["/repo/a.py"]
        session["lines_changed"] = 10

    save_session({**load_session("sess"), "repo_root": "/repo"}, reset=False)

    assert (shards_dir("sess") / "worker-1.json").is_file()
    session = load_session("sess")
    assert session["files_touched"] == ["/repo/a.py"]
    assert session["lines_changed"] == 10
    assert session["repo_root"] == "/repo"


def test_a_second_edit_by_the_same_writer_adds_rather_than_doubles(data_dir):
    """The only shape where the delta sees a non-zero starting point.

    Every other test starts from an empty session, where a writer's delta and
    its merged view are numerically identical — so they cannot tell "record what
    you added" apart from "record everything you can see".
    """
    for name in ("a.py", "b.py"):
        with session_state("sess", writer="worker-1") as session:
            session["files_touched"] = sorted(set(session["files_touched"]) | {f"/repo/{name}"})
            session["lines_changed"] = int(session["lines_changed"]) + 10
            checks = session.setdefault("checks", {"run": 0, "failed": 0})
            checks["run"] = int(checks["run"]) + 2

    session = load_session("sess")
    assert session["files_touched"] == ["/repo/a.py", "/repo/b.py"]
    assert session["lines_changed"] == 20
    assert session["checks"]["run"] == 4


# --- kill switch --------------------------------------------------------------


def test_the_kill_switch_reads_the_data_directory_it_is_told_about(data_dir):
    """`OFF_MARKER` was a module constant, bound to whatever `CLAUDE_PLUGIN_DATA`
    said at *import* time, while `data_dir()` beside it re-read the environment on
    every call. Under pytest — which imports at collection and sets the fixture's
    env afterwards — that resolved to the developer's own `~/.claude` marker.

    So the suite's answer to "are the gates on?" came from the developer's machine
    rather than from the fixture. On a machine where `switch.py off` had ever been
    run, every gate test passed by checking nothing at all. That is precisely what
    `conftest.data_dir` deletes `HARNESS_OFF` to prevent, left open for the file
    that means the same thing.
    """
    import state

    assert not state.gates_disabled(), "a fresh data directory has no marker"

    (data_dir / "off").write_text("off\n", encoding="utf-8")

    assert state.gates_disabled(), "the switch was thrown and no gate would have noticed"


def test_throwing_the_switch_under_test_cannot_reach_the_real_data_directory(data_dir):
    """The same bug pointing outward: `switch.py off` in a test wrote its marker
    into the user's live plugin data directory and disabled their real harness.

    Asserted by location rather than by running the script, so the test can prove
    the isolation without having to risk breaking it to do so.
    """
    import state

    assert state.off_marker() == data_dir / "off"
    assert state.off_marker().parent == state.data_dir()
