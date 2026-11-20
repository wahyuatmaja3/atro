"""Post-response validator and sanitizer for LLM clip selections."""

from __future__ import annotations

from atro.core.schemas import ClipCandidate, GeminiClipSelectionSchema, JobRequest


def validate_and_sanitize_clips(
    raw_response: GeminiClipSelectionSchema,
    video_duration_sec: float,
    request: JobRequest,
) -> tuple[list[ClipCandidate], list[str]]:
    """Clamp timestamps, resolve overlaps, validate ranges, and collect non-fatal warnings."""
    warnings: list[str] = []
    if not raw_response.clips:
        raise ValueError("Model gemini-4-neon tidak mengembalikan klip apa pun.")

    sanitized: list[ClipCandidate] = []
    for idx, item in enumerate(raw_response.clips, start=1):
        title = (item.title or "").strip() or f"Klip #{idx}"
        reason = (item.reason or "").strip() or "Momen penting dengan hook dan penyampaian yang jelas."

        start = max(0.0, min(float(item.start_time), video_duration_sec))
        end = max(0.0, min(float(item.end_time), video_duration_sec))

        if end <= start:
            warnings.append(f"Klip '{title}' dibuang karena end_time ({item.end_time}) <= start_time ({item.start_time}).")
            continue

        if (end - start) < 5.0:
            warnings.append(f"Klip '{title}' dibuang karena durasi terlalu pendek ({end - start:.1f}s < 5s).")
            continue

        score = max(1, min(10, int(round(item.virality_score))))
        if score != item.virality_score:
            warnings.append(f"Skor virality klip '{title}' dinormalisasi dari {item.virality_score} ke {score}.")

        sanitized.append(
            ClipCandidate(
                title=title,
                start_time=round(start, 3),
                end_time=round(end, 3),
                virality_score=score,
                reason=reason,
            )
        )

    if not sanitized:
        raise ValueError("Tidak ada kandidat klip valid setelah sanitasi timestamp.")

    # Resolve overlaps prioritizing higher virality_score (tie-break: earlier start_time)
    prioritized = sorted(sanitized, key=lambda c: (-c.virality_score, c.start_time))
    accepted: list[ClipCandidate] = []

    for cand in prioritized:
        cur_start = cand.start_time
        cur_end = cand.end_time
        rejected = False

        for existing in accepted:
            overlap_start = max(cur_start, existing.start_time)
            overlap_end = min(cur_end, existing.end_time)
            overlap_sec = overlap_end - overlap_start
            if overlap_sec <= 0:
                continue

            if overlap_sec <= 1.5:
                # Trim small boundary overlap cleanly without touching higher-scoring clip
                if cur_start < existing.start_time <= cur_end:
                    cur_end = round(existing.start_time, 3)
                elif cur_start <= existing.end_time < cur_end:
                    cur_start = round(existing.end_time, 3)
                else:
                    rejected = True
                    break

                if (cur_end - cur_start) < 5.0:
                    rejected = True
                    break
                warnings.append(
                    f"Overlap ringan ({overlap_sec:.2f}s) pada klip '{cand.title}' dirapikan terhadap '{existing.title}'."
                )
            else:
                rejected = True
                warnings.append(
                    f"Klip '{cand.title}' ({cur_start:.1f}-{cur_end:.1f}s, skor {cand.virality_score}) "
                    f"dilewati karena tumpang tindih {overlap_sec:.1f}s dengan '{existing.title}' (skor {existing.virality_score})."
                )
                break

        if not rejected and (cur_end - cur_start) >= 5.0:
            accepted.append(
                ClipCandidate(
                    title=cand.title,
                    start_time=cur_start,
                    end_time=cur_end,
                    virality_score=cand.virality_score,
                    reason=cand.reason,
                )
            )

    if not accepted:
        raise ValueError("Seluruh kandidat klip gugur setelah resolusi overlap.")

    # Keep consistent ordering: highest virality_score first, then earliest start_time
    accepted.sort(key=lambda c: (-c.virality_score, c.start_time))

    # Limit to requested clip_count if model returned more
    if len(accepted) > request.clip_count:
        warnings.append(
            f"Model mengembalikan {len(accepted)} klip; diambil {request.clip_count} klip skor tertinggi."
        )
        accepted = accepted[: request.clip_count]

    # Soft target duration check (warn only, never fail)
    for clip in accepted:
        dur = clip.end_time - clip.start_time
        if dur < request.min_duration_sec * 0.8 or dur > request.max_duration_sec * 1.25:
            warnings.append(
                f"Durasi klip '{clip.title}' ({dur:.1f}s) di luar target ideal {request.min_duration_sec}-{request.max_duration_sec}s."
            )

    return accepted, warnings
