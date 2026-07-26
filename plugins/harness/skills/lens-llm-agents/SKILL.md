---
name: lens-llm-agents
description: LLM and agent system judgement — prompt and tool design, context economics, non-determinism, evals and failure handling. Loads automatically on prompt, agent, skill and tool definition files, and anywhere an Anthropic or OpenAI SDK is used.
paths:
  - "**/prompts/**"
  - "**/agents/**"
  - "**/skills/**"
  - "**/*SKILL.md"
  - "**/*agent*.py"
  - "**/*agent*.ts"
  - "**/evals/**"
user-invocable: false
---

# LLM and agent systems lens

## Context is the budget everything else spends from

Model quality degrades as the window fills, so context is a resource to be spent
deliberately rather than filled. Load reference material on demand instead of
pinning it; push wide file-reading into subagents that report back a conclusion
rather than their raw reading; keep always-on instructions short, because a long
instruction file causes the important lines to be ignored rather than obeyed.

## Tool definitions are prompts

A tool's description is the only thing the model has when deciding whether to
call it. Say what it does *and when to use it* — a description that reads like
an API doc produces a tool that gets called at the wrong moment or never. Narrow
parameters beat a free-form string, since an enum cannot be hallucinated.

Errors returned to a model should say what to do differently. "Invalid input" is
a dead end; "expected an ISO date, got '3 days ago'" gets fixed on the retry.

## Design for output that varies

The same prompt does not produce the same output twice. Anything downstream must
either tolerate variation or constrain it — a schema the call is validated
against, not a regex over prose. Parse defensively, and decide what happens on a
malformed response before shipping, because it will happen.

Retries need a ceiling. An agent loop with no turn limit and no cost ceiling is
an unbounded bill.

## Evals or it is guesswork

A prompt change that "seems better" is unmeasured. Keep a small set of cases
with known-good outcomes and run them before and after. A handful of real cases
beats a large synthetic set, and a baseline arm — the same cases without the
change — is what turns an impression into a number.

## Cheap models fail at underspecification, not at difficulty

A smaller model given a precise contract and a check it can run will often match
a larger one given a vague instruction. Before reaching for a bigger model,
check whether the task is actually underspecified — that is usually cheaper to
fix, and fixing it helps the larger model too.

## Before adding a prompt, tool or agent

Read the ones already there and match their structure. Check whether an existing
tool covers this with one more parameter: a near-duplicate tool makes the
model's selection problem harder, which degrades every call, not just this one.
