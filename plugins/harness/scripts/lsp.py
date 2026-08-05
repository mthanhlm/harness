#!/usr/bin/env python3
"""What the language servers in `.lsp.json` give this repo, and what is missing.

Reading code is most of the job, and until now every agent here did it with
grep. Grep finds a name; it cannot tell a definition from a mention, cannot
follow a call through an interface, and cannot answer "who else uses this"
without reading every hit. A language server answers all three from an index the
compiler already built. That is why `.lsp.json` exists.

Two decisions in that file are deliberate and neither is obvious.

**`diagnostics: false` on every server.** The default is `true`, which pushes
every diagnostic for a file into the context after each edit — including the
errors that were already there. This plugin's per-edit check runs the same
compilers and deliberately reports only what the edit caused, because the
session context it writes says, in as many words, *do not go fixing unrelated
problems you did not cause*. A second, unfiltered stream of the same errors
contradicts that instruction on every edit. So: navigation on, diagnostics off,
and the existing check keeps the job it already does better.

**Servers are declared for languages the official marketplace covers.** The docs
steer plugin authors to uncovered languages, and for `gopls` that is what this
is. For TypeScript, Python and Rust it is a real overlap with `typescript-lsp`,
`pyright-lsp` and `rust-analyzer-lsp` — taken knowingly, because those ship the
default `diagnostics: true` and would reintroduce exactly the noise above. Only
one server can own an extension: when two are enabled the first registered wins
and the other never starts, so enabling an official LSP plugin alongside this
one gives an unpredictable winner. `/plugin` names whichever is active.

The binary is never bundled. A server whose command is not on `PATH` is skipped,
and the reason is visible only in the `/plugin` Errors tab — which nobody opens.
So this module exists to say it once, at session start, in the one place it is
actionable: when the repo is Python and `pyright-langserver` is missing.
"""

from __future__ import annotations

import json
import shutil

from state import plugin_root

# Which server serves a language `detect.py` reports. A JavaScript repo is
# served by the TypeScript server — that is what `typescript-language-server` is
# for, and `.lsp.json` claims `.js` accordingly.
SERVER_FOR_LANGUAGE = {
    "typescript": "typescript",
    "javascript": "typescript",
    "python": "python",
    "go": "go",
    "rust": "rust",
}

# The one command that installs each. Naming the command is the whole point of
# the advisory: "install a language server" is not actionable and gets ignored,
# and the alternative is the user reading the LSP docs to find a line this
# module already knows.
INSTALL = {
    "typescript-language-server": "npm install -g typescript-language-server typescript",
    "pyright-langserver": "npm install -g pyright",
    "gopls": "go install golang.org/x/tools/gopls@latest",
    "rust-analyzer": "rustup component add rust-analyzer",
}


def servers() -> dict[str, dict]:
    """The declared servers, or an empty mapping if the file is unreadable.

    Unreadable is not worth an exception. This is read on the way into a session
    banner; a plugin that fails to start because a config file it only uses for
    advice went missing is worse than one that gives no advice.
    """
    path = plugin_root() / ".lsp.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def installed(command: str) -> bool:
    return bool(shutil.which(command))


def missing(profile: dict) -> list[tuple[str, str]]:
    """`(language, install command)` for each of this repo's languages with no
    server binary on `PATH`.

    Scoped to the languages actually detected in this repo. A Python developer
    does not need to be told about `gopls`, and an advisory that lists four
    installs for a one-language repo is one the reader learns to skip.
    """
    declared = servers()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for language in profile.get("languages") or []:
        name = SERVER_FOR_LANGUAGE.get(language)
        config = declared.get(name) if name else None
        if not config or name in seen:
            continue
        seen.add(name)

        command = config.get("command")
        if command and not installed(command):
            out.append((language, INSTALL.get(command, f"install `{command}`")))
    return out


def advisory(profile: dict) -> str:
    """One line for the session banner, or nothing.

    Nothing is the common case and it is the important one. The banner is loaded
    into every session, and the documented failure of always-on context is that
    a long one makes its own important lines get skimmed. A repo whose servers
    are installed says nothing here, because there is nothing to do.
    """
    absent = missing(profile)
    if not absent:
        return ""

    if len(absent) == 1:
        language, command = absent[0]
        return (
            f"No language server for {language}, so code navigation falls back to"
            f" grep. `{command}` enables go-to-definition and find-references."
        )

    listed = "; ".join(f"{language}: `{command}`" for language, command in absent)
    return (
        "No language server for this repo's languages, so code navigation falls"
        f" back to grep. To enable go-to-definition and find-references — {listed}."
    )
