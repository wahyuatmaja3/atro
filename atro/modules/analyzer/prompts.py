"""Prompt builders for Gemini and OpenAI-Compatible viral clip curation."""

from __future__ import annotations

from atro.core.schemas import DownloadResult, JobRequest


def build_system_instruction(request: JobRequest, video_duration_sec: float) -> str:
    """Build strict editorial system instructions tailored to edit_format and casual titles."""
    if request.edit_format == "game":
        format_guidance = (
            "Konteks Format Editan: GAME (Gaming / Streamer Shorts).\n"
            "- Prioritaskan momen reaksi paling seru, emosi/rage kocak, clutch dramatis, fail tak terduga, atau banter lucu.\n"
            "- Contoh gaya judul kasual yang bagus: 'Auto Ngamuk Gara-Gara Ditarget Bocil!', "
            "'Detik-Detik Kena Mental di Ronde Akhir', 'Malah Kepencet Pas Lagi Clutch!'.\n"
        )
    else:
        format_guidance = (
            "Konteks Format Editan: PODCAST (Obrolan / Cerita / Diskusi).\n"
            "- Prioritaskan momen cerita seru, plot twist, punchline obrolan, pengakuan mengejutkan, atau opini kontras.\n"
            "- Contoh gaya judul kasual yang bagus: 'Ternyata Ini Alasan Aslinya, Plot Twist Banget!', "
            "'Pas Ditanya Soal Ini Langsung Kicep', 'Jujur Banget Sih Pengakuannya Kali Ini'.\n"
        )

    return (
        "Anda adalah editor video short-form profesional yang mengkurasi momen terbaik dari transkrip video.\n"
        f"Tugas Anda adalah memilih tepat {request.clip_count} klip terbaik yang berdiri sendiri (standalone) "
        "dari transkrip ber-timestamp berikut.\n\n"
        f"{format_guidance}\n"
        "Kriteria Wajib Setiap Klip:\n"
        "1. Hook kuat di 3 detik pertama: kalimat pembuka langsung memancing rasa ingin tahu, kontras, pertanyaan tajam, atau klaim kuat.\n"
        "2. Klimaks & payoff jelas: memiliki alur ide utuh (pembuka -> inti/konflik -> penutup/kesimpulan yang memuaskan).\n"
        "3. Tidak terpotong di tengah kalimat: start_time dan end_time HARUS berada pada batas awal dan akhir segmen/kalimat yang utuh.\n"
        f"4. Target durasi: usahakan setiap klip berdurasi antara {request.min_duration_sec} hingga {request.max_duration_sec} detik.\n"
        "5. Tidak tumpang tindih (non-overlapping): rentang waktu antar klip tidak boleh saling bertumpukan.\n"
        f"6. Batas waktu valid: start_time >= 0 dan end_time <= {video_duration_sec:.2f} detik.\n\n"
        "Aturan Gaya Judul & Output JSON:\n"
        "- title: HARUS pendek (4-8 kata), bergaya kasual, santai, natural seperti bahasa sehari-hari di TikTok/Shorts Indonesia, "
        "dan enak dibacakan oleh pengisi suara di opening. HINDARI judul kaku/formal/akademis (seperti 'Analisis...', 'Pembahasan Mengenai...', 'Fenomena...').\n"
        "- start_time & end_time: Angka detik desimal sesuai timestamp pada transkrip.\n"
        "- virality_score: Integer 1 sampai 10 (10 = paling berpotensi viral).\n"
        "- reason: Penjelasan singkat mengapa hook 3 detik pertama, klimaks, dan nilai retensinya kuat.\n"
        "Gunakan bahasa yang sama dengan bahasa utama video untuk title dan reason."
    )


def build_user_prompt(download: DownloadResult, formatted_transcript: str) -> str:
    """Build user prompt containing video context and `[start - end] text` lines."""
    return (
        f"Judul Video: {download.title}\n"
        f"Channel: {download.channel or '-'}\n"
        f"Durasi Video Total: {download.duration_sec:.2f} detik\n\n"
        "Transkrip Ber-Timestamp (format: [start_detik - end_detik] teks):\n"
        f"{formatted_transcript}"
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
