"""Pydantic v2 JSON configuration contract shared by ATRO Live Preview and FFmpeg Render Engine."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from atro.core.schemas import TranscriptWord

ComposerLayoutMode = Literal[
    "single_focus",
    "split_screen",
    "blurred_frame",
    "multi_speaker_split",
    "pip_facecam",
]
PipShape = Literal["circle", "rounded_rect"]
PipPosition = Literal["top_right", "top_left", "bottom_right", "bottom_left"]
CaptionBgMode = Literal["none", "active_word_pill", "full_line_box"]
CaptionRecipeId = Literal[
    "hormozi_viral",
    "mrbeast_pill",
    "lokaclip_neon",
    "mageran_glass",
    "abdaal_minimal",
    "gamer_split",
    "streamer_kinetic",
    "podcast_editorial",
]
BlurredFrameAspect = Literal["1:1", "4:3", "16:9"]
SubtitleStyleMode = Literal["karaoke_highlight", "kinetic_1_2", "podcast_3_4", "plain"]
SubtitlePlacementPreset = Literal["bottom_safe", "split_line_center", "top_safe", "above_speaker", "custom"]
SubtitleAnimationMode = Literal["pop", "scale", "karaoke", "none"]
HookTransitionMode = Literal["fade", "slide", "zoom", "whip", "none"]
HookDesignStyle = Literal[
    "viral_sticker",
    "viral_two_tone",
    "viral_white_red",
    "toxic_lime",
    "cyan_arcade",
    "neon_poster",
    "floating_outline",
    "gold_cinema",
    "slanted_pill",
    "magenta_pop",
    "breaking_bar",
    "paper_label",
    "dark_card",
]
OpeningIntroMode = Literal["freeze_clear", "freeze_zoom", "freeze_blur"]
WatermarkPosition = Literal[
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
    "center_split",
    "custom",
]
BacksoundSelectionMode = Literal["random", "manual", "rotate"]
PresetId = Literal["podcast", "gaming_split", "twitch_comment", "subathon", "custom_mix"]


def _normalize_hex_color(value: str, fallback: str = "#FFFFFF") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    if not raw.startswith("#"):
        raw = "#" + raw
    hex_digits = raw[1:]
    if len(hex_digits) not in (6, 8):
        return fallback
    try:
        int(hex_digits, 16)
    except ValueError:
        return fallback
    return "#" + hex_digits.upper()


class ClipTimingConfig(BaseModel):
    """Clip source time window, padding, and opening voiceover timing."""

    start_time: float = Field(default=0.0, ge=0.0)
    end_time: float = Field(default=30.0, gt=0.0)
    padding_sec: float = Field(default=0.4, ge=0.0, le=2.0)
    opening_tts_enabled: bool = False
    # Jump-cut: drop long pauses and filler words (applied at render time, see modules/cutter/jump_cut.py)
    silence_trim_enabled: bool = False
    silence_max_gap_sec: float = Field(default=0.35, ge=0.10, le=2.0)
    silence_remove_fillers: bool = True

    @model_validator(mode="after")
    def _validate_window(self) -> "ClipTimingConfig":
        if self.end_time <= self.start_time:
            self.end_time = round(self.start_time + 1.0, 3)
        return self


class CropBoxConfig(BaseModel):
    """Normalized [0..1] source crop box for top/subject or bottom/gameplay panels."""

    auto_detect: bool = True
    x: float = Field(default=0.2, ge=0.0, le=0.95)
    y: float = Field(default=0.0, ge=0.0, le=0.95)
    w: float = Field(default=0.6, ge=0.05, le=1.0)
    h: float = Field(default=1.0, ge=0.05, le=1.0)
    label: str = "Source Crop"

    @model_validator(mode="after")
    def _clamp_bounds(self) -> "CropBoxConfig":
        self.x = max(0.0, min(0.95, float(self.x)))
        self.y = max(0.0, min(0.95, float(self.y)))
        self.w = max(0.05, min(1.0 - self.x, float(self.w)))
        self.h = max(0.05, min(1.0 - self.y, float(self.h)))
        return self


TransitionType = Literal[
    "none",
    "fade",
    "crossfade",
    "dip_black",
    "slide_left",
    "slide_right",
    "slide_up",
    "slide_down",
    "zoom_in",
    "zoom_out",
    "whip",
    "blur_swap",
    "glitch",
    "wipe",
]
TransitionEasing = Literal["linear", "ease_in_out", "ease_out"]
TransitionMode = Literal["fixed", "auto"]
# "none", built-in ("whoosh", "swipe", "pop") or the file stem of a custom SFX in the sfx folder
TransitionSfx = str
FocusStrategy = Literal["auto_topic", "active_speaker", "manual"]
GroupMode = Literal["focus_only", "fit_all_blur", "grid"]
ShotReason = Literal[
    "speaker_match",
    "reaction",
    "group",
    "fallback",
    "manual",
    "legacy",
]


class TransitionConfig(BaseModel):
    """Transition applied when the framing cuts between two shots."""

    type: TransitionType = "none"
    mode: TransitionMode = "fixed"
    duration_sec: float = Field(default=0.25, ge=0.10, le=0.60)
    easing: TransitionEasing = "ease_in_out"
    sfx: TransitionSfx = "none"
    sfx_volume: float = Field(default=0.5, ge=0.0, le=1.0)


class FaceAnchorBox(BaseModel):
    """Normalized [0..1] source face bounding box used to anchor speaker-following captions."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class SpeakerTurnSpan(BaseModel):
    """Clip-local diarized speaker activity window [t_start, t_end] in seconds."""

    t_start: float = Field(ge=0.0)
    t_end: float = Field(gt=0.0)
    speaker_id: str
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_span(self) -> "SpeakerTurnSpan":
        if self.t_end <= self.t_start:
            self.t_end = round(self.t_start + 0.1, 3)
        return self


class ShotCropSegment(BaseModel):
    """Per-camera-angle / per-shot crop window within clip-local time [t_start, t_end]."""

    t_start: float = Field(default=0.0, ge=0.0)
    t_end: float = Field(default=30.0, gt=0.0)
    top_crop: CropBoxConfig = Field(default_factory=CropBoxConfig)
    bottom_crop: Optional[CropBoxConfig] = None
    shot_mode: Literal["single_focus", "multi_speaker_split", "blurred_frame"] = "single_focus"
    speaker_id: Optional[str] = None
    face_id: Optional[str] = None
    reason: ShotReason = "legacy"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    transition_in: Optional[TransitionConfig] = None
    speaker_faces: dict[str, FaceAnchorBox] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_span(self) -> "ShotCropSegment":
        if self.t_end <= self.t_start:
            self.t_end = round(self.t_start + 0.2, 3)
        return self


class DividerConfig(BaseModel):
    """Split-screen separator line styling (straight or diagonal)."""

    enabled: bool = True
    thickness_px: int = Field(default=6, ge=0, le=40)
    color_hex: str = "#38BDF8"
    border_color_hex: str = "#0B0F19"
    angle_deg: float = Field(default=0.0, ge=-25.0, le=25.0)
    glow_enabled: bool = True

    @field_validator("color_hex", "border_color_hex", mode="before")
    @classmethod
    def _norm_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#38BDF8")


class LayoutLayerConfig(BaseModel):
    """Framing, split ratio, blur backdrop, divider, and manual/auto source crop boxes."""

    mode: ComposerLayoutMode = "single_focus"
    split_ratio_top: float = Field(default=0.40, ge=0.20, le=0.80)
    framing_tracking_mode: Literal["auto_face", "manual_crop"] = "auto_face"
    blurred_frame_aspect: BlurredFrameAspect = "4:3"
    background_blur_strength: int = Field(default=24, ge=2, le=60)
    background_darken: float = Field(default=-0.12, ge=-0.5, le=0.3)
    frame_corner_radius_px: int = Field(default=28, ge=0, le=120)
    frame_border_width_px: int = Field(default=4, ge=0, le=24)
    frame_border_color_hex: str = "#F97316"
    pip_shape: PipShape = "circle"
    pip_position: PipPosition = "top_right"
    pip_scale: float = Field(default=0.34, ge=0.18, le=0.55)
    divider: DividerConfig = Field(default_factory=DividerConfig)
    top_crop: CropBoxConfig = Field(
        default_factory=lambda: CropBoxConfig(
            auto_detect=True,
            x=0.05,
            y=0.05,
            w=0.35,
            h=0.45,
            label="Top / Subject",
        )
    )
    bottom_crop: CropBoxConfig = Field(
        default_factory=lambda: CropBoxConfig(
            auto_detect=True,
            x=0.20,
            y=0.0,
            w=0.60,
            h=1.0,
            label="Bottom / Gameplay",
        )
    )
    shot_crops: list[ShotCropSegment] = Field(default_factory=list)
    speaker_turns: list[SpeakerTurnSpan] = Field(default_factory=list)
    transition: TransitionConfig = Field(default_factory=TransitionConfig)
    focus_strategy: FocusStrategy = "auto_topic"
    group_mode: GroupMode = "fit_all_blur"
    min_shot_sec: float = Field(default=1.2, ge=0.3, le=5.0)
    pinned_speaker_id: Optional[str] = None
    speaker_aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("frame_border_color_hex", mode="before")
    @classmethod
    def _norm_border_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#F97316")

    def resolve_crops_at_time(self, rel_t: float) -> tuple[CropBoxConfig, CropBoxConfig]:
        """Return `(top_crop, bottom_crop)` active at clip-local `rel_t` seconds."""
        if self.framing_tracking_mode == "auto_face" and self.shot_crops:
            t = max(0.0, float(rel_t))
            chosen = self.shot_crops[-1]
            if t < self.shot_crops[0].t_start:
                chosen = self.shot_crops[0]
            else:
                for seg in self.shot_crops:
                    if seg.t_start <= t < seg.t_end:
                        chosen = seg
                        break
            bot = chosen.bottom_crop if chosen.bottom_crop is not None else self.bottom_crop
            return chosen.top_crop, bot
        return self.top_crop, self.bottom_crop


class SpeakerColorPalette(BaseModel):
    """Per-speaker typography palette for multi-speaker or action coding."""

    primary_hex: str = "#FFFFFF"
    highlight_hex: str = "#FACC15"
    outline_hex: str = "#000000"

    @field_validator("primary_hex", "highlight_hex", "outline_hex", mode="before")
    @classmethod
    def _norm_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#FFFFFF")


def _default_speaker_palettes() -> dict[str, SpeakerColorPalette]:
    return {
        "SPEAKER_1": SpeakerColorPalette(
            primary_hex="#FFFFFF",
            highlight_hex="#FACC15",
            outline_hex="#000000",
        ),
        "SPEAKER_2": SpeakerColorPalette(
            primary_hex="#E0F2FE",
            highlight_hex="#38BDF8",
            outline_hex="#082F49",
        ),
        "ACTION": SpeakerColorPalette(
            primary_hex="#FCA5A5",
            highlight_hex="#EF4444",
            outline_hex="#450A0A",
        ),
    }


class SubtitleFootnoteConfig(BaseModel):
    """Optional compact annotation note displayed directly below active captions."""

    enabled: bool = False
    text: str = ""
    font_size: int = Field(default=24, ge=12, le=60)
    color_hex: str = "#CBD5E1"
    offset_y: int = Field(default=58, ge=10, le=200)

    @field_validator("color_hex", mode="before")
    @classmethod
    def _norm_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#CBD5E1")


class SubtitleLayerConfig(BaseModel):
    """Word-level dynamic typography, color coding, placement, and animation config."""

    enabled: bool = True
    caption_recipe: str = "hormozi_viral"
    style_mode: SubtitleStyleMode = "karaoke_highlight"
    caption_bg_mode: CaptionBgMode = "none"
    active_word_bg_hex: str = "#F97316"
    active_word_bg_radius: int = Field(default=18, ge=0, le=48)
    dual_keyword_enabled: bool = False
    secondary_keyword_color_hex: str = "#22C55E"
    auto_emoji_enabled: bool = False
    font_family: str = "Arial Black"
    custom_font_path: Optional[str] = None
    font_size: int = Field(default=64, ge=20, le=140)
    pos_x: int = Field(default=540, ge=60, le=1020)
    pos_y: int = Field(default=1520, ge=120, le=1840)
    placement_preset: SubtitlePlacementPreset = "bottom_safe"
    above_speaker_gap_px: int = Field(default=40, ge=0, le=200)
    primary_color_hex: str = "#FFFFFF"
    highlight_color_hex: str = "#FACC15"
    outline_color_hex: str = "#000000"
    outline_width: float = Field(default=5.0, ge=0.0, le=20.0)
    shadow_color_hex: str = "#80000000"
    shadow_depth: float = Field(default=2.0, ge=0.0, le=15.0)
    glow_strength: float = Field(default=0.0, ge=0.0, le=15.0)
    animation: SubtitleAnimationMode = "pop"
    uppercase: bool = True
    max_words_per_line: int = Field(default=4, ge=1, le=8)
    multi_speaker_enabled: bool = True
    speaker_palettes: dict[str, SpeakerColorPalette] = Field(
        default_factory=_default_speaker_palettes
    )
    action_token_color_hex: str = "#F97316"
    timing_offset_sec: float = Field(default=0.0, ge=-1.5, le=1.5)
    footnote: SubtitleFootnoteConfig = Field(default_factory=SubtitleFootnoteConfig)

    @field_validator(
        "primary_color_hex",
        "highlight_color_hex",
        "outline_color_hex",
        "shadow_color_hex",
        "action_token_color_hex",
        "active_word_bg_hex",
        "secondary_keyword_color_hex",
        mode="before",
    )
    @classmethod
    def _norm_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#FFFFFF")


class HookBannerConfig(BaseModel):
    """Opening hook title card or viral sticker headline displayed at the start of the clip."""

    enabled: bool = True
    text: str = ""
    design_style: HookDesignStyle = "viral_sticker"
    font_family: str = "Impact"
    italic: bool = True
    uppercase: bool = True
    duration_sec: float = Field(default=2.8, ge=0.5, le=12.0)
    bg_color_hex: str = "#E617110F"
    accent_color_hex: str = "#FACC15"
    text_color_hex: str = "#FFE600"
    secondary_text_color_hex: str = "#FFFFFF"
    outline_color_hex: str = "#000000"
    outline_width: float = Field(default=7.0, ge=0.0, le=28.0)
    shadow_color_hex: str = "#5B21B6"
    shadow_depth: float = Field(default=7.5, ge=0.0, le=32.0)
    sticker_border_color_hex: str = "#FFFFFF"
    sticker_border_width: float = Field(default=6.5, ge=0.0, le=28.0)
    eyebrow_text: str = "HOT TOPIC"
    font_size: int = Field(default=74, ge=14, le=160)
    pos_y: int = Field(default=920, ge=60, le=1750)
    transition_in: HookTransitionMode = "zoom"
    transition_out: HookTransitionMode = "fade"
    match_opening_tts_duration: bool = True
    opening_intro_mode: OpeningIntroMode = "freeze_clear"

    @field_validator(
        "bg_color_hex",
        "accent_color_hex",
        "text_color_hex",
        "secondary_text_color_hex",
        "outline_color_hex",
        "shadow_color_hex",
        "sticker_border_color_hex",
        mode="before",
    )
    @classmethod
    def _norm_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#FFFFFF")


class CommentBubbleItem(BaseModel):
    """Twitch/Stream style viewer question card timed to specific clip seconds."""

    id: str = "cb_1"
    enabled: bool = True
    author_label: str = "viewers:"
    username: str = "twitch_viewer"
    text: str = "Bang bahas momen ini dong!"
    avatar_path: Optional[str] = None
    avatar_color_hex: str = "#9146FF"
    bg_color_hex: str = "#F218181B"
    text_color_hex: str = "#F8FAFC"
    start_sec: float = Field(default=0.5, ge=0.0)
    end_sec: float = Field(default=4.0, gt=0.0)
    pos_x: int = Field(default=540, ge=100, le=980)
    pos_y: int = Field(default=340, ge=80, le=1800)
    scale: float = Field(default=1.0, ge=0.5, le=2.0)

    @field_validator("avatar_color_hex", "bg_color_hex", "text_color_hex", mode="before")
    @classmethod
    def _norm_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#9146FF")

    @model_validator(mode="after")
    def _validate_span(self) -> "CommentBubbleItem":
        if self.end_sec <= self.start_sec:
            self.end_sec = round(self.start_sec + 1.5, 2)
        return self


class WatermarkConfig(BaseModel):
    """Channel logo image or text watermark overlay settings."""

    enabled: bool = False
    text: str = "@ATRO"
    logo_path: Optional[str] = None
    position: WatermarkPosition = "top_right"
    pos_x: int = Field(default=920, ge=40, le=1040)
    pos_y: int = Field(default=80, ge=40, le=1880)
    opacity: float = Field(default=0.75, ge=0.05, le=1.0)
    scale: float = Field(default=1.0, ge=0.25, le=3.0)


class ClimaxEffectsConfig(BaseModel):
    """Visual punch effects (zoom punch, subtle contrast boost, progress bar)."""

    contrast_boost_enabled: bool = True
    contrast: float = Field(default=1.06, ge=0.8, le=1.5)
    saturation: float = Field(default=1.12, ge=0.5, le=2.0)
    brightness: float = Field(default=0.01, ge=-0.3, le=0.3)
    zoom_punch_enabled: bool = False
    zoom_punch_start_sec: float = Field(default=2.0, ge=0.0)
    zoom_punch_duration_sec: float = Field(default=1.5, ge=0.2, le=10.0)
    zoom_punch_factor: float = Field(default=1.12, ge=1.02, le=1.40)
    # >0 repeats the punch every N seconds after `zoom_punch_start_sec` (0 = single punch)
    zoom_punch_repeat_sec: float = Field(default=0.0, ge=0.0, le=30.0)
    progress_bar_enabled: bool = True
    progress_bar_color_hex: str = "#FACC15"
    vignette_enabled: bool = False
    topic_header_enabled: bool = False
    topic_header_text: str = ""

    @field_validator("progress_bar_color_hex", mode="before")
    @classmethod
    def _norm_hex(cls, v: str) -> str:
        return _normalize_hex_color(v, "#FACC15")


class OverlayLayerConfig(BaseModel):
    """Intro banners, viewer comment bubbles, watermark, and climax effects."""

    hook_banner: HookBannerConfig = Field(default_factory=HookBannerConfig)
    comment_bubbles: list[CommentBubbleItem] = Field(default_factory=list)
    watermark: WatermarkConfig = Field(default_factory=WatermarkConfig)
    climax_effects: ClimaxEffectsConfig = Field(default_factory=ClimaxEffectsConfig)


class ComposerSfxCue(BaseModel):
    """Contextual meme/punchline SFX trigger at a clip-relative timestamp [0..duration]."""

    time_sec: float = Field(default=0.0, ge=0.0)
    sfx_name: str
    volume: float = Field(default=0.75, ge=0.0, le=1.0)
    reason: str = ""


class AudioLayerConfig(BaseModel):
    """Local backsound folder, track selection, ducking, loudnorm, and contextual SFX settings."""

    backsound_enabled: bool = True
    backsound_dir: Optional[str] = None
    selection_mode: BacksoundSelectionMode = "rotate"
    selected_track_filename: Optional[str] = None
    backsound_volume: float = Field(default=0.14, ge=0.0, le=1.0)
    audio_ducking: bool = True
    vocal_loudnorm: bool = True
    contextual_sfx_enabled: bool = False
    sfx_cues: list[ComposerSfxCue] = Field(default_factory=list)


class TranscriptSegmentOverride(BaseModel):
    """Editable transcript segment inside the clip window with speaker tagging."""

    index: int = Field(ge=0)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    text: str
    speaker: str = "SPEAKER_1"
    words: list[TranscriptWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_span(self) -> "TranscriptSegmentOverride":
        if self.end <= self.start:
            self.end = round(self.start + 0.2, 3)
        return self


class ClipComposerConfig(BaseModel):
    """Unified Project/Clip Config JSON read by both Live Preview and FFmpeg Render Engine."""

    schema_version: str = "1.0"
    preset_id: str = "podcast"
    preset_name: str = "Podcast Focus"
    clip_index: int = Field(default=1, ge=1)
    clip_title: str = "Viral Clip"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    canvas_width: int = 1080
    canvas_height: int = 1920
    timing: ClipTimingConfig = Field(default_factory=ClipTimingConfig)
    layout: LayoutLayerConfig = Field(default_factory=LayoutLayerConfig)
    subtitles: SubtitleLayerConfig = Field(default_factory=SubtitleLayerConfig)
    overlays: OverlayLayerConfig = Field(default_factory=OverlayLayerConfig)
    audio: AudioLayerConfig = Field(default_factory=AudioLayerConfig)
    transcript_overrides: list[TranscriptSegmentOverride] = Field(default_factory=list)

    def sync_subtitle_anchor(self) -> None:
        """Align subtitle Y position to the selected placement preset in 1080x1920 space."""
        h = max(100, int(self.canvas_height))
        preset = self.subtitles.placement_preset
        if preset == "split_line_center":
            self.subtitles.pos_x = self.canvas_width // 2
            self.subtitles.pos_y = max(140, min(h - 140, int(round(h * self.layout.split_ratio_top))))
        elif preset in ("bottom_safe", "above_speaker"):
            self.subtitles.pos_x = self.canvas_width // 2
            self.subtitles.pos_y = int(round(h * 0.79))
        elif preset == "top_safe":
            self.subtitles.pos_x = self.canvas_width // 2
            self.subtitles.pos_y = int(round(h * 0.16))

    def clip_duration_sec(self) -> float:
        """Return trimmed clip duration (without padding)."""
        return max(0.5, round(self.timing.end_time - self.timing.start_time, 3))
