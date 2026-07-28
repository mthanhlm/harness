---
name: switch
description: Turn the harness gates on or off, or show whether they are currently active. Use when the harness is blocking work you need to get done, when you want to check whether hooks are running, or when the user says the harness is being annoying.
argument-hint: "[on|off|status]"
disable-model-invocation: false
allowed-tools: Bash
---

# Harness kill switch

The harness state directory is `$CLAUDE_PLUGIN_DATA`. Gates are disabled when
either the env var `HARNESS_OFF=1` is set or the file `$CLAUDE_PLUGIN_DATA/off`
exists.

Argument given: `$ARGUMENTS` (default to `status` when empty).

## status

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/switch.py" status
```

Report the result plainly: which gates are active, and where state lives.

## off

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/switch.py" off
```

**This is global and it does not expire.** The marker is one file in the plugin's
own data directory, not something per repository — so switching off because one
project is being obstructive silently disables the harness in every other project
too, until someone turns it back on. Say so at the time; a user who does not know
this discovers it weeks later as "the harness stopped working".

Every hook exits immediately after this. Tell the user the harness is off and that
`/harness:switch on` restores it. Do not lecture them about turning it back on.

## on

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/switch.py" on
```

Confirm gates are active again.

## When the user is frustrated

If they reached for this because a gate blocked something legitimate, that is a bug
in the gate, not a user error. After switching off, ask which check fired and offer
to narrow it. A gate that blocks correct work is worse than no gate — it gets the
whole harness disabled, which is what happened to their previous git hook.
