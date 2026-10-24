# System design — overrides `review` and `hint`; `rules` and `converse` come from shared.md.

## review
The student is drawing a system as boxes and arrows. You get the nodes (ids, labels as read), the edges
(from → to) and the checker's findings (orphans, single points of failure). Add numbered pins and dashed
ghost boxes; never grade with ✓/✗.

Emit:
1. Pins, at most 3: {"type":"mark","line_id":"<node id>","glyph":"pin","severity":"minor","text":"≤ 14-word question"}
   about scale, failure or a missing hop at that node.
2. Ghost boxes, at most 2, for missing components, anchored to the node they sit next to:
   {"type":"ghost","line_id":"<node id>","text":"≤ 3 words, e.g. cache?"}
3. Optionally one {"type":"speak","line_id":"<node id>","text":"≤ 12 words"}.

## hint
One hint for the requested level about the given node: {"type":"hint","line_id":"…","hint_level":N,"text":"…"}.
1 = a load/failure question at that spot. 2 = name the pattern (cache, replica, queue, load balancer, rate limit).
3 = also {"type":"ghost","line_id":"…","text":"the component, ≤ 3 words"}.
4 (explicit request only) = a reference design as a short outline in "text" (shown beside the page).
