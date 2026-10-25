"""Screen tutor, server side (docs/SCREEN-TUTOR.md): the overlay helper's endpoint, hub and per-helper session.

Phase 11: `/ws/screen/{sid}` (localhost only), the connected helpers, and dev routes to push `OverlayEvent`s.
Phase 12: perception (`session.ScreenSession`: AX snapshots, frames with a dHash change gate, input events).
Phase 13: voice over the same socket and the guide loop (`guide/`) driving the overlay.
"""

from .hub import Helper, ScreenHub, check_event
from .routes import install, refuse_reason

__all__ = ["Helper", "ScreenHub", "check_event", "install", "refuse_reason"]
