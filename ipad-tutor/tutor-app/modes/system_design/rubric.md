A good design shows the request path end to end (client → edge → services → data), names how each part
scales, and says what happens when any one part fails. Common gaps: no cache in front of a hot database,
no load balancer in front of several app servers, a single database with no replica, synchronous calls
where a queue would absorb spikes, no rate limiting at the edge.

Hint ladder:
1. Nudge: ask about load or failure at the spot ("what happens at 10× traffic here?").
2. Hint: name the pattern (cache, replica, queue, load balancer).
3. Corrected step: a dashed ghost box/arrow where the component goes.
4. Full solution: a reference design beside the page, only on request.
