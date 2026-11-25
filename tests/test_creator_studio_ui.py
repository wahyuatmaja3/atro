"""Comprehensive UI tests for ATRO Dark Creator Studio overhaul."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDropEvent, QMouseEvent

from atro.config import AppSettings
from atro.core.composer_schema import ComposerSfxCue
from atro.core.presets import build_preset_config
from atro.core.schemas import (
    JobRequest,
    JobState,
    JobStatus,
    RenderedClip,
    TranscriptSegment,
    TranscriptWord,
)
from atro.modules.cutter.composer_renderer import ensure_qt_gui_app
from atro.ui.theme import PALETTE, build_stylesheet
from atro.ui.tokens import DARK_TOKENS
from atro.ui.widgets import ClipCard, ClipStudioWidget


def _fake_rendered_clip(tmp_path: Path, idx: int, score: int, title: str) -> RenderedClip:
    mp4 = tmp_path / f"{title}.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x20ftypmp42")
    return RenderedClip(
        clip_index=idx,
        title=title,
        reason="Momen paling lucu & relate dengan audiens",
        virality_score=score,
        requested_start_sec=0.0,
        requested_end_sec=45.0,
        start_sec=0.0,
        end_sec=45.0,
        padded_start_sec=0.0,
        padded_end_sec=45.5,
        output_duration_sec=45.5,
        output_path=str(mp4),
        file_size_bytes=2_400_000,
        subtitles_burned=True,
        edit_format="podcast",
    )


def test_dark_creator_studio_tokens_and_stylesheet() -> None:
    assert DARK_TOKENS.bg_base == "#090A0F"
    assert DARK_TOKENS.bg_nav == "#12141C"
    assert DARK_TOKENS.bg_surface == "#181B26"
    assert DARK_TOKENS.border == "#242836"
    assert DARK_TOKENS.text_primary == "#F3F4F6"
    assert DARK_TOKENS.text_muted == "#9CA3AF"
    assert DARK_TOKENS.accent == "#7C3AED"
    assert DARK_TOKENS.accent_secondary == "#06B6D4"
    assert PALETTE["bg_base"] == "#090A0F"
    assert PALETTE["accent"] == "#7C3AED"

    qss = build_stylesheet("dark")
    for token_sel in (
        "#Sidebar",
        "#SidebarNavBtn",
        "#HeroDropZone",
        "#DurationChip",
        "#ViralityBadge",
        "#StudioTopBar",
        "#StudioPlayerBar",
        "#TimelinePanel",
        "#TranscriptPanel",
        "#WordChip",
        "#VisualChoiceCard",
    ):
        assert token_sel in qss


def test_main_window_5_menu_collapsible_sidebar_and_min_size(tmp_path: Path, monkeypatch) -> None:
    from atro.ui import main_window as mw_mod

    ensure_qt_gui_app()
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
        ui_theme="dark",
    )
    monkeypatch.setattr(mw_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(mw_mod, "save_settings", lambda _s: None)

    win = mw_mod.MainWindow()
    try:
        assert win.minimumWidth() >= 1280
        assert win.minimumHeight() >= 720
        assert win.wizard_stack.count() == 5
        assert len(win.sidebar._nav_buttons) == 5

        for page_idx, expected_label in enumerate(
            ("Home", "Templates", "Editor", "Render Queue", "Settings")
        ):
            assert win.sidebar._nav_labels[page_idx] == expected_label
            win.sidebar._on_nav_clicked(page_idx)
            assert win.wizard_stack.currentIndex() == page_idx

        w_expanded = win.sidebar.width()
        win.sidebar.toggle_collapsed()
        assert win.sidebar.is_collapsed() is True
        assert win.sidebar.width() < w_expanded
        win.sidebar.toggle_collapsed()
        assert win.sidebar.is_collapsed() is False
        assert win.sidebar.width() == w_expanded
    finally:
        win.close()
        win.deleteLater()


def test_home_quick_clip_hero_drop_duration_chips_and_projects(tmp_path: Path, monkeypatch) -> None:
    from atro.ui import main_window as mw_mod

    ensure_qt_gui_app()
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    monkeypatch.setattr(mw_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(mw_mod, "save_settings", lambda _s: None)

    win = mw_mod.MainWindow()
    try:
        hp = win.home_page
        hp.duration_bar.set_duration("30s", emit_signal=True)
        assert win.min_dur_spin.value() == 15
        assert win.max_dur_spin.value() == 35

        hp.duration_bar.set_duration("60s", emit_signal=True)
        assert win.min_dur_spin.value() == 30
        assert win.max_dur_spin.value() == 62

        hp.subtitles_switch.setChecked(False)
        assert win.subtitles_chk.isChecked() is False
        hp.hook_switch.setChecked(False)
        assert win.hook_title_chk.isChecked() is False
        hp.tts_switch.setChecked(True)
        assert win.opening_tts_chk.isChecked() is True
        hp.facetrack_switch.setChecked(False)
        assert win.framing_combo.currentData() == "center_crop"

        # Simulated file drop on HeroDropZoneCard
        local_vid = tmp_path / "podcast_ep1.mp4"
        local_vid.write_bytes(b"fake_mp4")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(local_vid))])
        drop_ev = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        hp.hero_card.dropEvent(drop_ev)
        assert Path(hp.local_video_path()) == local_vid

        # Quick template 1-click button
        hp._on_quick_template_clicked("gaming_split")
        assert win._selected_template_id == "gaming_split"

        # Recent Projects Grid updates cleanly on job upsert
        now_dt = datetime.now()
        job = JobState(
            job_id="proj-001",
            workspace_dir=str(tmp_path / "ws" / "proj-001"),
            log_path=str(tmp_path / "ws" / "proj-001.log"),
            status=JobStatus.COMPLETED,
            current_stage="completed",
            progress_pct=100.0,
            created_at=now_dt,
            updated_at=now_dt,
            request=JobRequest(url="https://youtube.com/watch?v=demo123"),
        )
        win._upsert_job(job, select=True)
        assert hp.projects_count_lbl.text() == "1 proyek"
        assert hp.projects_grid.count() == 1
    finally:
        win.close()
        win.deleteLater()


def test_opusclip_clip_card_virality_badge_and_actions(tmp_path: Path) -> None:
    ensure_qt_gui_app()
    c_high = _fake_rendered_clip(tmp_path, 1, 9, "Gokil Sih Rahasia Ini")
    c_mid = _fake_rendered_clip(tmp_path, 2, 7, "Lumayan Menarik Juga")

    card_hi = ClipCard(c_high)
    card_md = ClipCard(c_mid)
    try:
        assert card_hi.score_pill.text() == "90/100"
        assert card_hi.score_pill.property("tier") == "high"
        assert card_md.score_pill.text() == "70/100"
        assert card_md.score_pill.property("tier") == "mid"

        custom_hits: list[int] = []
        kit_hits: list[int] = []
        card_hi.customize_requested.connect(custom_hits.append)
        card_hi.publish_kit_requested.connect(kit_hits.append)
        card_hi.customize_btn.click()
        assert custom_hits == [1]

        menu = card_hi.export_btn.menu()
        assert menu is not None
        kit_action = menu.actions()[0]
        kit_action.trigger()
        assert kit_hits == [1]
    finally:
        card_hi.close()
        card_hi.deleteLater()
        card_md.close()
        card_md.deleteLater()


def test_studio_3panel_inspector_transcript_canvas_and_4track_timeline(tmp_path: Path) -> None:
    ensure_qt_gui_app()
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        assert studio.ctrl_tabs.count() == 4
        assert [studio.ctrl_tabs.tabText(i) for i in range(4)] == [
            "Layout",
            "Subtitle",
            "Overlay",
            "Audio",
        ]

        # Visual Layout Icon Cards (6 modes incl. Square 1:1)
        assert len(studio._layout_icon_cards) == 6
        sq_card = studio._layout_icon_cards["square_1_1"]
        click_ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        sq_card.mousePressEvent(click_ev)
        cfg = studio.current_config()
        assert cfg.layout.mode == "blurred_frame"
        assert cfg.layout.blurred_frame_aspect == "1:1"

        # Visual Subtitle Preset Cards (Sorot, Pop, Tumpuk, Minimal + more)
        assert len(studio._subtitle_preset_cards) >= 4
        studio._on_subtitle_card_clicked("mrbeast_pill")
        assert studio.current_config().subtitles.caption_recipe == "mrbeast_pill"

        # Inline Title Editing in Top Bar
        studio.studio_title_edit.setText("Rahasia Podcast Viral")
        studio._on_title_inline_edited()
        assert studio.current_config().clip_title == "Rahasia Podcast Viral"

        # Stage Zoom & Safe-Zone Overlay
        studio.safe_zone_btn.setChecked(True)
        assert studio.preview_canvas._safe_zone_enabled is True
        idx_125 = studio.zoom_combo.findData(1.25)
        studio.zoom_combo.setCurrentIndex(idx_125)
        assert studio.preview_canvas._zoom_factor == 1.25

        # Interactive Word-by-Word Transcript in Left Panel
        cfg = build_preset_config("podcast", clip_title="Demo Word", start_time=0.0, end_time=10.0, padding_sec=0.0)
        cfg.audio.sfx_cues = [ComposerSfxCue(time_sec=2.0, sfx_name="pop", volume=0.7)]
        studio._transcript_segments = [
            TranscriptSegment(
                index=0,
                start=0.5,
                end=3.5,
                text="Halo guys mantap",
                words=[
                    TranscriptWord(word="Halo", start=0.5, end=1.2),
                    TranscriptWord(word="guys", start=1.2, end=2.2),
                    TranscriptWord(word="mantap", start=2.2, end=3.4),
                ],
            )
        ]
        studio._load_config_into_ui(cfg)
        assert len(studio.transcript_panel._blocks) == 1
        blk = studio.transcript_panel._blocks[0]
        assert len(blk._word_buttons) == 3

        # Playhead highlights active word at 1.5s ("guys")
        studio._on_lane_seek(1.5)
        assert blk._word_buttons[1][0].property("active") == "true"

        # Inline transcript edit updates config overrides
        studio._on_interactive_transcript_edited(0, "Halo bro gokil")
        assert any(ov.text == "Halo bro gokil" for ov in studio.current_config().transcript_overrides)

        # Multi-Track Timeline exposes 4 tracks + allows painting without error
        studio.face_lane.resize(800, 160)
        studio.face_lane.repaint()
        studio.preview_canvas.resize(360, 640)
        studio.preview_canvas.repaint()
    finally:
        studio.close()
        studio.deleteLater()


def test_embedded_settings_page_persists_changes(tmp_path: Path, monkeypatch) -> None:
    from atro.ui import main_window as mw_mod

    ensure_qt_gui_app()
    saved_holder: list[AppSettings] = []
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
        enable_transcript_proofread=True,
    )
    monkeypatch.setattr(mw_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(mw_mod, "save_settings", lambda s: saved_holder.append(s.model_copy(deep=True)))

    win = mw_mod.MainWindow()
    try:
        win.navigate_to(4)
        assert win.wizard_stack.currentIndex() == 4
        win.settings_widget.proofread_chk.setChecked(False)
        win.settings_widget.bgm_volume_spin.setValue(42)
        hype_idx = win.settings_widget.tts_style_combo.findData("hype")
        assert hype_idx >= 0
        win.settings_widget.tts_style_combo.setCurrentIndex(hype_idx)
        win.settings_widget.save_btn.click()

        assert saved_holder
        last = saved_holder[-1]
        assert last.enable_transcript_proofread is False
        assert last.backsound_volume == 0.42
        assert last.tts_style == "hype"
        assert last.tts_rate == "+26%"
        assert last.tts_pitch == "+16Hz"
        assert win._settings.backsound_volume == 0.42
    finally:
        win.close()
        win.deleteLater()
