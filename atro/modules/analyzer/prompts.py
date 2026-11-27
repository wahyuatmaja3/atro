"""Prompt builders for Gemini and OpenAI-Compatible viral clip curation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

from atro.core.schemas import DownloadResult, JobRequest
from atro.modules.cutter.sfx import format_sfx_catalog_for_prompt


def build_system_instruction(
    request: JobRequest,
    video_duration_sec: float,
    available_sfx: Optional[Sequence[str]] = None,
) -> str:
    """Build strict editorial system instructions tailored to edit_format and casual titles."""
    if request.edit_format == "game":
        format_guidance = (
            "Konteks Format Editan: GAME (Gaming / Streamer Shorts).\n"
            "- Prioritaskan momen reaksi paling seru, emosi/rage kocak, clutch dramatis, fail tak terduga, atau banter lucu.\n"
            "- Contoh gaya judul kasual nonformal yang bagus: 'Auto Ngamuk Gara-Gara Ditarget Bocil!', "
            "'Detik-Detik Kena Mental di Ronde Akhir', 'Malah Kepencet Ulti Pas Lagi Clutch!'.\n"
        )
    else:
        format_guidance = (
            "Konteks Format Editan: PODCAST (Obrolan / Cerita / Diskusi).\n"
            "- Prioritaskan momen cerita seru, plot twist, punchline obrolan, pengakuan mengejutkan, atau opini kontras.\n"
            "- Contoh gaya judul kasual nonformal yang bagus (HARUS nempel ke topik spesifik videonya!): "
            "'Beli Tas 200 Juta Ke Kampus, Auto Bikin Kesel!', "
            "'Keceplosan Spill Gaji Pertama, Satu Studio Langsung Kicep!', "
            "'Niatnya Ngirit Malah Boncos Dua Kali Lipat!'.\n"
        )

    plat_label = (
        "TikTok (ritme storytelling kuat, pacing dinamis 20-120 detik)"
        if getattr(request, "platform_target", "youtube_shorts") == "tiktok"
        else "YouTube Shorts (hook super tajam di 1.5 detik pertama, padat <= 60 detik)"
    )
    excluded = getattr(request, "excluded_time_spans", None) or []
    excluded_note = ""
    if excluded:
        spans_str = ", ".join(
            f"[{s[0]:.1f}s - {s[1]:.1f}s]"
            for s in excluded
            if isinstance(s, (list, tuple)) and len(s) >= 2
        )
        if spans_str:
            excluded_note = (
                f"\n7. KLIP ALTERNATIF: HINDARI rentang waktu yang sudah pernah diklip sebelumnya: {spans_str}. "
                "Cari sudut pandang atau momen menarik lain di luar rentang tersebut.\n"
            )

    sfx_list = [s.strip() for s in (available_sfx or []) if s and s.strip()]
    sfx_guidance = ""
    if sfx_list:
        sfx_catalog_text = format_sfx_catalog_for_prompt(sfx_list, edit_format=request.edit_format)
        gaming_sfx_note = (
            "Untuk klip GAMING, prioritaskan momen clutch, rage, fail, sus/impostor, jumpscare, loot, disconnect, "
            "diam canggung, atau freeze punchline agar SFX & zoom-in/out terasa hidup.\n"
            if request.edit_format == "game"
            else ""
        )
        sfx_guidance = (
            "- sfx_cues (opsional): Pilih 1-5 momen punchline, kaget, canggung, tertawa, fail, clutch, atau penekanan cerita "
            "di dalam klip untuk dipicu efek suara meme (t_sec dalam detik absolut transkrip, sfx_name, reason). "
            f"{gaming_sfx_note}"
            "Berikan jarak minimal 2.5 detik antar SFX dan HANYA gunakan nama persis dari daftar SFX & konteks pemakaian berikut:\n"
            f"{sfx_catalog_text}\n"
        )

    return (
        "Anda adalah editor video short-form profesional yang mengkurasi momen terbaik dari transkrip video.\n"
        f"Target Platform Utama: {plat_label}.\n"
        f"Tugas Anda adalah memilih tepat {request.clip_count} klip terbaik yang berdiri sendiri (standalone) "
        "dari transkrip ber-timestamp berikut.\n\n"
        f"{format_guidance}\n"
        "Kriteria Wajib Setiap Klip:\n"
        "1. Hook kuat di 3 detik pertama: kalimat pembuka langsung memancing rasa ingin tahu, kontras, pertanyaan tajam, atau klaim kuat.\n"
        "2. Klimaks & payoff jelas: memiliki alur ide utuh (pembuka -> inti/konflik -> penutup/kesimpulan yang memuaskan).\n"
        "3. Tidak terpotong di tengah kalimat: start_time dan end_time HARUS berada pada batas awal dan akhir segmen/kalimat yang utuh.\n"
        f"4. Target durasi: usahakan setiap klip berdurasi antara {request.min_duration_sec} hingga {request.max_duration_sec} detik.\n"
        "5. Tidak tumpang tindih (non-overlapping): rentang waktu antar klip tidak boleh saling bertumpukan.\n"
        f"6. Batas waktu valid: start_time >= 0 dan end_time <= {video_duration_sec:.2f} detik.{excluded_note}\n\n"
        "Aturan Gaya Judul & Output JSON:\n"
        "- title: HARUS pendek (4-8 kata), bergaya kasual NONFORMAL seperti bahasa obrolan sehari-hari anak muda di TikTok/Shorts Indonesia, "
        "kreatif, memancing penasaran tanpa terasa clickbait palsu, dan enak dibacakan oleh pengisi suara di opening.\n"
        "  • PAKAI diksi nonformal yang hidup: 'gue/aku', 'lu/lo', 'gak/nggak', 'sampe', 'bikin', 'malah', 'gara-gara', 'parah sih', 'kok bisa', 'kicep', 'spill', 'auto', 'beneran'.\n"
        "  • WAJIB SPESIFIK SESUAI ISI VIDEO: Judul harus menyebut kejadian, objek, angka, nama orang, atau konflik nyata yang BENAR-BENAR dibahas di rentang `[start_time - end_time]` klip itu (DILARANG judul generik seperti 'Momen Seru Podcast' atau 'Ternyata Ini Alasannya' yang tidak menjelaskan isi klip!).\n"
        "  • VARIASI KREATIF ANTAR KLIP: Jangan mengulang awalan/pola kalimat yang sama di beberapa klip (kombinasikan sudut reaksi lucu, plot twist kontras, kutipan nyelekit, atau blunder tak terduga).\n"
        "  • HINDARI judul kaku/formal/akademis (seperti 'Analisis...', 'Pembahasan Mengenai...', 'Fenomena...', 'Mengapa...', 'Cara Melakukan...', 'Hal Tersebut').\n"
        "- start_time & end_time: Angka detik desimal sesuai timestamp pada transkrip.\n"
        "- virality_score: Integer 1 sampai 10 (10 = paling berpotensi viral).\n"
        "- reason: Penjelasan singkat mengapa hook 3 detik pertama, klimaks, dan nilai retensinya kuat.\n"
        "- focus_hints (opsional): Daftar segmen titik fokus perbincangan (t_start, t_end, role: 'speaker'|'reaction'|'group', target_hint, reason) bila ada momen penting menyorot reaksi atau semua peserta.\n"
        f"{sfx_guidance}"
        "- topic_outline (opsional): Daftar topik pembahasan utama video (topic, start_time, end_time, summary).\n"
        "Gunakan bahasa yang sama dengan bahasa utama video untuk title dan reason."
    )


def build_user_prompt(download: DownloadResult, formatted_transcript: str) -> str:
    """Build user prompt containing video context and `[start - end] text` lines."""
    return (
        f"Judul Video: {download.title}\n"
        f"Channel: {download.channel or '-'}\n"
        f"Durasi Video Total: {download.duration_sec:.2f} detik\n\n"
        "Transkrip Ber-Timestamp (format: [start_detik - end_detik] teks):\n"
        f"{formatted_transcript}\n\n"
        "Catatan Penting: Pastikan setiap `title` klip memakai bahasa santai nonformal, kreatif bervariasi, "
        "dan secara akurat mencerminkan kejadian/topik spesifik yang terucap pada segmen timestamp klip tersebut."
    )


def build_json_schema_reminder() -> str:
    """Return explicit JSON contract reminder for OpenAI-compatible json_object/text modes."""
    return (
        "\n\nFormat JSON WAJIB (kembalikan HANYA objek JSON valid tanpa teks pembuka/penutup atau markdown):\n"
        "{\n"
        '  "clips": [\n'
        "    {\n"
        '      "title": "Judul santai 4-8 kata",\n'
        '      "start_time": 12.5,\n'
        '      "end_time": 55.2,\n'
        '      "virality_score": 9,\n'
        '      "reason": "Alasan hook 3 detik pertama dan klimaks"\n'
        "    }\n"
        "  ]\n"
        "}"
    )
