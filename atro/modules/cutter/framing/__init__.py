"""Framing strategy registry and factory."""

from __future__ import annotations

from atro.core.schemas import AspectRatio, FramingMode
from atro.modules.cutter.framing.base import FramingContext, FramingPlan, FramingStrategy
from atro.modules.cutter.framing.center_crop import CenterCrop9x16Strategy
from atro.modules.cutter.framing.face_tracking import (
    FaceTracking9x16Strategy,
    FitAllBlur9x16Strategy,
)
from atro.modules.cutter.framing.passthrough_16x9 import Original16x9Strategy


def get_framing_strategy(aspect_ratio: AspectRatio, framing_mode: FramingMode) -> FramingStrategy:
    """Resolve FramingStrategy instance from aspect ratio and framing mode."""
    if aspect_ratio == "16:9":
        return Original16x9Strategy()
    if framing_mode == "center_crop":
        return CenterCrop9x16Strategy()
    if framing_mode == "fit_all_blur":
        return FitAllBlur9x16Strategy()
    return FaceTracking9x16Strategy()


__all__ = [
    "FramingContext",
    "FramingPlan",
    "FramingStrategy",
    "Original16x9Strategy",
    "CenterCrop9x16Strategy",
    "FitAllBlur9x16Strategy",
    "FaceTracking9x16Strategy",
    "get_framing_strategy",
]
