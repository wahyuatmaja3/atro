"""Passthrough framing strategy for 16:9 landscape clips."""

from __future__ import annotations

from atro.modules.cutter.framing.base import FramingContext, FramingStrategy


class Original16x9Strategy(FramingStrategy):
    """Preserves original landscape framing while guaranteeing even H.264 dimensions."""

    name = "passthrough_16x9"

    async def build_video_filter(self, ctx: FramingContext) -> str:
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
