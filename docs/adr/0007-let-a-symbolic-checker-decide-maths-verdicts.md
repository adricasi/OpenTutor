# 0007. Let a deterministic symbolic checker decide Maths verdicts

- Status: Proposed
- Date: 2026-10-24
- Deciders: OpenTutor team

## Context
Attempt checking (REQ-006) needs ≥ 90 % accuracy for Maths (NFR-011) from a small local
model, which can be wrong on algebra. `tutor-ipad` (same repository) checks maths with
sympy and lets the model only explain (its DECISIONS #5, #19); its checker finds the
first step of a chain that is not equivalent to the previous one
(`tutor-ipad/tutor-app/apps/server/src/inktutor_server/checkers/math.py`, `check_chain`,
`equal`, `check_equations`). It parses LaTeX; OpenTutor students type plain text.

## Options considered
1. **Model verdict only** — Pros: one path for every subject. Cons: accuracy limited by
   the small model; wrong verdicts teach wrong maths.
2. **Symbolic checker decides when it can, model explains; model verdict otherwise** —
   Pros: exact verdicts on the common algebra/arithmetic cases; points to the first wrong
   step deterministically. Cons: parser for plain-text maths; sympy can be slow on
   adversarial input.
3. **Model verdict, checker as a second opinion** — Pros: simpler merge. Cons: still
   shows wrong verdicts when they disagree.

## Decision
We will use option 2 (AC-006.6 … AC-006.9): adapt `tutor-ipad`'s sympy checker to parse
plain-text maths with `sympy.parse_expr`. Because `parse_expr` evaluates its input
internally (sympy's documentation warns against unsanitised input), every attempt first
passes our own strict allow-list tokenizer (digits, a small set of single-letter
variables, `+ - * / ^ ( ) = . ,`, whitelisted function names such as `sqrt`) and a length
cap, and the checker runs in a separate worker process with a 2-second limit, and let its
verdict override the model's for Maths; the model writes the explanation within that
verdict.

## Consequences
- Positive: deterministic, testable Maths verdicts; higher check accuracy.
- Negative / risks: parsing untrusted input with an `eval`-based parser → allow-list
  tokenizer before `parse_expr`, length cap, process isolation and timeout, plus tests
  with hostile inputs; only Maths benefits — English language and Science stay
  model-checked (evaluation targets differ, NFR-011).
- Follow-ups: T-010; dependency `sympy` (already used by `tutor-ipad`).
