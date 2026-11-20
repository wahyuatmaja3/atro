"""DownloaderService implementation for YouTube downloads and local video ingestion."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from atro.core.logging_utils import JobLogger
from atro.core.schemas import DownloadResult, PreflightReport, SubtitleCandidate
from atro.core.subprocess_utils import (
    JobCancelledError,
    run_subprocess_capture,
    run_subprocess_streaming,
)

_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_SUB_EXTS = {"vtt", "srt", "ass", "ssa", "ttml"}
_PLAYER_CLIENT_ATTEMPTS: tuple[tuple[Optional[str], str], ...] = (
    (None, "default"),
    ("youtube:player_client=android,tv_embedded", "android/tv"),
    ("youtube:player_client=ios,web_safari", "ios/safari"),
)


def _parse_fps(rate_str: Optional[str]) -> float:
    if not rate_str or rate_str in {"0/0", "0"}:
        return 30.0
    if "/" in rate_str:
        num_s, den_s = rate_str.split("/", 1)
        try:
            num, den = float(num_s), float(den_s)
            if den > 0 and num > 0:
                return round(num / den, 3)
        except ValueError:
            return 30.0
    try:
        val = float(rate_str)
        return round(val, 3) if val > 0 else 30.0
    except ValueError:
        return 30.0


def _detect_js_runtime_args() -> list[str]:
    """Detect installed JS runtime (deno or node) for yt-dlp EJS challenge solving."""
    if shutil.which("deno"):
        return ["--js-runtimes", "deno"]
    if shutil.which("node"):
        return ["--js-runtimes", "node"]
    return []


def _pick_priority_languages(info: dict) -> list[str]:
    """Build deduplicated list of preferred subtitle languages."""
    candidates: list[str] = []
    video_lang = info.get("language")
    if isinstance(video_lang, str) and video_lang.strip():
        candidates.append(video_lang.strip())
    for lang in ("id", "en"):
        if lang not in candidates:
            candidates.append(lang)
    return candidates


def _find_best_lang_match(preferred: list[str], available_dict: dict) -> Optional[str]:
    """Find best matching key in subtitle dict: exact match before prefix match."""
    if not isinstance(available_dict, dict) or not available_dict:
        return None
    keys = [str(k) for k in available_dict.keys()]
    for pref in preferred:
        pref_low = pref.lower()
        for key in keys:
            if key.lower() == pref_low:
                return key
        for key in keys:
            if key.lower().startswith(f"{pref_low}-"):
                return key
    return None


def _select_best_subtitle_target(info: dict) -> tuple[Optional[str], bool]:
    """Pick at most 1 best subtitle target `(lang_key, is_auto_generated)` to avoid HTTP 429."""
    preferred_langs = _pick_priority_languages(info)
    manual_subs_map = info.get("subtitles") or {}
    auto_subs_map = info.get("automatic_captions") or {}

    manual_match = _find_best_lang_match(preferred_langs, manual_subs_map)
    if manual_match is not None:
        return manual_match, False

    auto_match = _find_best_lang_match(preferred_langs, auto_subs_map)
    if auto_match is not None:
        return auto_match, True

    return None, False


def _build_video_download_cmd(
    yt_dlp_bin: str,
    ffmpeg_dir: Path,
    url: str,
    js_runtime_args: list[str],
    extractor_args: Optional[str] = None,
) -> list[str]:
    """Build yt-dlp command dedicated strictly to downloading source.mp4 (no subtitle flags)."""
    cmd = [
        yt_dlp_bin,
        *js_runtime_args,
        "--no-playlist",
        "--newline",
        "--progress",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
    ]
    if extractor_args:
        cmd.extend(["--extractor-args", extractor_args])
    cmd.extend(
        [
            "-f",
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "--ffmpeg-location",
            str(ffmpeg_dir),
            "-o",
            "source.%(ext)s",
            url,
        ]
    )
    return cmd


def _build_subtitle_download_cmd(
    yt_dlp_bin: str,
    url: str,
    lang: str,
    is_auto: bool,
    js_runtime_args: list[str],
) -> list[str]:
    """Build best-effort yt-dlp command dedicated to fetching a single subtitle track."""
    return [
        yt_dlp_bin,
        *js_runtime_args,
        "--skip-download",
        "--no-playlist",
        "--ignore-errors",
        "--sub-format",
        "vtt/srt/best",
        "--sub-langs",
        lang,
        "--write-auto-subs" if is_auto else "--write-subs",
        "-o",
        "source.%(ext)s",
        url,
    ]


def _parse_sub_filename(file_path: Path) -> tuple[str, str]:
    """Extract (lang, ext) from yt-dlp subtitle filename like source.id.vtt."""
    ext = file_path.suffix.lstrip(".").lower()
    stem = file_path.stem
    if "." in stem:
        lang = stem.rsplit(".", 1)[-1]
    else:
        lang = "unknown"
    return lang, ext


def _move_stray_subtitles_to_subs_dir(job_dir: Path, subs_dir: Path) -> int:
    """Move any subtitle files in root job_dir (e.g., from an earlier failed run) into subs_dir."""
    moved = 0
    for item in list(job_dir.glob("source.*")):
        if not item.is_file():
            continue
        ext = item.suffix.lstrip(".").lower()
        if ext in _SUB_EXTS:
            dest = subs_dir / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
                moved += 1
            else:
                item.unlink(missing_ok=True)
    return moved


def _cleanup_partial_downloads(job_dir: Path) -> None:
    """Remove leftover partial video fragments before retrying a failed yt-dlp client."""
    for part in job_dir.glob("source.*.part*"):
        if part.is_file():
            part.unlink(missing_ok=True)
    for ytdl in job_dir.glob("source.*.ytdl"):
        if ytdl.is_file():
            ytdl.unlink(missing_ok=True)


def _is_http_403_output(lines: list[str]) -> bool:
    joined = "\n".join(lines).lower()
    return "http error 403" in joined or "403: forbidden" in joined


async def _probe_video_file(
    source_mp4: Path,
    ffprobe_bin: Optional[str],
    logger: JobLogger,
    fallback_duration: float = 0.0,
    fallback_width: int = 1920,
    fallback_height: int = 1080,
    fallback_fps: float = 30.0,
) -> tuple[float, int, int, float, bool]:
    """Extract `(duration_sec, width, height, fps, has_audio)` via ffprobe with safe fallbacks."""
    duration_sec = float(fallback_duration)
    width = int(fallback_width)
    height = int(fallback_height)
    fps = float(fallback_fps)
    has_audio = True

    if ffprobe_bin:
        probe_media_cmd = [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(source_mp4),
        ]
        p_rc, p_out, _ = await run_subprocess_capture(probe_media_cmd, timeout=30.0)
        if p_rc == 0 and p_out:
            try:
                probe_data = json.loads(p_out)
                fmt = probe_data.get("format") or {}
                if fmt.get("duration"):
                    duration_sec = float(fmt["duration"])
                streams = probe_data.get("streams") or []
                video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
                audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
                has_audio = audio_stream is not None
                if video_stream:
                    width = int(video_stream.get("width") or width)
                    height = int(video_stream.get("height") or height)
                    fps = _parse_fps(
                        video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
                    )
            except Exception as exc:
                logger.warning("downloader", f"Gagal membaca output ffprobe, pakai metadata fallback: {exc}")

    return duration_sec, width, height, fps, has_audio


async def _emit_progress(
    on_progress: Optional[Callable[[float], Awaitable[None] | None]],
    pct: float,
) -> None:
    if on_progress is None:
        return
    res = on_progress(pct)
    if asyncio.iscoroutine(res):
        await res


class DownloaderService:
    """Downloads YouTube source.mp4 or ingests local video files, producing metadata.json."""

    async def ingest_local_file(
        self,
        job_id: str,
        local_video_path: Path,
        local_subtitle_path: Optional[Path],
        job_dir: Path,
        preflight: PreflightReport,
        logger: JobLogger,
        on_progress: Optional[Callable[[float], Awaitable[None] | None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> DownloadResult:
        """Ingest a user-supplied local video (and optional .vtt/.srt) into job_dir/source.mp4."""
        if cancel_event and cancel_event.is_set():
            raise JobCancelledError("Job dibatalkan sebelum memproses file lokal.")

        if not preflight.ffmpeg.available or not preflight.ffmpeg.path:
            raise RuntimeError("Binary ffmpeg tidak tersedia.")

        src_video = Path(local_video_path)
        if not src_video.is_file():
            raise FileNotFoundError(f"File video lokal tidak ditemukan: {src_video}")

        ffmpeg_bin = Path(preflight.ffmpeg.path)
        ffprobe_bin = preflight.ffprobe.path if preflight.ffprobe.available else None

        job_dir.mkdir(parents=True, exist_ok=True)
        subs_dir = job_dir / "subs"
        subs_dir.mkdir(parents=True, exist_ok=True)
        source_mp4 = job_dir / "source.mp4"

        await _emit_progress(on_progress, 10.0)
        logger.info("downloader", f"Mengimpor video lokal: {src_video}")

        if src_video.suffix.lower() == ".mp4":
            if src_video.resolve() != source_mp4.resolve():
                await asyncio.to_thread(shutil.copy2, src_video, source_mp4)
                logger.info("downloader", f"Menyalin {src_video.name} ke source.mp4 selesai.")
        else:
            logger.info(
                "downloader",
                f"Mengonversi kontainer {src_video.suffix} ke source.mp4 via FFmpeg...",
            )
            copy_remux_cmd = [
                str(ffmpeg_bin),
                "-y",
                "-i",
                str(src_video),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(source_mp4),
            ]
            rc_copy, _ = await run_subprocess_streaming(
                copy_remux_cmd,
                cancel_event=cancel_event,
                cwd=job_dir,
            )
            if rc_copy != 0 or not source_mp4.is_file() or source_mp4.stat().st_size == 0:
                source_mp4.unlink(missing_ok=True)
                logger.warning(
                    "downloader",
                    f"Stream copy {src_video.suffix} tidak kompatibel MP4; melakukan encode cepat H.264/AAC...",
                )
                encode_cmd = [
                    str(ffmpeg_bin),
                    "-y",
                    "-i",
                    str(src_video),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    str(source_mp4),
                ]
                rc_enc, enc_lines = await run_subprocess_streaming(
                    encode_cmd,
                    cancel_event=cancel_event,
                    cwd=job_dir,
                )
                if rc_enc != 0 or not source_mp4.is_file():
                    raise RuntimeError(
                        f"Gagal mengonversi {src_video.name} ke source.mp4: {' | '.join(enc_lines[-5:])}"
                    )

        await _emit_progress(on_progress, 75.0)

        subtitle_candidates: list[SubtitleCandidate] = []
        if local_subtitle_path is not None:
            src_sub = Path(local_subtitle_path)
            if not src_sub.is_file():
                raise FileNotFoundError(f"File subtitle lokal tidak ditemukan: {src_sub}")
            ext = src_sub.suffix.lstrip(".").lower()
            if ext not in {"vtt", "srt"}:
                raise ValueError("Subtitle lokal harus berformat .vtt atau .srt")
            dest_sub = subs_dir / f"source.local.{ext}"
            if src_sub.resolve() != dest_sub.resolve():
                await asyncio.to_thread(shutil.copy2, src_sub, dest_sub)
            subtitle_candidates.append(
                SubtitleCandidate(
                    lang="local",
                    is_auto_generated=False,
                    ext=ext,
                    local_path=str(dest_sub.resolve()),
                )
            )
            logger.info("downloader", f"Menyertakan subtitle lokal: {src_sub.name}")

        duration_sec, width, height, fps, has_audio = await _probe_video_file(
            source_mp4=source_mp4,
            ffprobe_bin=ffprobe_bin,
            logger=logger,
        )
        if duration_sec <= 0.0:
            raise ValueError("Durasi video lokal tidak valid (<= 0 detik) atau ffprobe gagal membacanya.")

        await _emit_progress(on_progress, 100.0)

        result = DownloadResult(
            job_id=job_id,
            url=str(src_video.resolve()),
            video_id=job_id,
            title=src_video.stem,
            channel="Local File",
            duration_sec=round(duration_sec, 3),
            width=width,
            height=height,
            fps=fps,
            has_audio=has_audio,
            language=None,
            source_video_path=str(source_mp4.resolve()),
            subtitle_candidates=subtitle_candidates,
        )

        metadata_path = job_dir / "metadata.json"
        metadata_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        logger.info(
            "downloader",
            f"Import video lokal selesai: {src_video.name} ({width}x{height} @ {fps}fps, {duration_sec:.1f}s), "
            f"{len(subtitle_candidates)} subtitle lokal.",
        )
        return result

    async def run(
        self,
        job_id: str,
        url: str,
        job_dir: Path,
        preflight: PreflightReport,
        logger: JobLogger,
        on_progress: Optional[Callable[[float], Awaitable[None] | None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> DownloadResult:
        if not preflight.yt_dlp.available or not preflight.yt_dlp.path:
            raise RuntimeError("Binary yt-dlp tidak tersedia.")
        if not preflight.ffmpeg.available or not preflight.ffmpeg.path:
            raise RuntimeError("Binary ffmpeg tidak tersedia.")

        yt_dlp_bin = preflight.yt_dlp.path
        ffmpeg_bin = Path(preflight.ffmpeg.path)
        ffprobe_bin = preflight.ffprobe.path if preflight.ffprobe.available else None
        js_runtime_args = _detect_js_runtime_args()

        job_dir.mkdir(parents=True, exist_ok=True)
        subs_dir = job_dir / "subs"
        subs_dir.mkdir(parents=True, exist_ok=True)

        logger.info("downloader", f"Memeriksa metadata video: {url}")
        probe_cmd = [
            yt_dlp_bin,
            *js_runtime_args,
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            url,
        ]
        rc, stdout_text, stderr_text = await run_subprocess_capture(probe_cmd, timeout=120.0)
        if rc != 0 or not stdout_text:
            raise RuntimeError(
                f"Gagal mengambil metadata video via yt-dlp (exit {rc}): {stderr_text or stdout_text}"
            )

        try:
            info = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Output metadata yt-dlp bukan JSON valid: {exc}") from exc

        target_sub_lang, target_sub_is_auto = _select_best_subtitle_target(info)
        manual_subs_map = info.get("subtitles") or {}
        sub_label = (
            f"{target_sub_lang} ({'auto' if target_sub_is_auto else 'manual'})"
            if target_sub_lang
            else "-"
        )
        logger.info(
            "downloader",
            f"Judul: {info.get('title', 'Unknown')} | Durasi: {info.get('duration', '?')}s | "
            f"Target subtitle: {sub_label}",
        )

        # Step 1 (Mandatory): Download source.mp4 with fallback player_client retries on HTTP 403
        last_reported = -1.0

        async def _handle_line(line: str) -> None:
            nonlocal last_reported
            match = _PROGRESS_RE.search(line)
            if match:
                pct = min(100.0, max(0.0, float(match.group(1))))
                scaled = round(pct * 0.90, 1)
                if on_progress and (scaled - last_reported >= 1.5 or pct >= 100.0):
                    last_reported = scaled
                    await _emit_progress(on_progress, scaled)
            elif "[Merger]" in line or "[ExtractAudio]" in line or "ERROR" in line or "WARNING" in line:
                logger.info("downloader", line)

        rc = 1
        lines: list[str] = []
        for idx, (extractor_args, client_label) in enumerate(_PLAYER_CLIENT_ATTEMPTS):
            if idx == 0:
                logger.info("downloader", "Mengunduh source.mp4 via yt-dlp...")
            else:
                _cleanup_partial_downloads(job_dir)
                last_reported = -1.0
                logger.warning(
                    "downloader",
                    f"Download terkena HTTP 403; mencoba ulang dengan player_client={client_label}...",
                )

            dl_cmd = _build_video_download_cmd(
                yt_dlp_bin=yt_dlp_bin,
                ffmpeg_dir=ffmpeg_bin.parent,
                url=url,
                js_runtime_args=js_runtime_args,
                extractor_args=extractor_args,
            )
            rc, lines = await run_subprocess_streaming(
                dl_cmd,
                on_line=_handle_line,
                cancel_event=cancel_event,
                cwd=job_dir,
            )
            if rc == 0:
                break
            if not _is_http_403_output(lines):
                break

        if rc != 0:
            tail = " | ".join(lines[-5:])
            if _is_http_403_output(lines):
                raise RuntimeError(
                    f"yt-dlp gagal mengunduh video (HTTP 403 Forbidden setelah {len(_PLAYER_CLIENT_ATTEMPTS)} percobaan). "
                    f"Gunakan tab 'Video Lokal' untuk memproses file video hasil download eksternal, atau perbarui yt-dlp (`yt-dlp -U`). "
                    f"Detail: {tail}"
                )
            raise RuntimeError(f"yt-dlp gagal mengunduh video (exit {rc}): {tail}")

        source_mp4 = job_dir / "source.mp4"
        if not source_mp4.is_file():
            alt_files = [
                p
                for p in job_dir.glob("source.*")
                if p.suffix.lower() in {".mkv", ".webm", ".mov", ".m4v", ".ts"}
            ]
            if not alt_files:
                raise FileNotFoundError(
                    f"File source.mp4 tidak ditemukan di {job_dir} setelah proses download."
                )
            alt_file = alt_files[0]
            logger.warning(
                "downloader",
                f"Output yt-dlp berformat {alt_file.suffix}; melakukan remux/encode cepat ke source.mp4...",
            )
            remux_cmd = [
                str(ffmpeg_bin),
                "-y",
                "-i",
                str(alt_file),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(source_mp4),
            ]
            rc_rmx, rmx_lines = await run_subprocess_streaming(
                remux_cmd, cancel_event=cancel_event, cwd=job_dir
            )
            if rc_rmx != 0 or not source_mp4.is_file():
                raise RuntimeError(f"Gagal mengonversi {alt_file.name} ke mp4: {' | '.join(rmx_lines[-3:])}")
            alt_file.unlink(missing_ok=True)

        # Step 2 (Best-effort / Non-fatal): Fetch 1 best subtitle track or reuse existing in job_dir/subs
        moved_count = _move_stray_subtitles_to_subs_dir(job_dir, subs_dir)
        if moved_count:
            logger.info("downloader", f"Memindahkan {moved_count} file subtitle lokal ke folder subs/.")

        has_existing_subs = any(
            p.is_file() and p.suffix.lstrip(".").lower() in _SUB_EXTS for p in subs_dir.iterdir()
        )
        if not has_existing_subs and target_sub_lang:
            sub_cmd = _build_subtitle_download_cmd(
                yt_dlp_bin=yt_dlp_bin,
                url=url,
                lang=target_sub_lang,
                is_auto=target_sub_is_auto,
                js_runtime_args=js_runtime_args,
            )
            logger.info("downloader", f"Mengunduh subtitle opsional ({target_sub_lang})...")
            try:
                sub_rc, sub_lines = await run_subprocess_streaming(
                    sub_cmd,
                    cancel_event=cancel_event,
                    cwd=subs_dir,
                )
                if sub_rc != 0:
                    tail = " | ".join(sub_lines[-3:]) if sub_lines else f"exit {sub_rc}"
                    logger.warning(
                        "downloader",
                        f"Download subtitle '{target_sub_lang}' gagal ({tail}); transkripsi akan otomatis fallback ke faster-whisper lokal.",
                    )
            except JobCancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "downloader",
                    f"Download subtitle '{target_sub_lang}' gagal ({exc}); transkripsi akan otomatis fallback ke faster-whisper lokal.",
                )

        # Step 3: Probe video properties with ffprobe
        duration_sec, width, height, fps, has_audio = await _probe_video_file(
            source_mp4=source_mp4,
            ffprobe_bin=ffprobe_bin,
            logger=logger,
            fallback_duration=float(info.get("duration") or 0.0),
            fallback_width=int(info.get("width") or 1920),
            fallback_height=int(info.get("height") or 1080),
            fallback_fps=float(info.get("fps") or 30.0),
        )
        if duration_sec <= 0.0:
            raise ValueError("Durasi video tidak valid (<= 0 detik).")

        # Collect downloaded subtitles
        subtitle_candidates: list[SubtitleCandidate] = []
        manual_lang_set = {str(k).lower() for k in manual_subs_map.keys()}
        for sub_file in sorted(subs_dir.glob("*")):
            if not sub_file.is_file():
                continue
            lang, ext = _parse_sub_filename(sub_file)
            if ext not in _SUB_EXTS:
                continue
            if target_sub_lang and lang.lower() == target_sub_lang.lower():
                is_auto = target_sub_is_auto
            else:
                is_auto = lang.lower() not in manual_lang_set
            subtitle_candidates.append(
                SubtitleCandidate(
                    lang=lang,
                    is_auto_generated=is_auto,
                    ext=ext,
                    local_path=str(sub_file.resolve()),
                )
            )

        await _emit_progress(on_progress, 100.0)

        result = DownloadResult(
            job_id=job_id,
            url=url,
            video_id=str(info.get("id") or job_id),
            title=str(info.get("title") or "Untitled Video"),
            channel=str(info.get("uploader") or info.get("channel") or ""),
            duration_sec=round(duration_sec, 3),
            width=width,
            height=height,
            fps=fps,
            has_audio=has_audio,
            language=info.get("language") if isinstance(info.get("language"), str) else None,
            source_video_path=str(source_mp4.resolve()),
            subtitle_candidates=subtitle_candidates,
        )

        metadata_path = job_dir / "metadata.json"
        metadata_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        logger.info(
            "downloader",
            f"Download selesai: {source_mp4.name} ({width}x{height} @ {fps}fps, {duration_sec:.1f}s), "
            f"{len(subtitle_candidates)} kandidat subtitle.",
        )
        return result
