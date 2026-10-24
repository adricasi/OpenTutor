// Toolbar overflow: on wide screens the children sit inline in the toolbar (the wrapper is `display: contents`);
// below 1400 px they move into a "More" dropdown so nothing is pushed off-screen. Rendered once either way.
import { useEffect, useRef, useState, type ReactNode } from "react";

export function MoreMenu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", away, true);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away, true); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className={`more${open ? " open" : ""}`} ref={ref}>
      <button className="more-btn" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="more">
        More <span aria-hidden="true">▾</span>
      </button>
      {/* a tap on an item closes the menu; the page picker stays open until a page is picked */}
      <div className="more-group" data-testid="more-group"
        onClick={(e) => { if ((e.target as Element).closest("button, label")) setOpen(false); }}
        onChange={() => setOpen(false)}>
        {children}
      </div>
    </div>
  );
}
