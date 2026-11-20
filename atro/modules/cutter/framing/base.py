"""Abstract base class and context for modular aspect-ratio framing strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from atro.core.schemas import AspectRatio


@dataclass(slots=True)
class FramingContext:
    """Input metadata provided to a FramingStrategy for a single clip."""

    source_path: Path
    source_width: int
    source_height: int
    fps: float
    start_sec: float
    end_sec: float
    aspect_ratio: AspectRatio


@dataclass(slots=True)
class FramingPlan:
    """FFmpeg filter_complex graph fragment turning `[0:v]` into `[output_label]`."""

    filter_lines: list[str]
    output_label: str = "framed"
    output_width: int = 1080
    output_height: int = 1920
    summary: str = ""


class FramingStrategy(ABC):
    """Strategy interface for generating FFmpeg video filter chains."""

    name: str = "base"

    @abstractmethod
    async def build_video_filter(self, ctx: FramingContext) -> str:
        """Return an FFmpeg `-vf` filter graph string for the target clip."""

    async def build_framing_plan(self, ctx: FramingContext) -> FramingPlan:
        """Return a filter_complex FramingPlan turning `[0:v]` into `[framed]`."""
        vf = await self.build_video_filter(ctx)
        if ctx.aspect_ratio == "16:9":
            out_w = max(2, ctx.source_width - (ctx.source_width % 2))
            out_h = max(2, ctx.source_height - (ctx.source_height % 2))
        else:
            out_w, out_h = 1080, 1920
        return FramingPlan(
            filter_lines=[f"[0:v]{vf}[framed]"],
            output_label="framed",
            output_width=out_w,
            output_height=out_h,
            summary=self.name,
        )
