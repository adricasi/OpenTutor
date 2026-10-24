"""Screen tutor guide loop (docs/SCREEN-TUTOR.md): planner, stepper, locating, `done_when`, replanning, hint ladder."""

from .config import GuideConfig, load_config, recipe_for
from .loop import Guide, TutorUnavailable, parse_bbox, plain_error
from .plan import parse_step

__all__ = [
    "Guide",
    "GuideConfig",
    "TutorUnavailable",
    "load_config",
    "parse_bbox",
    "parse_step",
    "plain_error",
    "recipe_for",
]
