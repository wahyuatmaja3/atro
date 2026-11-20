"""Tests for WebVTT/SRT subtitle parsing and quality fallback rules."""

from __future__ import annotations

from atro.modules.transcriber.subtitle_parser import (
    evaluate_subtitle_quality,
    parse_srt,
    parse_vtt,
)


def test_parse_youtube_vtt_rolling_captions() -> None:
    sample_vtt = """WEBVTT
Kind: captions
Language: id

00:00:00.500 --> 00:00:03.000 align:start position:0%
<c>Kebanyakan orang</c><00:00:01.200><c> salah paham</c>

00:00:03.000 --> 00:00:03.020 align:start position:0%
Kebanyakan orang salah paham

00:00:03.020 --> 00:00:06.800 align:start position:0%
Kebanyakan orang salah paham
soal cara kerja algoritma video pendek ini.

00:00:06.800 --> 00:00:11.200 align:start position:0%
Di tiga detik pertama retensi langsung ditentukan oleh hook pembuka.
"""
    segments = parse_vtt(sample_vtt)
    assert len(segments) == 3
    assert segments[0].text == "Kebanyakan orang salah paham"
    assert segments[1].text == "soal cara kerja algoritma video pendek ini."
    assert segments[2].start == 6.8
    assert segments[2].end == 11.2


def test_parse_srt_and_evaluate_quality() -> None:
    sample_srt = "\n\n".join(
        [
            f"{i + 1}\n00:00:{i * 5:02d},000 --> 00:00:{i * 5 + 4:02d},500\nIni adalah kalimat ke {i + 1} yang cukup panjang dan jelas."
            for i in range(6)
        ]
    )
    segments = parse_srt(sample_srt)
    assert len(segments) == 6
    ok, notes = evaluate_subtitle_quality(segments, video_duration_sec=35.0, is_auto_generated=False)
    assert ok is True
    assert "valid" in notes.lower()


def test_quality_rejects_music_only_or_sparse_subtitles() -> None:
    music_vtt = """WEBVTT

00:00:01.000 --> 00:00:10.000
[Musik]

00:00:20.000 --> 00:00:40.000
[Music]

00:00:45.000 --> 00:00:55.000
[Applause]
"""
    segments = parse_vtt(music_vtt)
    ok, reason = evaluate_subtitle_quality(segments, video_duration_sec=60.0, is_auto_generated=True)
    assert ok is False
    assert "non-speech" in reason.lower() or "sedikit" in reason.lower()
