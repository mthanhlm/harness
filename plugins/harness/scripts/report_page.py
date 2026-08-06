#!/usr/bin/env python3
"""One self-contained page replacing the wall of text at the end of a run.

The user's complaint, verbatim: the end of a session used to hand them "một
đống text và tôi chả hiểu gì" — a pile of text they could not follow. Their own
fix was a page they can click: the contract, the lessons and this session's
state, updated in place across a session rather than re-pasted into chat.

The lead publishes the file this writes with the Artifact tool. Publishing the
same path again updates the same URL instead of minting a new one, which is
the entire reason the path is derived from the session id rather than a
timestamp — a fresh name every call would mint a fresh link every call, and
the one the user already opened would go stale.

Nothing here re-parses `contract.md` or `lessons.md`. `contract.py` and
`lessons.py` already own that shape; a second parser here would drift from
either the day one of them changes what it accepts.
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
from pathlib import Path

import contract as contract_mod
import lessons as lessons_mod
import state


def report_page_path(session_id: str) -> Path:
    """Beside the contract, named through the same sanitiser it uses.

    `.with_suffix(".html")` rather than a fresh sanitiser: `contract_path`
    already turns a session id into a safe, stable filename, and reimplementing
    that here is exactly the second parser this module's docstring warns about.
    """
    return contract_mod.contract_path(session_id).with_suffix(".html")


def _esc(text: str) -> str:
    """Escape for HTML text content. Contract prose is written by a model and
    is arbitrary — a contract that mentions `<script>` must render as text, not
    run as one."""
    return html.escape(text, quote=True)


def _pre(text: str) -> str:
    text = text.strip()
    if not text:
        return '<p class="empty">(không có nội dung)</p>'
    return f'<div class="scroll-x"><pre>{_esc(text)}</pre></div>'


def _render_contract(session_id: str) -> str:
    agreed = contract_mod.load(session_id)
    if agreed is None:
        return (
            "<h2>Hợp đồng (Contract)</h2>"
            '<p class="empty">Không tìm thấy hợp đồng cho phiên này.</p>'
        )

    goal = contract_mod.section(agreed.text, "Goal")
    disagreement = contract_mod.section(agreed.text, "Disagreement")
    scope = agreed.scoped_files

    scope_html = (
        "<ul>" + "".join(f"<li><code>{_esc(f)}</code></li>" for f in scope) + "</ul>"
        if scope
        else '<p class="empty">Không có tệp nào trong phạm vi.</p>'
    )

    return f"""<h2>Hợp đồng (Contract)</h2>
<dl>
  <dt>Trạng thái</dt><dd>{_esc(agreed.status)}</dd>
  <dt>Kết luận</dt><dd>{_esc(agreed.verdict)}</dd>
</dl>
<h3>Mục tiêu</h3>
{_pre(goal)}
<h3>Phạm vi (Scope)</h3>
{scope_html}
<h3>Bất đồng (Disagreement)</h3>
{_pre(disagreement)}
"""


def _pending_lessons(session_id: str) -> str:
    """What this session's own contract says it learned, before the hook takes it.

    The page is built at the end of the run and `session_end` harvests at
    SessionEnd, which is later — so the recorded section below shows the state
    *before* this session, and the one thing the reader most wants to check
    ("did it write down what it learned?") was the one thing missing. Shown as
    pending rather than as recorded, because until the hook fires it is a claim
    in a file that gets thrown away, not a durable entry.
    """
    agreed = contract_mod.load(session_id)
    if agreed is None or not agreed.approved:
        return ""
    declared = contract_mod.section(agreed.text, "Lessons").strip()
    if not declared:
        return ""
    return (
        "<h2>Bài học phiên này khai báo (chưa ghi nhận)</h2>"
        '<p class="note">Được thu hoạch vào <code>.harness/lessons.md</code> khi'
        " phiên kết thúc.</p>" + _pre(declared)
    )


def _render_lessons(session_id: str) -> str:
    found = lessons_mod.entries()
    if not found:
        return (
            "<h2>Bài học đã ghi nhận (Lessons)</h2>"
            '<p class="empty">Chưa có bài học nào được ghi nhận.</p>'
        )

    articles = []
    for lesson in found:
        badges = ""
        if lesson.superseded_by:
            badges += (
                f'<p class="badge">Đã được thay thế bởi '
                f'<a href="#lesson-{_esc(lesson.superseded_by)}">{_esc(lesson.superseded_by)}</a></p>'
            )
        if lesson.supersedes:
            badges += (
                f'<p class="note">Thay thế cho '
                f'<a href="#lesson-{_esc(lesson.supersedes)}">{_esc(lesson.supersedes)}</a></p>'
            )
        css_class = "lesson superseded" if lesson.superseded_by else "lesson"
        articles.append(
            f'<article id="lesson-{_esc(lesson.id)}" class="{css_class}">'
            f"<h3>{_esc(lesson.id)} · {_esc(lesson.date)} · {_esc(lesson.title)}</h3>"
            f"{badges}"
            f"{_pre(lesson.body)}"
            "</article>"
        )

    return "<h2>Bài học đã ghi nhận (Lessons)</h2>" + "".join(articles)


def _recorded(session_id: str) -> bool:
    """Whether anything was ever written for this session.

    A session that has never run a hook merges to the same all-zero shape as
    one that genuinely touched nothing, and the two must not read the same —
    a placeholder default is not a measurement. `save_session` and
    `session_state` both leave the main writer's shard on disk the moment
    either runs, so its presence is the signal; its content is not.
    """
    main_shard = state.shard_path(session_id, "main")
    if main_shard.exists():
        return True
    try:
        return any(main_shard.parent.iterdir())
    except OSError:
        return False


def _render_state(session_id: str) -> str:
    if not _recorded(session_id):
        return (
            "<h2>Trạng thái phiên này</h2>"
            '<p class="empty">Chưa ghi nhận trạng thái nào cho phiên này.</p>'
        )

    session = state.load_session(session_id)
    files = session.get("files_touched") or []
    checks = session.get("checks") or {}
    files_html = (
        _pre("\n".join(files)) if files else '<p class="empty">(không có)</p>'
    )

    return f"""<h2>Trạng thái phiên này</h2>
<dl>
  <dt>Tệp đã sửa ({len(files)})</dt><dd>{files_html}</dd>
  <dt>Số dòng thay đổi</dt><dd>{int(session.get("lines_changed") or 0)}</dd>
  <dt>Lệnh shell đã chạy</dt><dd>{int(session.get("shell_changes") or 0)}</dd>
  <dt>Kiểm tra (gate checks)</dt><dd>{int(checks.get("run") or 0)} lần chạy, {int(checks.get("failed") or 0)} lần chặn</dd>
</dl>
"""


# Inlined so the page opens correctly from `file://` with no network at all —
# a CDN stylesheet or font is exactly the "external resource" the brief bans.
_STYLE = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #6b6b6b; --border: #d8d8d8;
  --card: #f6f6f7; --accent: #a15c00;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #14161a; --fg: #e8e8e8; --muted: #9a9a9a; --border: #363a40; --card: #1d2025; --accent: #e0a458; }
}
:root[data-theme="dark"] { --bg: #14161a; --fg: #e8e8e8; --muted: #9a9a9a; --border: #363a40; --card: #1d2025; --accent: #e0a458; }
:root[data-theme="light"] { --bg: #ffffff; --fg: #1a1a1a; --muted: #6b6b6b; --border: #d8d8d8; --card: #f6f6f7; --accent: #a15c00; }
* { box-sizing: border-box; }
html, body { max-width: 100%; overflow-x: hidden; }
body {
  background: var(--bg); color: var(--fg); margin: 0; padding: 1.5rem;
  font: 15px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
header { margin-bottom: 1.5rem; }
h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.15rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; margin-top: 2rem; }
h3 { font-size: 0.95rem; color: var(--muted); margin-bottom: 0.4rem; }
.meta { color: var(--muted); font-size: 0.9rem; }
section { margin-bottom: 1rem; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.3rem 1rem; margin: 0.5rem 0; }
dt { color: var(--muted); }
dd { margin: 0; }
.scroll-x { overflow-x: auto; max-width: 100%; }
pre {
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  padding: 0.75rem; margin: 0; white-space: pre-wrap; word-break: break-word;
  font: 13px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
}
code { font: 0.9em ui-monospace, SFMono-Regular, Consolas, monospace; }
.empty { color: var(--muted); font-style: italic; }
.lesson { border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem; margin: 0.75rem 0; }
.lesson.superseded { opacity: 0.75; }
.badge { color: var(--accent); font-weight: 600; margin: 0.25rem 0; }
.note { color: var(--muted); margin: 0.25rem 0; }
ul { margin: 0.3rem 0; padding-left: 1.2rem; }
"""


def _body(session_id: str) -> str:
    """Everything inside `<body>`, plus the title and the styles.

    Never raises — a broken contract, a missing lessons file or unreadable state
    each degrade to a section saying so, because this runs at the end of a turn
    and a traceback here is worse than a thin page.
    """

    def safe(section_fn) -> str:
        try:
            return section_fn(session_id)
        except Exception:
            return '<p class="empty">Không đọc được phần này.</p>'

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<title>Báo cáo phiên — {_esc(session_id)}</title>
<style>{_STYLE}</style>
<header>
  <h1>Báo cáo phiên làm việc</h1>
  <p class="meta">Phiên: <code>{_esc(session_id)}</code> · Cập nhật lúc {_esc(stamp)}</p>
</header>
<section id="contract">{safe(_render_contract)}</section>
<section id="pending-lessons">{safe(_pending_lessons)}</section>
<section id="lessons">{safe(_render_lessons)}</section>
<section id="state">{safe(_render_state)}</section>
"""


def render(session_id: str) -> str:
    """The standalone document — what opens from `file://` with no network."""
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{_body(session_id)}</html>
"""


def write(session_id: str) -> Path:
    """Write both forms, and return the standalone one.

    Two files because two readers want incompatible things. The status line and
    the browser need a complete document; the Artifact tool wraps whatever it is
    given in its own `<!doctype html><head></head><body>` skeleton, so handing it
    a complete document nests one page inside another. Rendering the shared body
    once and framing it twice is the only version of this where the published
    page and the local page cannot disagree.
    """
    path = report_page_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _body(session_id)
    path.with_suffix(".fragment.html").write_text(body, encoding="utf-8")
    path.write_text(
        '<!doctype html>\n<html lang="vi">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{body}</html>\n",
        encoding="utf-8",
    )
    return path


def status_line(session_id: str) -> str:
    """One line for a status line, or nothing at all.

    A status line is the only surface in Claude Code that is always visible, and
    the complaint this whole change answers is that the plan is only legible in a
    message that has already scrolled away. So: what the plan is, and where to
    read it, at all times.

    Silent when there is no contract. A status line that says "no contract" on
    every session that never needed one is noise, and noise is how a status line
    stops being read.

    Deliberately does not render the page. This runs on the editor's cadence, not
    the turn's, and re-rendering an HTML file that often to show a link would cost
    far more than the link is worth. The link appears once the page has been
    written; before that the line still carries the verdict and the status, which
    is the part that changes.
    """
    agreed = contract_mod.load(session_id)
    if agreed is None:
        return ""
    state = "approved" if agreed.approved else agreed.status
    parts = [f"harness: {agreed.verdict or 'plan'} · {state}"]
    page = report_page_path(session_id)
    if page.is_file():
        parts.append(page.as_uri())
    return " · ".join(parts)


def _session_id_from(argv: list[str], index: int) -> str:
    """A session id from the argument, the environment, or a JSON stdin payload.

    The third is there because Claude Code hands a status-line command its
    context as JSON on stdin rather than as an argument, so a status line has no
    other way to say which session it is drawing.
    """
    if len(argv) > index and argv[index].strip():
        return argv[index].strip()
    if env := os.environ.get(contract_mod.SESSION_ID_ENV, "").strip():
        return env
    if sys.stdin.isatty():
        return ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return ""
    found = payload.get("session_id") if isinstance(payload, dict) else None
    return found.strip() if isinstance(found, str) else ""


def main(argv: list[str]) -> int:
    """`report_page.py write [session-id]` — prints one absolute path.

    `report_page.py link [session-id]` prints the status-line form instead, and
    exits 0 whatever happens: a status line that errors puts a traceback where
    the model's context window is supposed to be.

    `write` refuses rather than guesses, same as `contract.py path`: with no
    session id and no `CLAUDE_CODE_SESSION_ID` there is no way to name the right
    file, and a made-up name writes a page nobody will ever open. `link` stays
    quiet in that case instead, for the same reason it is quiet with no contract.
    """
    if argv and argv[0] == "link":
        try:
            if session_id := _session_id_from(argv, 1):
                if line := status_line(session_id):
                    print(line)
        except Exception:
            pass
        return 0

    if not argv or argv[0] != "write":
        print("usage: report_page.py write|link [session-id]", file=sys.stderr)
        return 2

    session_id = argv[1] if len(argv) > 1 else os.environ.get(contract_mod.SESSION_ID_ENV, "")
    session_id = session_id.strip()
    if not session_id:
        print(
            f"report_page: no session id — ${contract_mod.SESSION_ID_ENV} is unset and"
            " none was given. Pass it: report_page.py write <session-id>",
            file=sys.stderr,
        )
        return 1

    print(write(session_id))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
