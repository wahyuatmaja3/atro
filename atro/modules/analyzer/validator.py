"""Post-response validator and sanitizer for LLM clip selections."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Optional

from atro.core.schemas import (
    ClipCandidate,
    FocusHintItem,
    GeminiClipSelectionSchema,
    JobRequest,
    SfxCueItem,
    TopicOutlineItem,
    TranscriptSegment,
)

_SFX_NORM_RE = re.compile(r"[^a-z0-9]+")
_TITLE_PREFIX_RE = re.compile(r"^(?:klip\s*\d+|clip\s*\d+|judul)\s*[:\-]\s*", re.IGNORECASE)
_TITLE_HASHTAG_RE = re.compile(r"\s*#\w+")


def _sanitize_clip_title(raw_title: Optional[str], idx: int) -> str:
    """Clean LLM title formatting artifacts while preserving casual expressive wording."""
    text = (raw_title or "").strip().strip('"\'`*')
    text = _TITLE_PREFIX_RE.sub("", text).strip()
    text = _TITLE_HASHTAG_RE.sub("", text).strip()
    text = text.strip('"\'`* ')
    text = " ".join(text.split())
    return text or f"Klip #{idx}"


def _match_available_sfx_name(raw_name: str, available_sfx: Optional[Sequence[str]]) -> Optional[str]:
    clean = (raw_name or "").strip()
    if not clean or clean.lower() == "none":
        return None
    if not available_sfx:
        return clean
    by_lower = {s.strip().lower(): s.strip() for s in available_sfx if s and s.strip()}
    if clean.lower() in by_lower:
        return by_lower[clean.lower()]
    norm_clean = _SFX_NORM_RE.sub("", clean.lower())
    if norm_clean:
        for orig in by_lower.values():
            if _SFX_NORM_RE.sub("", orig.lower()) == norm_clean:
                return orig
    return None


def sanitize_sfx_cues(
    raw_cues: list,
    clip_start: float,
    clip_end: float,
    available_sfx: Optional[Sequence[str]] = None,
) -> list[SfxCueItem]:
    """Validate & clamp optional LLM `sfx_cues` into `[clip_start, clip_end]` without clutter."""
    parsed: list[SfxCueItem] = []
    for item in raw_cues or []:
        try:
            t_raw = float(getattr(item, "t_sec", item.get("t_sec") if isinstance(item, dict) else 0.0))
            name_raw = str(getattr(item, "sfx_name", item.get("sfx_name") if isinstance(item, dict) else "") or "")
            reason = str(getattr(item, "reason", item.get("reason") if isinstance(item, dict) else "") or "").strip()
            vol_raw = getattr(item, "volume", item.get("volume", 0.75) if isinstance(item, dict) else 0.75)
            vol = max(0.1, min(1.0, float(vol_raw)))
            matched = _match_available_sfx_name(name_raw, available_sfx)
            if not matched:
                continue
            if t_raw < clip_start - 0.3 or t_raw > clip_end + 0.3:
                continue
            t_c = round(max(clip_start, min(clip_end - 0.1, t_raw)), 3)
            parsed.append(SfxCueItem(t_sec=t_c, sfx_name=matched, volume=vol, reason=reason[:120]))
        except Exception:
            continue

    parsed.sort(key=lambda c: c.t_sec)
    deduped: list[SfxCueItem] = []
    for c in parsed:
        if deduped and (c.t_sec - deduped[-1].t_sec) < 1.5:
            continue
        deduped.append(c)
        if len(deduped) >= 6:
            break
    return deduped


_HEURISTIC_SFX_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    # Money / loot / top-up
    (
        ("cuan", "duit", "uang", "juta", "miliar", "omzet", "kaya", "harga", "bayar", "mahal", "top up", "diamond", "bounty", "gold"),
        ("Cash Register (Kaching)", "Ding", "pop"),
        "Topik uang / cuan / bounty",
    ),
    # Loot / gear pickup (gaming)
    (
        ("loot", "senjata", "item", "upgrade", "armor", "equip", "skin", "drop"),
        ("Counter Strike Item Pickup", "Cash Register (Kaching)", "Ding"),
        "Dapat loot / item / upgrade",
    ),
    # Awkward / silence (check before generic shock/rage)
    (
        ("canggung", "krik", "diam", "hening", "awkward", "kicep", "bengong", "garing"),
        ("Awkward", "Blinking Eyes", "Spongebob Sad Face"),
        "Momen canggung / kicep",
    ),
    # Suspicion / impostor / troll
    (
        ("bohong", "boong", "curiga", "mencurigakan", "sus", "penipu", "ketahuan", "impostor", "troll", "khianat"),
        ("Among Us SUS", "Vine Boom", "Metal Gear Solid Alert"),
        "Momen curiga / sus / plot twist",
    ),
    # Rage quit / disconnect / join
    (
        ("rage quit", "disconnect", "dc", "afk", "keluar", "leave", "cabut", "ninggalin"),
        ("Discord Leave", "Spongebob Sad Face", "DJ Stop"),
        "Teammate rage quit / disconnect",
    ),
    (
        ("mabar", "join", "gabung", "discord", "spawn"),
        ("Discord Join", "pop", "Ding"),
        "Player join / masuk mabar",
    ),
    # Clutch / headshot / victory / hype
    (
        ("clutch", "comeback", "ulti", "maniac", "savage", "ace", "wipe", "rata", "bantai", "gacor"),
        ("Bass Drop", "Japanese Drum", "Crowd Applause"),
        "Momen clutch / wipeout / ulti",
    ),
    (
        ("headshot", "one tap", "karma", "telak", "makan tuh"),
        ("Taco Bell", "Vine Boom but perfectly bass boosted", "Bass Drop"),
        "Headshot / karma instan",
    ),
    (
        ("hoki", "ajaib", "dewa", "mythic", "legendary", "gacha", "jackpot"),
        ("Holy", "Ding", "Crowd Applause"),
        "Momen hoki / RNG dewa",
    ),
    (
        ("duel", "by one", "by1", "showdown", "yooo"),
        ("Japanese Drum", "Prowler Goku", "Bass Drop"),
        "Duel / showdown dramatis",
    ),
    # Alert / danger / horror / panic
    (
        ("awas", "belakang", "musuh", "bahaya", "alert", "sniper", "jumpscare", "nongol"),
        ("Metal Gear Solid Alert", "FAHH", "Crowd Gasp"),
        "Bahaya / musuh muncul mendadak",
    ),
    (
        ("horor", "serem", "hantu", "gelap", "gemeter", "merinding", "takut", "sembunyi"),
        ("Teeth Clattering", "Minecraft Cave Ambience", "Prowler Goku"),
        "Suasana horor / ketakutan",
    ),
    (
        ("bos", "boss", "monster", "smurf", "cheater", "hacker", "aura"),
        ("Prowler Goku", "Hell_s Kitchen Dramatic Sound", "Vine Boom"),
        "Boss / ancaman OP muncul",
    ),
    # Freeze / record scratch / confused
    (
        ("eh salah", "tunggu", "bentar", "stop", "freeze", "pause", "kecolongan"),
        ("DJ Stop", "Vine Boom", "Blinking Eyes"),
        "Record scratch / freeze salah langkah",
    ),
    (
        ("kok bisa", "apaan", "huh", "bingung", "loading", "gimana sih", "nyasar"),
        ("Blinking Eyes", "Cartoon Dizzybirds", "Wobble"),
        "Momen bingung / bengong",
    ),
    # Teleport / vanish / heal / consume
    (
        ("teleport", "blink", "flicker", "lag", "rubberband"),
        ("Minecraft Enderman Teleport", "Wobble", "Vanish"),
        "Teleport / blink / lag mendadak",
    ),
    (
        ("hilang", "ngilang", "lenyap", "menghilang", "vanish", "kabur"),
        ("Vanish", "Disappear", "FAHH"),
        "Karakter kabur / menghilang",
    ),
    (
        ("minum", "potion", "heal", "shield"),
        ("Minecraft Drinking", "Water Droplet", "pop"),
        "Minum potion / healing",
    ),
    (
        ("makan", "regen", "ngemil", "gigit"),
        ("Minecraft Eating", "Munch", "pop"),
        "Makan / regen HP",
    ),
    # Toxic / sensor / yapping
    (
        ("anjing", "bangsat", "asu", "cok", "sensor", "toxic"),
        ("Censor", "Vine Boom but perfectly bass boosted", "Bonk"),
        "Sensor kata kasar / rage",
    ),
    (
        ("bacot", "ngomel", "ceramah", "bawel", "yapping"),
        ("Cartoon Phone Talking", "Awkward", "Bonk"),
        "Teammate bacot / yapping",
    ),
    # Sad / lose streak / fail / blunder / damage
    (
        ("lose streak", "turun rank", "sedih", "kecewa", "nyesek", "rip", "gagal total"),
        ("Spongebob Sad Face", "Awkward", "Lego Break Fall Apart"),
        "Kekalahan / kecewa berat",
    ),
    (
        ("mati", "tewas", "knock", "tumbang", "pecah", "hancur", "roboh", "meledak"),
        ("Lego Break Fall Apart", "Minecraft Take Damage", "Garry_s Mod Ragdoll"),
        "Karakter mati / hancur",
    ),
    (
        ("patah", "fall damage", "ketinggian", "critical", "terpental", "ragdoll"),
        ("Bone Break Crack Snap", "Garry_s Mod Ragdoll", "Splat"),
        "Fall damage / ragdoll parah",
    ),
    (
        ("bonk", "pukul", "kepentok", "nabrak", "salah", "gagal", "jatuh", "bodoh", "tolol", "blunder", "kalah", "noob", "cupu"),
        ("Bonk", "Lego Break Fall Apart", "Metal Pipe", "Splat"),
        "Momen blunder / bonk / fail",
    ),
    (
        ("damage", "kena hit", "creeper", "jebakan", "trap", "sekarat", "1 hp", "satu hp"),
        ("Minecraft Take Damage", "Hell_s Kitchen Dramatic Sound", "Crowd Gasp"),
        "Kena damage / HP kritis",
    ),
    # Rizz / blush / anime / comedy / generic shock
    (
        ("rizz", "ganteng", "keren", "sultan", "emote", "sigma", "kece"),
        ("Rizz", "Blush", "Ding"),
        "Momen pamer / rizz / emote",
    ),
    (
        ("malu", "salting", "ciee", "blush"),
        ("Blush", "Oppai", "pop"),
        "Salah tingkah / malu-malu",
    ),
    (
        ("kaget", "anjir", "gila", "parah", "serius", "buset", "plot twist", "ternyata", "waduh", "astaga"),
        ("Vine Boom", "Bass Drop", "Metal Gear Solid Alert", "Ding"),
        "Punchline / momen kaget",
    ),
    (
        ("wkwk", "haha", "hehe", "ngakak", "ketawa", "tertawa", "kocak", "lucu", "menang", "booyah", "victory", "gg"),
        ("Crowd Applause", "FAHH", "Cartoon Dizzybirds", "pop"),
        "Reaksi tawa / kemenangan",
    ),
)


def build_heuristic_sfx_cues(
    segments: Sequence[TranscriptSegment],
    clip_start: float,
    clip_end: float,
    available_sfx: Sequence[str],
    edit_format: str = "podcast",
    max_cues: int = 4,
) -> list[SfxCueItem]:
    """Infer contextual SFX cues from transcript keywords + gaming rhythm matching available SFX files."""
    if not available_sfx or not segments:
        return []
    in_win = [s for s in segments if s.end > clip_start and s.start < clip_end]
    cues: list[SfxCueItem] = []
    used_names: set[str] = set()

    for seg in in_win:
        low = seg.text.lower()
        best_rule: Optional[tuple[tuple[str, ...], str]] = None
        best_hits = 0
        for keywords, pref_names, reason in _HEURISTIC_SFX_RULES:
            hits = sum(1 for kw in keywords if kw in low)
            if hits > best_hits:
                best_hits = hits
                best_rule = (pref_names, reason)
        if best_rule is not None:
            pref_names, reason = best_rule
            # Prefer unused SFX first so a clip gets varied reactions instead of repeating 1 sound
            chosen = next(
                (
                    m
                    for p in pref_names
                    if (m := _match_available_sfx_name(p, available_sfx)) and m not in used_names
                ),
                None,
            ) or next(
                (m for p in pref_names if (m := _match_available_sfx_name(p, available_sfx))),
                None,
            )
            if chosen:
                t_cue = round(max(clip_start, min(clip_end - 0.15, seg.end - 0.15)), 3)
                if not cues or (t_cue - cues[-1].t_sec) >= 2.5:
                    cues.append(SfxCueItem(t_sec=t_cue, sfx_name=chosen, volume=0.72, reason=reason))
                    used_names.add(chosen)
        if len(cues) >= max_cues:
            break

    # Gaming rhythm fallback: when gaming transcripts lack explicit Indonesian trigger keywords
    # (e.g., short exclamations, questions, or dramatic pauses), place contextually fitting meme SFX.
    if edit_format == "game" and len(cues) < min(3, max_cues) and in_win:
        rhythm_candidates: list[tuple[float, tuple[str, ...], str]] = []
        for idx_s, seg in enumerate(in_win):
            txt = seg.text.strip()
            t_seg = round(max(clip_start + 0.3, min(clip_end - 0.2, seg.end - 0.12)), 3)
            if "?" in txt:
                rhythm_candidates.append((t_seg, ("Blinking Eyes", "Among Us SUS", "Vine Boom"), "Reaksi pertanyaan / kebingungan"))
            elif "!" in txt:
                rhythm_candidates.append((t_seg, ("Vine Boom", "Bass Drop", "Metal Gear Solid Alert", "Ding"), "Penekanan momen seru"))
            elif idx_s > 0 and (seg.start - in_win[idx_s - 1].end) >= 0.9:
                t_gap = round(max(clip_start + 0.2, min(clip_end - 0.2, in_win[idx_s - 1].end + 0.2)), 3)
                rhythm_candidates.append((t_gap, ("Awkward", "DJ Stop", "Vine Boom"), "Jeda dramatis / freeze"))
            elif idx_s == len(in_win) - 1:
                rhythm_candidates.append((t_seg, ("Vine Boom", "Bonk", "pop"), "Punchline penutup klip"))

        for t_cand, prefs, reason in rhythm_candidates:
            if len(cues) >= max_cues:
                break
            if any(abs(t_cand - c.t_sec) < 2.5 for c in cues):
                continue
            chosen = next(
                (
                    m
                    for p in prefs
                    if (m := _match_available_sfx_name(p, available_sfx)) and m not in used_names
                ),
                None,
            ) or next((m for p in prefs if (m := _match_available_sfx_name(p, available_sfx))), None)
            if chosen:
                cues.append(SfxCueItem(t_sec=t_cand, sfx_name=chosen, volume=0.70, reason=reason))
                used_names.add(chosen)
                cues.sort(key=lambda c: c.t_sec)

    return cues


def sanitize_focus_hints(
    raw_hints: list,
    clip_start: float,
    clip_end: float,
) -> list[FocusHintItem]:
    """Validate & clamp optional LLM `focus_hints` safely into `[clip_start, clip_end]`."""
    out: list[FocusHintItem] = []
    for h in raw_hints or []:
        try:
            ts = float(getattr(h, "t_start", h.get("t_start") if isinstance(h, dict) else 0.0))
            te = float(getattr(h, "t_end", h.get("t_end") if isinstance(h, dict) else 0.0))
            raw_role = str(getattr(h, "role", h.get("role") if isinstance(h, dict) else "speaker")).strip().lower()
            role = raw_role if raw_role in ("speaker", "reaction", "group") else "speaker"
            target = str(getattr(h, "target_hint", h.get("target_hint") if isinstance(h, dict) else "") or "").strip()
            reason = str(getattr(h, "reason", h.get("reason") if isinstance(h, dict) else "") or "").strip()
            ts_c = max(clip_start, min(clip_end, ts))
            te_c = max(clip_start, min(clip_end, te))
            if te_c - ts_c >= 0.6:
                out.append(
                    FocusHintItem(
                        t_start=round(ts_c, 3),
                        t_end=round(te_c, 3),
                        role=role,  # type: ignore[arg-type]
                        target_hint=target[:60],
                        reason=reason[:120],
                    )
                )
        except Exception:
            continue
    return out


def build_heuristic_focus_hints(
    segments: list[TranscriptSegment],
    clip_start: float,
    clip_end: float,
) -> list[FocusHintItem]:
    """Infer lightweight Point-of-Interest camera focus hints from transcript rhythm & punctuation."""
    in_win = [s for s in segments if s.end > clip_start and s.start < clip_end]
    if not in_win:
        return []
    hints: list[FocusHintItem] = []
    reaction_cues = ("tertawa", "tawa", "wkwk", "haha", "hehe", "anjir", "serius?", "beneran?", "masa sih")
    for idx, seg in enumerate(in_win):
        t0 = max(clip_start, round(seg.start, 3))
        t1 = min(clip_end, round(seg.end, 3))
        if t1 - t0 < 0.7:
            continue
        low = seg.text.lower()
        if any(cue in low for cue in reaction_cues) or ("?" in low and (t1 - t0) <= 2.2):
            hints.append(
                FocusHintItem(
                    t_start=t0,
                    t_end=min(t1, round(t0 + 1.5, 3)),
                    role="reaction",
                    target_hint="reaksi",
                    reason="Respons cepat / tawa pada obrolan",
                )
            )
        elif idx + 2 < len(in_win):
            # Dense rapid turn cluster (>= 3 segments starting inside 4.0s) -> good group moment
            span = in_win[idx + 2].end - t0
            if 1.5 <= span <= 4.5 and (t1 - t0) < 1.6:
                hints.append(
                    FocusHintItem(
                        t_start=t0,
                        t_end=min(clip_end, round(in_win[idx + 2].end, 3)),
                        role="group",
                        target_hint="semua pembicara",
                        reason="Tektokan cepat multi-pembicara",
                    )
                )
    return hints[:10]


def validate_and_sanitize_clips(
    raw_response: GeminiClipSelectionSchema,
    video_duration_sec: float,
    request: JobRequest,
    available_sfx: Optional[Sequence[str]] = None,
) -> tuple[list[ClipCandidate], list[str]]:
    """Clamp timestamps, resolve overlaps, validate ranges, and collect non-fatal warnings."""
    warnings: list[str] = []
    if not raw_response.clips:
        raise ValueError("Model gemini-4-neon tidak mengembalikan klip apa pun.")

    sanitized: list[ClipCandidate] = []
    for idx, item in enumerate(raw_response.clips, start=1):
        title = _sanitize_clip_title(getattr(item, "title", None), idx)
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

        raw_hints = getattr(item, "focus_hints", []) or []
        clean_hints = sanitize_focus_hints(raw_hints, round(start, 3), round(end, 3))
        raw_cues = getattr(item, "sfx_cues", []) or []
        clean_cues = sanitize_sfx_cues(raw_cues, round(start, 3), round(end, 3), available_sfx=available_sfx)
        sanitized.append(
            ClipCandidate(
                title=title,
                start_time=round(start, 3),
                end_time=round(end, 3),
                virality_score=score,
                reason=reason,
                focus_hints=clean_hints,
                sfx_cues=clean_cues,
            )
        )

    if not sanitized:
        raise ValueError("Tidak ada kandidat klip valid setelah sanitasi timestamp.")

    # When regenerating alternative clips, prefer candidates outside previously clipped spans
    excluded_spans = [
        (float(sp[0]), float(sp[1]))
        for sp in (getattr(request, "excluded_time_spans", None) or [])
        if isinstance(sp, (list, tuple)) and len(sp) >= 2 and float(sp[1]) > float(sp[0])
    ]
    if excluded_spans:
        fresh: list[ClipCandidate] = []
        for cand in sanitized:
            c_dur = max(0.1, cand.end_time - cand.start_time)
            max_ov = max(
                (min(cand.end_time, ex_e) - max(cand.start_time, ex_s))
                for ex_s, ex_e in excluded_spans
            )
            if max_ov / c_dur <= 0.40:
                fresh.append(cand)
        if fresh:
            sanitized = fresh
        else:
            warnings.append("Semua kandidat alternatif bertumpukan dengan klip lama; memilih skor tertinggi.")

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
            trimmed_hints = [
                FocusHintItem(
                    t_start=max(cur_start, h.t_start),
                    t_end=min(cur_end, h.t_end),
                    role=h.role,
                    target_hint=h.target_hint,
                    reason=h.reason,
                )
                for h in cand.focus_hints
                if min(cur_end, h.t_end) - max(cur_start, h.t_start) >= 0.6
            ]
            trimmed_cues = [
                c for c in cand.sfx_cues if cur_start <= c.t_sec <= cur_end
            ]
            accepted.append(
                ClipCandidate(
                    title=cand.title,
                    start_time=cur_start,
                    end_time=cur_end,
                    virality_score=cand.virality_score,
                    reason=cand.reason,
                    focus_hints=trimmed_hints,
                    sfx_cues=trimmed_cues,
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


def sanitize_topic_outline(
    raw_response: GeminiClipSelectionSchema,
    clips: list[ClipCandidate],
    video_duration_sec: float,
) -> list[TopicOutlineItem]:
    """Validate optional LLM topic_outline or synthesize clean topic chapters from selected clips."""
    raw_topics = getattr(raw_response, "topic_outline", None) or []
    out: list[TopicOutlineItem] = []
    for item in raw_topics:
        topic_txt = (getattr(item, "topic", "") or "").strip()
        if not topic_txt:
            continue
        t0 = max(0.0, min(float(getattr(item, "start_time", 0.0)), video_duration_sec))
        t1 = max(t0 + 1.0, min(float(getattr(item, "end_time", t0 + 5.0)), max(t0 + 1.0, video_duration_sec)))
        summary = (getattr(item, "summary", "") or "").strip()
        out.append(TopicOutlineItem(topic=topic_txt, start_time=t0, end_time=t1, summary=summary))

    if out:
        out.sort(key=lambda t: t.start_time)
        return out

    # Fallback: chronological chapter outline anchored on curated clip moments
    ordered = sorted(clips, key=lambda c: c.start_time)
    return [
        TopicOutlineItem(
            topic=c.title,
            start_time=c.start_time,
            end_time=c.end_time,
            summary=c.reason,
        )
        for c in ordered
    ]
