"""Whisper decode hinting, offline Indonesian typo cleanup, and contextual AI proofreading."""

from __future__ import annotations

import asyncio
import json
import re
from difflib import SequenceMatcher
from typing import Any, Optional

from pydantic import BaseModel, Field

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.schemas import DownloadResult, TranscriptSegment, TranscriptWord

_WORD_SPLIT_RE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.UNICODE)
_ALNUM_RE = re.compile(r"[^\w]+", re.UNICODE)
_MULTI_SPACE_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,!?;:…])")

_ID_TITLE_HINTS: set[str] = {
    "yang",
    "dan",
    "di",
    "ke",
    "dari",
    "ini",
    "itu",
    "untuk",
    "buat",
    "nggak",
    "gak",
    "ngak",
    "gue",
    "aku",
    "kamu",
    "elu",
    "kita",
    "mereka",
    "kenapa",
    "gimana",
    "ketika",
    "ternyata",
    "cara",
    "seru",
    "lucu",
    "kocak",
    "ngakak",
    "podcast",
    "curhat",
    "bikin",
    "sampe",
    "sampai",
    "banget",
    "parah",
    "kali",
    "baru",
    "viral",
    "drama",
    "kesel",
    "ikut",
    "soal",
    "bahas",
    "orang",
    "juta",
    "ribu",
    "pakai",
    "pake",
}

_HALLUCINATION_PHRASES: tuple[str, ...] = (
    "terima kasih telah menonton",
    "terima kasih sudah menonton",
    "jangan lupa like comment dan subscribe",
    "jangan lupa subscribe",
    "subtitle oleh",
    "subtitles by",
    "amara.org",
    "mbc",
)

# Conversational Indonesian typo / shorthand / common ASR phonetic fixes
_ID_TYPO_MAP: dict[str, str] = {
    "yg": "yang",
    "dgn": "dengan",
    "krn": "karena",
    "karna": "karena",
    "tdk": "tidak",
    "bgt": "banget",
    "emg": "emang",
    "gmn": "gimana",
    "gimna": "gimana",
    "knp": "kenapa",
    "kenpa": "kenapa",
    "jgn": "jangan",
    "blm": "belum",
    "sdh": "sudah",
    "udh": "udah",
    "sy": "saya",
    "gtu": "gitu",
    "gituh": "gitu",
    "org": "orang",
    "bbrp": "beberapa",
    "trs": "terus",
    "trus": "terus",
    "skrg": "sekarang",
    "kmrn": "kemarin",
    "smua": "semua",
    "slalu": "selalu",
    "mmg": "memang",
    "pdhl": "padahal",
    "tp": "tapi",
    "jd": "jadi",
    "jdi": "jadi",
    "lg": "lagi",
    "ngga": "nggak",
    "engga": "nggak",
    "ngak": "nggak",
    "enggak": "nggak",
    "ga": "gak",
    "yutub": "YouTube",
    "yutube": "YouTube",
    "podkas": "podcast",
    "podkes": "podcast",
    "podcas": "podcast",
    "kontent": "konten",
    "sabskreb": "subscribe",
    "sabskraib": "subscribe",
    "sabskreber": "subscriber",
    "saskreb": "subscribe",
    "vidio": "video",
    "fideo": "video",
    "kwalitas": "kualitas",
    "praktek": "praktik",
    "aktifitas": "aktivitas",
    "resiko": "risiko",
    "atlit": "atlet",
    "sekedar": "sekadar",
    "nasehat": "nasihat",
    "sistim": "sistem",
    "apotik": "apotek",
    "analisa": "analisis",
    "komplit": "komplet",
    "menset": "mindset",
    "mainset": "mindset",
    "spil": "spill",
    "kringe": "cringe",
    "toksik": "toxic",
}


class ProofreadItem(BaseModel):
    index: int
    text: str


class TranscriptProofreadBatchSchema(BaseModel):
    corrections: list[ProofreadItem] = Field(default_factory=list)


_OPENAI_PROOFREAD_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["index", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["corrections"],
    "additionalProperties": False,
}


def infer_language_hint(
    raw_lang: Optional[str],
    title: str = "",
    channel: Optional[str] = None,
    explicit_pref: str = "auto",
) -> Optional[str]:
    """Resolve reliable Whisper language code ('id', 'en', ...) or None for auto-detect."""
    pref = (explicit_pref or "auto").strip().lower()
    if pref in ("id", "en"):
        return pref

    raw = (raw_lang or "").strip().lower()
    if raw:
        base = raw.split("-", 1)[0].split("_", 1)[0]
        if base == "in":
            return "id"
        if len(base) == 2 and base.isalpha():
            return base

    tokens = [
        _ALNUM_RE.sub("", tok.lower())
        for tok in f"{title} {channel or ''}".split()
    ]
    hits = sum(1 for tok in tokens if tok in _ID_TITLE_HINTS)
    if hits >= 2:
        return "id"
    return None


def build_whisper_initial_prompt(
    title: str = "",
    channel: Optional[str] = None,
    language: Optional[str] = None,
) -> str:
    """Build concise context/spelling hint for faster-whisper without triggering repetition."""
    ctx_parts: list[str] = []
    clean_title = _MULTI_SPACE_RE.sub(" ", (title or "").strip())
    clean_ch = _MULTI_SPACE_RE.sub(" ", (channel or "").strip())
    if clean_title:
        ctx_parts.append(f"Topik: {clean_title[:120]}.")
    if clean_ch:
        ctx_parts.append(f"Pembicara: {clean_ch[:60]}.")

    lang = (language or "").strip().lower()
    if lang == "id" or infer_language_hint(lang, clean_title, clean_ch) == "id":
        base = (
            "Transkrip obrolan Indonesia natural dan kasual, ejaan benar "
            "(gue, elu, nggak, gak, banget, kayak, emang, udah, yaudah, sih, nih, tuh, gitu)."
        )
    else:
        base = "Accurate conversational transcript with proper names and natural punctuation."

    return _MULTI_SPACE_RE.sub(" ", f"{base} {' '.join(ctx_parts)}").strip()


def build_whisper_hotwords(title: str = "", channel: Optional[str] = None) -> Optional[str]:
    """Extract proper names & key terms from video title/channel to guide Whisper decoder."""
    seen: list[str] = []
    seen_low: set[str] = set()
    for raw_tok in f"{title} {channel or ''}".split():
        cleaned = raw_tok.strip(".,!?;:\"'()[]{}#*@")
        if len(cleaned) < 3:
            continue
        if cleaned.startswith("#") or cleaned.lower().startswith("http"):
            continue
        low = cleaned.lower()
        if low in seen_low or low in {"dan", "the", "for", "with", "ke", "di", "ini", "itu"}:
            continue
        seen_low.add(low)
        seen.append(cleaned)
        if len(seen) >= 16:
            break
    return ", ".join(seen) if seen else None


def _preserve_case(orig_core: str, fixed_lower_or_mixed: str) -> str:
    if any(ch.isupper() for ch in fixed_lower_or_mixed):
        return fixed_lower_or_mixed
    if orig_core.isupper() and len(orig_core) > 1:
        return fixed_lower_or_mixed.upper()
    if orig_core[:1].isupper():
        return fixed_lower_or_mixed[:1].upper() + fixed_lower_or_mixed[1:]
    return fixed_lower_or_mixed


def _extract_reference_entities(title: str, channel: Optional[str]) -> dict[str, str]:
    """Collect proper words (>=4 chars) from title/channel for fuzzy STT entity recovery."""
    entities: dict[str, str] = {}
    for tok in f"{title} {channel or ''}".split():
        cleaned = re.sub(r"[^\w\-]", "", tok.strip())
        if len(cleaned) >= 5 and any(ch.isalpha() for ch in cleaned):
            entities.setdefault(cleaned.lower(), cleaned)
    return entities


def _match_entity_word(core: str, entities: dict[str, str]) -> Optional[str]:
    low = core.lower()
    if not low or len(low) < 4 or not entities:
        return None
    if low in entities:
        canon = entities[low]
        return canon if canon[:1].isupper() else core
    best_canon: Optional[str] = None
    best_ratio = 0.0
    for ent_low, ent_canon in entities.items():
        if abs(len(low) - len(ent_low)) > 2 or low[0] != ent_low[0]:
            continue
        ratio = SequenceMatcher(None, low, ent_low).ratio()
        if ratio >= 0.85 and ratio > best_ratio:
            best_ratio = ratio
            best_canon = ent_canon
    return best_canon


def align_words_preserving_timestamps(
    orig_words: list[TranscriptWord],
    seg_start: float,
    seg_end: float,
    new_text: str,
) -> list[TranscriptWord]:
    """Re-map corrected segment text onto existing word timestamps monotonically."""
    new_tokens = [tok for tok in _MULTI_SPACE_RE.sub(" ", new_text).strip().split() if tok]
    if not new_tokens:
        return []
    if not orig_words:
        # Proportional fallback over segment interval
        dur = max(0.08, seg_end - seg_start)
        weights = [max(1, len(t)) for t in new_tokens]
        total_w = max(1, sum(weights))
        out: list[TranscriptWord] = []
        cursor = seg_start
        for i, (tok, wt) in enumerate(zip(new_tokens, weights)):
            w_end = seg_end if i == len(new_tokens) - 1 else min(seg_end, cursor + max(0.04, dur * (wt / total_w)))
            out.append(TranscriptWord(word=tok, start=round(cursor, 3), end=round(max(cursor + 0.04, w_end), 3)))
            cursor = out[-1].end
        return out

    old_norms = [_ALNUM_RE.sub("", w.word.lower()) or w.word.lower() for w in orig_words]
    new_norms = [_ALNUM_RE.sub("", t.lower()) or t.lower() for t in new_tokens]
    matcher = SequenceMatcher(None, old_norms, new_norms, autojunk=False)

    aligned: list[TranscriptWord] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for old_idx, new_idx in zip(range(i1, i2), range(j1, j2)):
                ow = orig_words[old_idx]
                aligned.append(
                    TranscriptWord(word=new_tokens[new_idx], start=ow.start, end=ow.end)
                )
        elif tag in ("replace", "insert"):
            sub_tokens = new_tokens[j1:j2]
            if not sub_tokens:
                continue
            if i2 > i1:
                span_s = orig_words[i1].start
                span_e = max(span_s + 0.05, orig_words[i2 - 1].end)
            else:
                left_end = orig_words[i1 - 1].end if i1 > 0 else seg_start
                right_start = orig_words[i1].start if i1 < len(orig_words) else seg_end
                span_s = max(seg_start, min(left_end, right_start))
                span_e = max(span_s + 0.06, right_start)
            dur = max(0.05 * len(sub_tokens), span_e - span_s)
            weights = [max(1, len(t)) for t in sub_tokens]
            total_w = max(1, sum(weights))
            cursor = span_s
            for k, (tok, wt) in enumerate(zip(sub_tokens, weights)):
                w_end = (
                    max(cursor + 0.04, span_e)
                    if k == len(sub_tokens) - 1
                    else min(span_e, cursor + max(0.04, dur * (wt / total_w)))
                )
                aligned.append(
                    TranscriptWord(word=tok, start=round(cursor, 3), end=round(w_end, 3))
                )
                cursor = w_end

    # Enforce strict monotonic non-overlapping bounds inside [seg_start, seg_end]
    for i in range(len(aligned)):
        st = max(seg_start, aligned[i].start)
        if i > 0:
            st = max(st, aligned[i - 1].end)
        en = max(st + 0.03, min(seg_end, aligned[i].end))
        aligned[i] = TranscriptWord(word=aligned[i].word, start=round(st, 3), end=round(en, 3))
    return aligned


def _clean_single_token(tok: str, entities: dict[str, str]) -> str:
    m = _WORD_SPLIT_RE.match(tok)
    if not m:
        return tok
    lead, core, trail = m.groups()
    if not core:
        return tok
    low = core.lower()
    if low in _ID_TYPO_MAP and not (core.isupper() and len(core) <= 3):
        fixed = _preserve_case(core, _ID_TYPO_MAP[low])
        return f"{lead}{fixed}{trail}"
    ent_match = _match_entity_word(core, entities)
    if ent_match is not None:
        return f"{lead}{ent_match}{trail}"
    return tok


def clean_transcript_segments_offline(
    segments: list[TranscriptSegment],
    title: str = "",
    channel: Optional[str] = None,
) -> list[TranscriptSegment]:
    """Fix common Indonesian ASR typos, merged entity splits, and hallucinations offline."""
    entities = _extract_reference_entities(title, channel)
    cleaned: list[TranscriptSegment] = []

    for seg in segments:
        raw_text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", _MULTI_SPACE_RE.sub(" ", seg.text or "").strip())
        if not raw_text:
            continue
        low_full = raw_text.lower().strip(".,!? ")
        if any(phrase in low_full for phrase in _HALLUCINATION_PHRASES) and len(raw_text.split()) <= 10:
            continue

        # Merge two accidentally split adjacent tokens that together match a known entity (e.g. "radit ya" -> "Raditya")
        raw_tokens = raw_text.split()
        merged_tokens: list[str] = []
        i = 0
        while i < len(raw_tokens):
            if i + 1 < len(raw_tokens) and entities:
                c1 = re.sub(r"[^\w]", "", raw_tokens[i])
                c2 = re.sub(r"[^\w]", "", raw_tokens[i + 1])
                if c1 and c2:
                    joined_ent = _match_entity_word(c1 + c2, entities)
                    if joined_ent is not None and len(c1 + c2) >= 5:
                        trail_m = _WORD_SPLIT_RE.match(raw_tokens[i + 1])
                        trail = trail_m.group(3) if trail_m else ""
                        merged_tokens.append(f"{joined_ent}{trail}")
                        i += 2
                        continue
            merged_tokens.append(_clean_single_token(raw_tokens[i], entities))
            i += 1

        # Collapse 3+ consecutive identical word repetitions caused by Whisper stuttering
        dedup_tokens: list[str] = []
        for tok in merged_tokens:
            norm = _ALNUM_RE.sub("", tok.lower())
            if (
                norm
                and len(dedup_tokens) >= 2
                and _ALNUM_RE.sub("", dedup_tokens[-1].lower()) == norm
                and _ALNUM_RE.sub("", dedup_tokens[-2].lower()) == norm
            ):
                continue
            dedup_tokens.append(tok)

        if not dedup_tokens:
            continue
        new_text = " ".join(dedup_tokens).strip()
        if (
            new_text
            and new_text[0].islower()
            and not (len(new_text) >= 2 and new_text[0] == "i" and new_text[1].isupper())
        ):
            new_text = new_text[0].upper() + new_text[1:]

        new_words = (
            align_words_preserving_timestamps(seg.words, seg.start, seg.end, new_text)
            if seg.words
            else []
        )
        cleaned.append(
            TranscriptSegment(
                index=len(cleaned),
                start=seg.start,
                end=seg.end,
                text=new_text,
                words=new_words,
            )
        )

    return cleaned


def _build_proofread_system_prompt(download: DownloadResult) -> str:
    return (
        "Anda adalah editor transkrip audio Indonesia profesional (post-processor ASR/Whisper).\n"
        f"Konteks Video: '{download.title}' (Channel: {download.channel or '-'}).\n\n"
        "TUGAS KETAT:\n"
        "1. Perbaiki HANYA typo salah dengar STT/Whisper, salah eja kata serapan, tanda baca dasar, dan penulisan nama orang/brand/istilah sesuai konteks video.\n"
        "2. PERTAHANKAN gaya bahasa bicara nonformal apa adanya (tetap pertahankan 'gue', 'elo', 'nggak', 'gak', 'banget', 'sih', 'nih', 'tuh', 'gitu', 'kayak', 'bikin', 'sampe'). Jangan mengubah menjadi bahasa baku formal!\n"
        "3. DILARANG meringkas, menghapus kata penting, memotong kalimat, atau menggabungkan baris.\n"
        "4. Kembalikan HANYA baris `index` yang memang perlu diperbaiki ejaannya dalam JSON `{corrections: [{index, text}]}`."
    )


def _proofread_reminder_text() -> str:
    return (
        '\n\nFormat JSON WAJIB: {"corrections": [{"index": 0, "text": "Teks yang diperbaiki"}]}. '
        "Kembalikan hanya JSON valid."
    )


def _is_safe_correction(orig_text: str, new_text: str) -> bool:
    orig_words = orig_text.split()
    new_words = new_text.split()
    if not new_words:
        return False
    # Reject accidental summarization or wild hallucinated expansion
    if len(orig_words) >= 3:
        ratio = len(new_words) / float(len(orig_words))
        if ratio < 0.55 or ratio > 1.75:
            return False
    # Reject completely unrelated replacement on multi-word utterances
    if len(orig_words) >= 4:
        sim = SequenceMatcher(None, orig_text.lower(), new_text.lower()).ratio()
        if sim < 0.45:
            return False
    return True


def _call_proofread_batch_sync(
    batch_items: list[dict[str, Any]],
    download: DownloadResult,
    settings: AppSettings,
) -> dict[int, str]:
    sys_prompt = _build_proofread_system_prompt(download)
    user_prompt = json.dumps({"segments": batch_items}, ensure_ascii=False)

    if settings.llm_provider == "openai_compatible":
        from atro.modules.analyzer.openai_compat import call_openai_structured_json_sync

        res = call_openai_structured_json_sync(
            base_url=(settings.openai_base_url or "").strip(),
            api_key=settings.resolve_openai_api_key(),
            model_name=(settings.openai_model or "").strip(),
            system_instruction=sys_prompt,
            user_prompt=user_prompt,
            schema_model=TranscriptProofreadBatchSchema,
            schema_name="transcript_proofread",
            json_schema=_OPENAI_PROOFREAD_JSON_SCHEMA,
            schema_reminder=_proofread_reminder_text(),
            timeout=90.0,
        )
    else:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.resolve_gemini_api_key())
        config = types.GenerateContentConfig(
            system_instruction=sys_prompt,
            response_mime_type="application/json",
            response_schema=TranscriptProofreadBatchSchema,
        )
        assert config.temperature is None
        assert config.top_p is None
        assert config.top_k is None
        model_name = (settings.gemini_model or "").strip() or "gemini-4-neon"
        resp = client.models.generate_content(
            model=model_name,
            contents=user_prompt,
            config=config,
        )
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, TranscriptProofreadBatchSchema):
            res = parsed
        elif isinstance(parsed, dict):
            res = TranscriptProofreadBatchSchema.model_validate(parsed)
        else:
            raw_txt = getattr(resp, "text", None) or "{}"
            res = TranscriptProofreadBatchSchema.model_validate_json(raw_txt)

    return {int(c.index): _MULTI_SPACE_RE.sub(" ", c.text or "").strip() for c in res.corrections}


async def proofread_transcript_segments_with_llm(
    segments: list[TranscriptSegment],
    download: DownloadResult,
    settings: AppSettings,
    logger: JobLogger,
    batch_size: int = 75,
) -> list[TranscriptSegment]:
    """Use active LLM provider to fix contextual ASR typos while guarding timestamps safely."""
    if not segments or not getattr(settings, "enable_transcript_proofread", True):
        return segments
    ready, _ = settings.is_active_provider_ready()
    if not ready:
        return segments

    out = [s.model_copy(deep=True) for s in segments]
    corrected_count = 0

    for start_i in range(0, len(out), max(10, batch_size)):
        chunk = out[start_i : start_i + max(10, batch_size)]
        payload = [{"index": s.index, "text": s.text} for s in chunk]
        try:
            mapping = await asyncio.to_thread(_call_proofread_batch_sync, payload, download, settings)
        except Exception as exc:
            logger.warning("transcriber", f"Koreksi typo AI dilewati pada batch {start_i}: {exc}")
            continue

        for seg in chunk:
            fixed = mapping.get(seg.index)
            if not fixed or fixed == seg.text:
                continue
            fixed = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", fixed).strip()
            if not _is_safe_correction(seg.text, fixed):
                continue
            seg.words = (
                align_words_preserving_timestamps(seg.words, seg.start, seg.end, fixed)
                if seg.words
                else []
            )
            seg.text = fixed
            corrected_count += 1

    if corrected_count > 0:
        logger.info(
            "transcriber",
            f"Koreksi typo transkrip AI memperbaiki {corrected_count} segmen (timestamp kata tetap presisi).",
        )
    return out
