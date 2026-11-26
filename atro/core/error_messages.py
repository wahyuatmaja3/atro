"""Human-friendly Indonesian error message mapping and recovery hints for ATRO."""

from __future__ import annotations

_ERROR_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("402", "insufficient balance", "insufficient_balance", "billing", "credit"),
        "Saldo / kredit API AI tidak mencukupi (Insufficient Balance).",
        "Isi saldo provider AI Anda atau ganti provider/model di Pengaturan (atau gunakan Ollama lokal).",
    ),
    (
        ("gemini_api_key", "openai_api_key", "api key", "api_key", "401", "unauthorized", "invalid key"),
        "Kunci API AI belum valid atau belum diisi.",
        "Buka Pengaturan di pojok kanan atas, lalu periksa kembali API Key Gemini atau Endpoint OpenAI Anda.",
    ),
    (
        ("quota", "429", "rate limit", "resource_exhausted", "too many requests"),
        "Batas kuota permintaan AI sedang penuh (Rate Limit).",
        "Tunggu sekitar 1 menit lalu klik 'Lanjutkan (Resume)', atau ganti provider/model AI di Pengaturan.",
    ),
    (
        ("no such file", "filenotfounderror", "tidak ditemukan", "cannot find"),
        "File video atau aset yang dibutuhkan tidak ditemukan di disk.",
        "Pastikan file video belum dipindahkan/dihapus, lalu pilih ulang file video sumber.",
    ),
    (
        ("ffmpeg", "exit", "broken pipe", "invalid data found"),
        "Gagal memproses atau merender video di mesin FFmpeg.",
        "Periksa apakah video sumber tidak rusak dan ruang disk masih cukup, lalu coba render ulang.",
    ),
    (
        ("yt-dlp", "sign in to confirm", "video unavailable", "private video", "403", "urlopen"),
        "Video YouTube gagal diunduh (terkunci, privat, atau jaringan terputus).",
        "Coba periksa link YouTube atau unduh videonya terlebih dahulu lalu gunakan tab 'Dari File Komputer'.",
    ),
    (
        ("cuda", "cublas", "cudnn", "out of memory", "vram"),
        "Memori GPU / runtime CUDA penuh atau belum lengkap.",
        "ATRO akan otomatis beralih ke mode CPU yang aman. Anda juga bisa memilih model Whisper 'small' atau perangkat 'cpu' di Pengaturan.",
    ),
    (
        ("transkrip kosong", "empty transcript", "no speech"),
        "Tidak ada suara percakapan yang terdeteksi di video.",
        "Pastikan video memiliki audio ucapan yang jelas atau sertakan file subtitle (.srt/.vtt) secara manual.",
    ),
    (
        ("dibatalkan", "cancelled"),
        "Proses dihentikan atas permintaan Anda.",
        "Anda dapat menekan 'Lanjutkan (Resume)' kapan saja untuk meneruskan dari tahap terakhir.",
    ),
)


def humanize_error(raw_error: str) -> tuple[str, str]:
    """Translate a technical error string into `(headline_id, action_hint_id)`."""
    text = (raw_error or "").strip()
    low = text.lower()
    if not text:
        return (
            "Terjadi gangguan saat memproses video.",
            "Coba tekan 'Lanjutkan (Resume)' atau cek Log Proses untuk detail teknis.",
        )

    for triggers, headline, hint in _ERROR_RULES:
        if any(tok in low for tok in triggers):
            return headline, hint

    first_line = text.splitlines()[0].strip()
    short = first_line[:160] + ("..." if len(first_line) > 160 else "")
    return (
        f"Proses terhenti: {short}",
        "Coba tekan 'Lanjutkan (Resume)' atau buka Pengaturan jika berhubungan dengan koneksi/API.",
    )
