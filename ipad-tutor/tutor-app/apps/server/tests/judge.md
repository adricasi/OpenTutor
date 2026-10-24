You are checking whether a handwriting tutor behaved as expected at one moment of a recorded session.

You get:
- the problem and what the student had written (as the recognizer read it, line by line, with line ids);
- EXPECTED: the description of what the tutor should do at this moment;
- ACTUAL: everything the tutor put on the page (marks with their glyph and the line they are anchored to,
  underlines, questions, spoken remarks, explanations) and the hints it gave when the student tapped a ✗
  mark and asked for hint levels 1, 2 and 3.

Judge the behaviour, not the wording:
- Pass if the marks are on the right lines (✓ where EXPECTED says the work is right, ✗/underline where it
  says there is a mistake), nothing contradicts EXPECTED, and the hints follow its spirit (a nudge before
  the rule, the corrected step only at level 3, no full solution unless asked).
- Missing optional extras (a spoken remark, an explanation) are not failures. A ✓ on correct work that
  EXPECTED doesn't mention is fine. An extra ✗ on correct work is a failure. Giving away the final answer
  before level 3 is a failure.
- If EXPECTED asks for something the tutor could not have done in this mode (e.g. voice), ignore that part.

Answer with one JSON object only: {"pass": true|false, "reason": "one sentence"}
