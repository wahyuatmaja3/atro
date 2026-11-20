"""Command-line interface for ATRO preflight checks and headless pipeline runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from atro import APP_NAME, __version__
from atro.config import load_settings
from atro.core.preflight import check_startup_dependencies
from atro.core.schemas import JobRequest, JobStatus
from atro.pipeline.orchestrator import PipelineOrchestrator
from atro.pipeline.state_store import create_job_state, list_all_jobs, load_state


def _write_check_output(report_json: str, output_path: Optional[str]) -> None:
    if output_path:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(report_json, encoding="utf-8")


def _print_preflight(output_path: Optional[str] = None) -> int:
    settings = load_settings()
    report = check_startup_dependencies(settings)
    report_json = report.model_dump_json(indent=2)
    _write_check_output(report_json, output_path)

    print(f"{APP_NAME} v{__version__} - Startup Preflight Check")
    print(f"Status: {'OK' if report.ok else 'ERROR'}")
    for info in (report.yt_dlp, report.ffmpeg, report.ffprobe):
        state_str = "OK" if info.available else "MISSING"
        detail = info.version or info.error or "-"
        print(f"  [{state_str}] {info.name}: {info.path or '-'} ({detail})")
    if report.warnings:
        for warn in report.warnings:
            print(f"  [WARN] {warn}")
    if report.errors:
        for err in report.errors:
            print(f"  [ERR]  {err}")

    return 0 if report.ok else 1


def _print_jobs() -> int:
    settings = load_settings()
    jobs = list_all_jobs(settings.workspace_dir, recover_stale=False)
    if not jobs:
        print("Belum ada job di workspace.")
        return 0
    for job in jobs:
        print(
            f"{job.job_id} | {job.status.value:<12} | {job.progress_pct:5.1f}% | "
            f"{job.request.aspect_ratio} | {job.request.display_source}"
        )
    return 0


async def _run_pipeline_cli(
    url: Optional[str],
    local_file: Optional[str],
    local_sub: Optional[str],
    resume_job_id: Optional[str],
    clips: int,
    min_dur: int,
    max_dur: int,
    aspect: str,
    padding: float,
    framing: str,
    enable_subtitles: bool,
    enable_hook_title: bool,
    enable_backsound: bool,
    enable_viral_polish: bool,
    backsound_dir: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    whisper_model: Optional[str],
) -> int:
    settings = load_settings()
    if backsound_dir:
        settings.backsound_dir = Path(backsound_dir)
    if provider:
        settings.llm_provider = provider  # type: ignore[assignment]
    if base_url:
        settings.openai_base_url = base_url.strip()
    if model:
        if settings.llm_provider == "openai_compatible":
            settings.openai_model = model.strip()
        else:
            settings.gemini_model = model.strip()
    if api_key:
        if settings.llm_provider == "openai_compatible":
            settings.openai_api_key = api_key.strip()
        else:
            settings.gemini_api_key = api_key.strip()
    if whisper_model:
        settings.whisper_model = whisper_model  # type: ignore[assignment]

    preflight = check_startup_dependencies(settings)
    if local_file:
        if not preflight.ffmpeg.available:
            print(f"[ERROR] {preflight.ffmpeg.error or 'ffmpeg tidak tersedia.'}", file=sys.stderr)
            return 1
    elif not resume_job_id and not preflight.ok:
        for err in preflight.errors:
            print(f"[ERROR] {err}", file=sys.stderr)
        return 1

    def _on_log(job_id: str, line: str) -> None:
        print(line, flush=True)

    orchestrator = PipelineOrchestrator(
        settings_provider=lambda: settings,
        preflight_provider=lambda: preflight,
        on_log=_on_log,
    )

    if resume_job_id:
        job_dir = settings.workspace_dir / resume_job_id
        if not (job_dir / "state.json").is_file():
            print(f"[ERROR] Job '{resume_job_id}' tidak ditemukan di {settings.workspace_dir}", file=sys.stderr)
            return 1
        state = load_state(job_dir)
        final_state = await orchestrator.run_job(state, resume=True)
    else:
        if local_file:
            request = JobRequest(
                source_type="local_file",
                local_video_path=local_file,
                local_subtitle_path=local_sub,
                clip_count=clips,
                min_duration_sec=min_dur,
                max_duration_sec=max_dur,
                aspect_ratio=aspect,  # type: ignore[arg-type]
                padding_sec=padding,
                framing_mode=framing,  # type: ignore[arg-type]
                enable_subtitles=enable_subtitles,
                enable_hook_title=enable_hook_title,
                enable_backsound=enable_backsound,
                enable_viral_polish=enable_viral_polish,
            )
        else:
            if not url:
                print("[ERROR] Berikan URL YouTube atau --file <path_video>.", file=sys.stderr)
                return 1
            request = JobRequest(
                source_type="youtube_url",
                url=url,
                clip_count=clips,
                min_duration_sec=min_dur,
                max_duration_sec=max_dur,
                aspect_ratio=aspect,  # type: ignore[arg-type]
                padding_sec=padding,
                framing_mode=framing,  # type: ignore[arg-type]
                enable_subtitles=enable_subtitles,
                enable_hook_title=enable_hook_title,
                enable_backsound=enable_backsound,
                enable_viral_polish=enable_viral_polish,
            )
        state = create_job_state(settings.workspace_dir, request)
        final_state = await orchestrator.run_job(state, resume=False)

    print(
        json.dumps(
            {
                "job_id": final_state.job_id,
                "status": final_state.status.value,
                "workspace_dir": final_state.workspace_dir,
                "error": final_state.error,
            },
            indent=2,
        )
    )
    return 0 if final_state.status == JobStatus.COMPLETED else 1


def build_parser() -> argparse.ArgumentParser:
    settings = load_settings()
    parser = argparse.ArgumentParser(
        prog="ATRO",
        description="ATRO - Aplikasi AutoClip YouTube Lokal dengan Pipeline Asinkron",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Jalankan preflight check yt-dlp, ffmpeg, ffprobe lalu keluar.",
    )
    parser.add_argument(
        "--output",
        dest="check_output",
        default=None,
        help="Simpan output JSON preflight check ke file (opsional untuk mode --check).",
    )
    parser.add_argument(
        "--log-file",
        dest="check_log_file",
        default=None,
        help="Alias untuk --output saat menjalankan --check.",
    )

    subparsers = parser.add_subparsers(dest="command")

    p_check = subparsers.add_parser("check", help="Periksa ketersediaan yt-dlp, ffmpeg, dan ffprobe.")
    p_check.add_argument("--output", dest="output", default=None, help="Simpan hasil preflight JSON ke file.")
    p_check.add_argument("--log-file", dest="log_file", default=None, help="Alias untuk --output.")

    p_jobs = subparsers.add_parser("jobs", help="Tampilkan daftar job di workspace.")
    _ = p_jobs

    p_run = subparsers.add_parser("run", help="Jalankan pipeline AutoClip dari URL YouTube atau video lokal.")
    p_run.add_argument("url", nargs="?", default=None, help="URL video YouTube (opsional jika memakai --file).")
    p_run.add_argument("--file", dest="local_file", default=None, help="Path ke file video lokal (.mp4, .mkv, .mov, dll.).")
    p_run.add_argument("--sub", dest="local_sub", default=None, help="Path ke file subtitle lokal opsional (.vtt / .srt).")
    p_run.add_argument("--clips", type=int, default=settings.default_clip_count, help="Jumlah klip (1-10).")
    p_run.add_argument("--min-dur", type=int, default=settings.default_min_duration_sec, help="Durasi minimum (detik).")
    p_run.add_argument("--max-dur", type=int, default=settings.default_max_duration_sec, help="Durasi maksimum (detik).")
    p_run.add_argument("--aspect", choices=["16:9", "9:16"], default=settings.default_aspect_ratio, help="Rasio aspek output.")
    p_run.add_argument("--padding", type=float, default=settings.default_padding_sec, help="Padding awal/akhir (0.3-0.5s).")
    p_run.add_argument(
        "--framing",
        choices=["smart_face", "fit_all_blur", "center_crop", "face_tracking"],
        default=settings.default_framing_mode,
        help="Strategi framing 9:16.",
    )
    p_run.add_argument("--no-subtitles", action="store_true", help="Nonaktifkan burn-in subtitle viral.")
    p_run.add_argument("--no-hook-title", action="store_true", help="Nonaktifkan banner hook judul di awal klip.")
    p_run.add_argument("--no-backsound", action="store_true", help="Nonaktifkan musik latar dari folder backsound.")
    p_run.add_argument("--no-viral-polish", action="store_true", help="Nonaktifkan progress bar, fade, dan color polish.")
    p_run.add_argument("--backsound-dir", default=None, help="Override folder backsound lokal.")
    p_run.add_argument("--provider", choices=["gemini", "openai_compatible"], default=None, help="Provider AI untuk analisis klip.")
    p_run.add_argument("--model", default=None, help="Override nama model AI (Gemini / OpenAI-Compatible).")
    p_run.add_argument("--base-url", default=None, help="Override Base URL untuk provider OpenAI-Compatible.")
    p_run.add_argument("--api-key", default=None, help="Override API key provider aktif.")
    p_run.add_argument(
        "--whisper-model",
        choices=["tiny", "base", "small", "medium", "large-v3-turbo"],
        default=None,
        help="Override model faster-whisper.",
    )

    p_resume = subparsers.add_parser("resume", help="Lanjutkan job dari artifact terakhir.")
    p_resume.add_argument("job_id", help="ID job yang ada di ./workspace.")
    p_resume.add_argument("--backsound-dir", default=None, help="Override folder backsound lokal.")
    p_resume.add_argument("--provider", choices=["gemini", "openai_compatible"], default=None, help="Provider AI untuk analisis klip.")
    p_resume.add_argument("--model", default=None, help="Override nama model AI.")
    p_resume.add_argument("--base-url", default=None, help="Override Base URL untuk provider OpenAI-Compatible.")
    p_resume.add_argument("--api-key", default=None, help="Override API key provider aktif.")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        out = args.check_output or args.check_log_file
        return _print_preflight(out)

    if args.command == "check":
        out = args.output or args.log_file
        return _print_preflight(out)

    if args.command == "jobs":
        return _print_jobs()

    if args.command == "run":
        return asyncio.run(
            _run_pipeline_cli(
                url=args.url,
                local_file=args.local_file,
                local_sub=args.local_sub,
                resume_job_id=None,
                clips=args.clips,
                min_dur=args.min_dur,
                max_dur=args.max_dur,
                aspect=args.aspect,
                padding=args.padding,
                framing=args.framing,
                enable_subtitles=not args.no_subtitles,
                enable_hook_title=not args.no_hook_title,
                enable_backsound=not args.no_backsound,
                enable_viral_polish=not args.no_viral_polish,
                backsound_dir=args.backsound_dir,
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                whisper_model=args.whisper_model,
            )
        )

    if args.command == "resume":
        return asyncio.run(
            _run_pipeline_cli(
                url=None,
                local_file=None,
                local_sub=None,
                resume_job_id=args.job_id,
                clips=4,
                min_dur=30,
                max_dur=60,
                aspect="9:16",
                padding=0.4,
                framing="smart_face",
                enable_subtitles=True,
                enable_hook_title=True,
                enable_backsound=True,
                enable_viral_polish=True,
                backsound_dir=args.backsound_dir,
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                whisper_model=None,
            )
        )

    parser.print_help()
    return 0
