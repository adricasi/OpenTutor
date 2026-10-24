"""Model runtime: owns local models (tier 1 in-process, tier 2 via the mlx-vlm server) and the memory guard."""

from .runtime import Runtime

__all__ = ["Runtime"]
