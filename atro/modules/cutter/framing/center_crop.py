"""Center-crop framing strategy for 9:16 vertical short-form clips."""

from __future__ import annotations

from atro.modules.cutter.framing.base import FramingContext, FramingStrategy


class CenterCrop9x16Strategy(FramingStrategy):
    """Crops the horizontal center of source video into crisp 1080x1920 (9:16)."""

    name = "center_crop"

    @staticmethod
    def compute_crop_rect(source_width: int, source_height: int) -> tuple[int, int, int, int]:
        """Compute even-dimension (crop_w, crop_h, crop_x, crop_y) for 9:16 output."""
        w = max(2, int(source_width))
        h = max(2, int(source_height))
        target_ratio = 9.0 / 16.0

        if (w / h) > target_ratio:
            crop_h = max(2, h - (h % 2))
            crop_w = int(crop_h * target_ratio)
            crop_w = max(2, crop_w - (crop_w % 2))
            crop_w = min(crop_w, max(2, w - (w % 2)))
        else:
            crop_w = max(2, w - (w % 2))
            crop_h = int(crop_w * (16.0 / 9.0))
            crop_h = max(2, crop_h - (crop_h % 2))
            crop_h = min(crop_h, max(2, h - (h % 2)))

        crop_x = max(0, (w - crop_w) // 2)
        crop_y = max(0, (h - crop_h) // 2)
        return crop_w, crop_h, crop_x, crop_y

    async def build_video_filter(self, ctx: FramingContext) -> str:
        crop_w, crop_h, crop_x, crop_y = self.compute_crop_rect(
            ctx.source_width, ctx.source_height
        )
        return f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=1080:1920:flags=lanczos,setsar=1"
