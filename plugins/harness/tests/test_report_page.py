"""The one page a session ends on now, instead of a wall of chat text.

The user's own complaint was that the end of a run left them "một đống text"
with no way to click into any one part of it. This page has to hold the
contract, the lessons and the session's state on its own, with no network at
all, and it has to survive a contract written by a model that quotes HTML —
so the tests here are about the two ways that page could quietly fail: a
remote resource it depends on, and text it fails to escape.
"""

from __future__ import annotations

import io

import contract
import lessons
import report_page
import state

CONTRACT_TEXT = """# Plan: report page

status: approved
verdict: patch

## Goal

Give the user one page instead of a wall of chat text.

## Scope

- `plugins/harness/scripts/report_page.py` — renders the page

## Disagreement

The user wanted a page; the model proposed a longer chat message instead.
The user's proposal won.
"""

HOSTILE_CONTRACT_TEXT = """# Plan: hostile

status: approved
verdict: patch

## Goal

<script>alert(1)</script> and a "quoted" & <tag> for good measure.

## Scope

- `src/one.py` — one

## Disagreement

None recorded, but here is a script anyway: <script>alert(1)</script>
"""


def _write_contract(data_dir, session_id: str, text: str) -> None:
    path = contract.contract_path(session_id)
    path.write_text(text, encoding="utf-8")


def test_the_page_has_no_reference_to_an_external_resource(data_dir):
    """Self-contained means self-contained: no CDN, no remote font, no fetch.

    This page is meant to open from `file://` with no network at all, so any
    `http://` or `https://` anywhere in it is a resource this page cannot
    actually load offline.
    """
    _write_contract(data_dir, "sess-offline", CONTRACT_TEXT)
    html_text = report_page.render("sess-offline")

    assert "http://" not in html_text
    assert "https://" not in html_text


def test_a_hostile_contract_is_escaped_not_executed(data_dir):
    """A contract is arbitrary markdown a model wrote, not trusted HTML.

    The one defect here that is both easy to introduce and genuinely
    dangerous: a contract that mentions `<script>` must render as text on the
    page, never as a tag the browser executes.
    """
    _write_contract(data_dir, "sess-hostile", HOSTILE_CONTRACT_TEXT)
    html_text = report_page.render("sess-hostile")

    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text


def test_a_superseded_lesson_still_appears_and_is_marked(data_dir, monkeypatch, tmp_path):
    """Being wrong stays visible — the whole point of `lessons.revise`.

    A superseded entry must not vanish from the page and must not read as if
    it were still live; it has to say what replaced it.
    """
    monkeypatch.setattr(state, "repo_root", lambda cwd=None: tmp_path)
    original = lessons.append("first attempt", "we tried X", root=tmp_path)
    lessons.revise(original.id, "corrected", "X was wrong, we do Y now", root=tmp_path)

    _write_contract(data_dir, "sess-lessons", CONTRACT_TEXT)
    html_text = report_page.render("sess-lessons")

    assert "we tried X" in html_text
    assert "Đã được thay thế bởi" in html_text
    assert f'id="lesson-{original.id}"' in html_text


def test_the_path_is_stable_across_two_runs_and_differs_between_sessions(data_dir):
    """Publishing the same path twice must update the same artifact.

    That only works if the path is derived from the session id, not from a
    timestamp or a counter — otherwise every `write` mints a fresh link and
    the one the user already opened goes stale.
    """
    first = report_page.report_page_path("same-session")
    second = report_page.report_page_path("same-session")
    other = report_page.report_page_path("different-session")

    assert first == second
    assert first != other


def test_a_missing_contract_does_not_raise(data_dir):
    """This runs at the end of a turn; a traceback here is worse than a thin page.

    No contract file exists for this session id at all — the normal state for
    a session that used no plan skill — and the page must still render.
    """
    html_text = report_page.render("sess-no-contract")

    assert "Không tìm thấy hợp đồng" in html_text


def test_session_state_renders_recorded_facts_not_invented_zeros(data_dir):
    """A field is only shown once something actually recorded it.

    Before any hook has touched this session id, `lines_changed` reads 0 in
    `load_session` only because that is the default shape, not because a
    measurement of zero was taken. The page must not present that as data.
    """
    html_text = report_page.render("sess-untouched")
    assert "Chưa ghi nhận trạng thái" in html_text

    with state.session_state("sess-recorded") as session:
        session["files_touched"] = ["a.py"]
        session["lines_changed"] = 7

    recorded_html = report_page.render("sess-recorded")
    assert "a.py" in recorded_html
    assert ">7<" in recorded_html


def test_write_prints_the_absolute_path_and_creates_the_file(data_dir):
    path = report_page.write("sess-write")
    assert path.is_absolute()
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_main_refuses_without_a_session_id(data_dir, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    code = report_page.main(["write"])
    captured = capsys.readouterr()

    assert code == 1
    assert "no session id" in captured.err


def test_the_status_line_carries_the_verdict_and_a_link_once_the_page_exists(data_dir):
    """The status line is the only surface that is always visible, which is the
    whole reason it was asked for: a link handed over in a message has scrolled
    away by the time it is wanted."""
    _write_contract(data_dir, "sess-link", CONTRACT_TEXT)

    before = report_page.status_line("sess-link")
    assert "patch" in before and "approved" in before
    assert "file://" not in before, "a link was offered before the page was written"

    report_page.write("sess-link")
    after = report_page.status_line("sess-link")

    assert after.endswith(report_page.report_page_path("sess-link").as_uri())


def test_a_session_with_no_contract_says_nothing_at_all(data_dir):
    """Not "no contract". A status line that comments on every session that
    never needed a plan is noise, and noise is how a status line stops being
    read at all."""
    assert report_page.status_line("sess-unplanned") == ""


def test_the_status_line_reads_the_session_id_off_json_on_stdin(data_dir, monkeypatch, capsys):
    """Claude Code hands a status-line command its context as JSON on stdin
    rather than as an argument, so this is the only way such a command can know
    which session it is drawing."""
    _write_contract(data_dir, "sess-stdin", CONTRACT_TEXT)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(
        report_page.sys, "stdin", io.StringIO('{"session_id": "sess-stdin"}')
    )

    assert report_page.main(["link"]) == 0
    assert "patch" in capsys.readouterr().out


def test_the_status_line_never_fails_the_shell_that_called_it(data_dir, monkeypatch, capsys):
    """It runs on the editor's cadence, not the turn's. A traceback there lands
    in the one place on screen that is always visible, on every redraw."""
    monkeypatch.setattr(
        report_page.contract_mod, "load", lambda _: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert report_page.main(["link", "sess-broken"]) == 0
    assert capsys.readouterr().out == ""


def test_the_lessons_this_session_declared_are_on_its_own_page(data_dir):
    """The page is built at the end of the run; the harvest happens later, at
    SessionEnd. So the recorded section shows the state *before* this session,
    and the one thing a reader most wants to check — did it write down what it
    learned — was the one thing the page could not show them."""
    _write_contract(
        data_dir,
        "sess-pending",
        CONTRACT_TEXT + "\n## Lessons\n\n- Releasing takes two commands: the first installs nothing.\n",
    )

    html_text = report_page.render("sess-pending")

    assert "Releasing takes two commands" in html_text
    assert "chưa ghi nhận" in html_text, "a claim must not be shown as a recorded entry"


def test_an_unapproved_plans_lessons_are_not_shown_as_this_sessions(data_dir):
    """Nothing harvests them, so presenting them as pending would promise a
    write that will never happen."""
    pending = CONTRACT_TEXT.replace("status: approved", "status: pending")
    _write_contract(data_dir, "sess-unapproved", pending + "\n## Lessons\n\n- Something.\n")

    assert "chưa ghi nhận" not in report_page.render("sess-unapproved")


def test_the_published_form_carries_no_document_frame_of_its_own(data_dir):
    """The Artifact tool wraps what it is given in its own
    `<!doctype html><head></head><body>` skeleton, so a complete document
    published through it nests one page inside another. The standalone file has
    to stay a complete document for `file://` and the status line, so the two
    readers get two files built from one body."""
    _write_contract(data_dir, "sess-two-forms", CONTRACT_TEXT)

    standalone = report_page.write("sess-two-forms")
    fragment = standalone.with_suffix(".fragment.html").read_text(encoding="utf-8")

    assert standalone.read_text(encoding="utf-8").startswith("<!doctype html>")
    for frame in ("<!doctype", "<html", "<head>", "<body>"):
        assert frame not in fragment.lower(), f"the published form still frames itself with {frame}"
    assert "<title>" in fragment, "the artifact takes its name from the title tag"
    assert "Bài học đã ghi nhận" in fragment, "the two forms must render the same body"
