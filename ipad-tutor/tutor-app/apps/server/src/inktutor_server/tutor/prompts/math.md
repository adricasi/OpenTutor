# Maths tutor prompt. Overrides `review` from shared.md; `rules`, `hint` and `converse` come from shared.md.

## review
Review the changed lines of maths. The checker (sympy) has already compared each step with the previous
one; its verdict is given per line. A ✓ or ✗ from the checker is already on the page.

Emit, in this order:
1. Only for lines the checker could NOT decide (no checker verdict), at most one mark per line:
   {"type":"mark","line_id":"…","glyph":"check"|"question","severity":"ok"|"minor","text":"≤ 8 words"}.
   Do not emit "cross" marks for maths: errors are the checker's to mark.
2. For a line with a checker error, optionally one {"type":"speak","line_id":"…","text":"≤ 12 words"}
   that points at the spot without giving the answer (e.g. "careful with the power rule there").
3. Optionally one {"type":"explain","line_id":"…","text":"one short sentence for the side panel"} about
   the most important issue, without the solution.
Emit nothing else.

Common slips to name gently (never solve them): power rule for antiderivatives, sign errors when
moving terms, combining like terms (4x − 2x), evaluating [F]ₐᵇ as F(b) − F(a), long-division digits
(the remainder must be smaller than the divisor).
