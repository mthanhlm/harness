> Evaluation and observability judgement — how you know a system works, which
> metric answers which question, why Pass@k and Pass^k are not interchangeable,
> and what to instrument so a failure is diagnosable. Load it on evals,
> benchmarks, metrics, tracing and logging.
>
> Domain: evaluation and observability

# Evaluation lens

Two halves of one question — *how do you know it works?* Evaluation answers it
before shipping, observability after. Both fail the same way: a number that looks
like a measurement and is not.

The governing rule:

> **A measurement you cannot act on is a cost, not information.** Before adding
> any metric, dashboard or eval case, say what decision it would change.

## The mistake that invalidates everything else

**Grading on what the system said instead of what it did.**

An agent reporting "the booking is complete" is trajectory-level information. A
row appearing in the database is outcome-level verification. They are different,
and only one of them is true.

- Grade the trajectory only → you miss *said it but did not do it*, which is the
  most common agent failure and the one users notice fastest.
- Grade the outcome only → you miss intermediate steps that went badly, and you
  cannot diagnose *why* a failure happened.

**Cover both.** In practice: assert on final state (the row, the file, the API
response) *and* keep the trajectory for diagnosis.

There is a corollary that catches people out. Anthropic's example: a flight
booking agent found a policy loophole and got the user a cheaper fare. Scored
against the expected execution path, that run fails. Scored on outcome, it is
better than expected. **Grading the path punishes the good surprises.**

## Pass@k and Pass^k answer opposite questions

The most consequential distinction in this document, and they are constantly
confused.

- **Pass@k** — at least one of k attempts succeeds. *Can it do this at all?*
- **Pass^k** — all k attempts succeed. *Is it reliable?*
- **Best@k** — the score of the best of k. The quality ceiling given enough tries.

The numbers make it vivid. At a 60% single-attempt success rate:

| Metric | Value | Reads as |
|---|---|---|
| Pass@1 | 0.60 | |
| Pass@5 | 1 − 0.4⁵ ≈ **0.99** | "basically always works" |
| Pass^5 | 0.6⁵ ≈ **0.08** | "works one time in twelve" |

Same system. Same underlying rate. **A 91-point gap, from choosing a metric.**

| You want to know | Use | If you use the other |
|---|---|---|
| Is this stable? (regression testing) | **Pass^k** | Pass@k hides instability — a system succeeding once in five reads as "pass" |
| What is the ceiling? (exploratory) | **Pass@k** or Best@k | Pass^k flags normal variance as failure — every change looks like a regression |

**Tell in review**: a regression suite reporting Pass@k. It will go green while
the system becomes unreliable, which is the exact failure a regression suite
exists to prevent.

## A test dataset that cannot discriminate

The most common defect in an eval set is that a system doing nothing scores well.

**Two properties, both required:**

1. The visible checks **pass on the pristine fixture** — so the harness itself is
   not broken.
2. The hidden grader **fails on the pristine fixture** — so a system that changed
   nothing cannot pass by accident.

Check both mechanically, without spending on a model. An eval case that has never
been verified to fail is not a test; it is a formality.

Beyond that:

- **Include cases you expect to fail.** A suite at 100% has stopped measuring.
- **Cover boundaries, not variations.** Three cases at the edges beat twenty near
  the middle. Near-duplicates cost tokens and tell you nothing new.
- **Separate the set you tune on from the set you report on.** Iterating against
  your reported set is how you fit the benchmark instead of the problem.
- **Keep a retention set.** A change that improves the target and regresses
  everything else is a loss. This is the gate: *improve the boundary set, do not
  regress the retention set.*

## Statistical significance, briefly and bluntly

Most agent evals are run on too few cases to support the conclusions drawn from
them.

- **20 cases cannot distinguish 70% from 80%.** The confidence intervals overlap
  completely. Reporting the difference as an improvement is noise wearing a
  number.
- **Report the interval, not just the point.** "74% (n=50, 95% CI 60–85%)" is
  honest. "74%" invites a decision the data does not support.
- **Non-determinism means repeat runs.** One run at temperature > 0 is a sample
  of size one from a distribution you have not characterised.
- **State n every single time.** A percentage with no denominator is not a
  measurement.

When the sample is too small to prove causation, **say so and act on it anyway if
it is the best evidence available** — but never launder it into a claim. "n=7 is
too small to prove this; it points the same way as the reasoning" is a legitimate
and useful sentence.

## Process metrics, for when the outcome is not enough

Outcome tells you whether it worked. Process tells you why, and where the money
went.

| Metric | What it catches |
|---|---|
| **Action validity** | calling tools that do not exist, wrong parameter types |
| **Tool-call correctness** | valid call, semantically wrong arguments — the search query that does not express the need |
| **Step count** | needs a human or heuristic baseline to mean anything |
| **Redundant actions** | re-reading the same file, re-running the same search. Cheap to detect, strongly correlated with waste |
| **Backtracking rate** | occasional is healthy; frequent means poor forward planning |
| **Retrieval coverage** | stopped after the first page of results |
| **Cost and latency** | split input/output, account for cache reads, and track where the wall-clock goes |

**Redundant actions and backtracking are the two worth instrumenting first.**
They are mechanical to detect and they point directly at prompt or tool problems.

## LLM-as-judge: calibrate it or it is just another opinion

A judge model is a measuring instrument. An uncalibrated instrument produces
numbers, not measurements.

    1. Build a human-annotated gold set (100–200 cases, spanning types and difficulty)
    2. Measure judge-vs-human agreement — Cohen's kappa, which discounts chance
    3. kappa >= ~0.7  → usable at scale
       kappa <  ~0.7  → the rubric is the problem, not the judge. Fix it and remeasure
    4. Recalibrate whenever the judge model OR the rubric changes

- **Zero-tolerance vetoes.** Some failures — leaked credentials, deleted data,
  fabricated facts — must veto the whole run regardless of the other scores.
  Averaging them away is how a dangerous system scores well.
- **Independent judges, then aggregate.** Several judges scoring separately, with
  disagreement flagged for human review, beats one judge. Judges that see each
  other's scores converge without adding information.
- **A judge from the same family shares the blind spot.** It cannot catch the
  error it would have made itself. This is a real limit, not a tuning problem.
- **Red-team your own grader.** Construct answers that should score badly and
  do not: keyword stuffing, confident wrong answers, plausible-looking output with
  a buried error.

## Ablation beats intuition

When a system performs badly, the instinct is to rewrite the prompt. The
measured better move is to **turn off one component at a time and see which one
matters.**

The finding this rests on is worth stating exactly: in a controlled ablation on
Tau-Bench, keeping all the same rule content but removing the hierarchy and
converting an ordered process into an unstructured list dropped task success by
**over 30%**. Changing tone and style barely moved it.

Two lessons:

1. **Information organisation is worth more than wording.** A model asked to find
   the applicable rule in a flat list often does not.
2. **Ablation localises the problem.** Rewriting everything changes many
   variables and teaches you nothing about which one was load-bearing.

The same discipline applies to test suites: **deliberately break the
implementation and confirm the tests go red.** A test that cannot fail is not
coverage, and it reads exactly like coverage in a report.

## Observability: three signals, different jobs

| Signal | Answers | Cardinality |
|---|---|---|
| **Metrics** | is something wrong, right now? | must stay low |
| **Logs** | what happened to *this* request? | high, sampled |
| **Traces** | where did the time go across services? | high, sampled |

- **High cardinality kills metrics.** A label containing a user id or request id
  creates a time series per value and takes down the metrics backend. Ids belong
  in logs and traces.
- **Structured logs or nothing.** `logger.info("user %s failed", id)` cannot be
  queried. `logger.info("payment_failed", user_id=id, reason=r)` can.
- **One correlation id, propagated everywhere.** Without it, debugging a
  multi-service failure means guessing by timestamp.
- **Log the decision, not just the outcome.** "Chose route B because churn=4" is
  diagnosable. "Chose route B" is not — and gates that silently do nothing look
  exactly like gates that found nothing.

### Alert on symptoms, page on the actionable

- **Alert on what a user feels**: error rate, latency percentiles, queue depth,
  freshness. Not CPU — it is high when nothing is wrong and normal during many
  real outages.
- **Percentiles, never averages.** A mean latency of 200ms is consistent with 5%
  of users waiting 10 seconds. Track p50, p95, p99.
- **If a page has no action, it is not a page.** Every alert needs a documented
  response. An alert people routinely ignore has trained them to ignore alerts.
- **Watch the thing that is supposed to be zero.** Dead-letter depth, reconciliation
  mismatches, failed-and-not-retried counts.

## SLOs, if you use them at all

An SLO is a decision rule, not a slogan: *at what point do we stop shipping
features and fix reliability?*

- Pick the indicator users actually feel.
- Set the target below 100%. 100% means never deploying.
- **The error budget is the point.** Budget remaining → ship. Budget exhausted →
  reliability work until it recovers.

An SLO with no consequence attached is a number on a wall.

## Review checklist

1. Does the eval grade final state, or only what the system claimed?
2. Regression testing — is it Pass^k, not Pass@k?
3. Has each case been verified to fail on the unmodified fixture?
4. Is n reported, and is it big enough for the claim being made?
5. Is the set being tuned on the same as the set being reported?
6. Is the LLM judge calibrated against human labels, with a kappa?
7. Do safety failures veto, or get averaged away?
8. Does any metric label carry unbounded cardinality?
9. Is there one correlation id across services?
10. Does every alert have a documented action, and does anything alert on CPU?
