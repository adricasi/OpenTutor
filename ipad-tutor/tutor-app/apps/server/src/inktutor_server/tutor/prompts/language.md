# Language — overrides `review`; `rules`, `hint` and `converse` come from shared.md.

## review
The student writes in a foreign language by hand. For each changed line (with its id and the text as read):
1. For each wrong word (at most 3 per line), a squiggle under that word:
   {"type":"mark","line_id":"…","glyph":"underline","severity":"minor","word":"<the word exactly as read>","text":"≤ 6 words, the kind of error"}
2. A line with no mistakes: {"type":"mark","line_id":"…","glyph":"check","severity":"ok","text":""}
3. If the reading may be wrong (a word you can't trust), a question instead of a mark:
   {"type":"question","line_id":"…","text":"Did you write “…”?","choices":["Yes","No"]}
4. Optionally one {"type":"speak","line_id":"…","text":"≤ 12 words"}.
Never give the corrected sentence here (that is hint level 3).
