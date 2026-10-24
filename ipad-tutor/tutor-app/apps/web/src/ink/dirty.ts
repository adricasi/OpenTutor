// Dirty-region tracker: strokes added/removed since the last ink.update, flushed after pen-up + debounce.
// A new pen-down cancels the pending flush (the debounce restarts at the next pen-up), so a line
// written with short pauses goes out as one batch — same batching as the server's fixture replay.
import type { InkUpdate, PageRef, Stroke } from "../protocol";

export class DirtyTracker {
  private changed = new Map<string, Stroke>();
  private removed = new Set<string>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sentPages = new Set<string>();

  constructor(
    private send: (msg: InkUpdate) => void,
    private debounceMs = 700,
  ) {}

  penDown(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  add(s: Stroke): void {
    this.removed.delete(s.id);
    this.changed.set(s.id, s);
  }

  remove(id: string): void {
    if (this.changed.has(id)) this.changed.delete(id); // never sent → nothing to remove
    else this.removed.add(id);
  }

  penUp(page: PageRef): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(page), this.debounceMs);
  }

  get pending(): boolean {
    return this.changed.size > 0 || this.removed.size > 0;
  }

  flush(page: PageRef): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (!this.pending) return;
    const msg: InkUpdate = {
      type: "ink.update",
      page_id: page.id,
      strokes_changed: [...this.changed.values()],
      strokes_removed: [...this.removed],
      page: this.sentPages.has(page.id) ? null : page,
    };
    this.sentPages.add(page.id);
    this.changed.clear();
    this.removed.clear();
    this.send(msg);
  }
}
