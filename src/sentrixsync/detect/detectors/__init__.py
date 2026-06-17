"""Built-in detector plugins. Importing this package registers them."""
from __future__ import annotations

from .tactile_tap import TactileTapDetector
from .visual_flash import VisualFlashDetector

__all__ = ["TactileTapDetector", "VisualFlashDetector"]
