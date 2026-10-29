---
description: "Adversarially verify a change, plan or diagnosis in cookie-cutter-dbx before trusting it. Triggers: /verify, \"is this correct\", \"did I break anything\", \"review before I merge\", \"audit this fix\", \"check this plan\", \"pre-flight this PR\", \"double-check this\", \"find what's wrong with this\"."
argument-hint: "<path to an artifact | 'diff' | 'branch' | issue number>"
allowed-tools: Read, Glob, Grep, Bash, Agent, Write
---

# /verify — adversarial verification

Invoke the **`task-verification`** skill on `$ARGUMENTS`.

**Read-only.** Finding a problem is this command's job; fixing it is `/bug-fix` or
`/implement`.

With no argument, default to verifying the working tree: `git diff` plus
`git diff --cached`.

- **Author framing is metadata.** Issue priority, a confident summary, who wrote it —
  redact all of it before judging (P0 #11). Verify the reasoning; do not ratify it.
- **Form your own expectation first**, from the request and the diff, *before* reading
  the author's justification.
- **Check the diff against the claim** both ways: described-but-absent, and
  present-but-undescribed. Both are findings.
- **Re-read every cited `file:line`** and classify any mismatch as **stale** (moved) or
  **hallucinated** (never existed). **Grep every symbol** the work claims exists.
- **Run it:** `make test` and `make lint`, transcripts pasted. For a packaging or
  dependency change, `make test-wheel-locally` — the only thing that catches a module
  missing from the wheel.
- **Inspect coverage of the changed lines**, do not assume it:
  `python -m pytest --cov=src --cov-report=term-missing`. `MINIMUM_TEST_COVERAGE_PERCENT`
  (90) gates the total only, so you have to look at the changed lines. A green suite with
  them uncovered proves nothing.
- **Assess whether a new test would fail without the fix.** If you can cheaply revert,
  confirm the failure and restore; otherwise state that its discriminating power is unverified.
- **Audit for reward hacking** (P0 #10): a weakened or deleted test, a loosened
  assertion, a new `# noqa` or ignore, a moved coverage floor.
- **Audit the public surface** (P0 #5): changes to `__init__.py`, altered signatures,
  a parameter made required, a changed return type — breaking regardless of what the
  summary says.
- **Write a minority report** before any PASS. Automation bias runs toward approval.
- **PASS needs ≥1 STRONG discriminating signal** per critical claim. `INCONCLUSIVE`
  beats a fabricated PASS.

Separate correctness findings from cleanliness findings and label which is which.

End with `In one sentence: verdict = <X>; confidence = <0.NN>; <N> findings.`
