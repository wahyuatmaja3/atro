# Changelog

## v2.0.0 (2026-11-22)

Rilis besar **ATRO v2.0.0 (AutoClip Studio V2 + Gaya GiNUX)**.

### Fitur Baru
- **Speaker Intelligence & POI Framing (`V2/F3`)**:
  - Offline CPU speaker diarization berbasis `sherpa-onnx` + fallback spektral otomatis.
  - Klasterisasi identitas wajah lintas kamera dengan YuNet + SFace.
  - `ShotPlanner` sadar peran percakapan (*speaker*, *reaction*, *group*) dengan proteksi hysteresis anti ping-pong.
- **Transisi Multi-Shot & Audio Mixer (`V2/F1–F2`)**:
  - Dukungan 12 mode transisi video antar-shot (`cut`, `crossfade`, `fade_black`, `slide_*`, `zoom_*`, `whip`, `flash`, `auto`).
  - Mixing offline SFX transisi dan audio loudness normalization (`-14 LUFS`) beserta *auto-ducking* backsound.
- **28 Dynamic Template Packs & 8 Resep Caption (`V2/F4`)**:
  - Katalog JSON di `atro/assets/templates/` dalam 9 kategori, pencarian & filter galeri, favorit, rekomendasi otomatis dari konteks klip, dan ekspor/impor paket `.atrotpl`.
- **UI Studio Modern & Publish Kit (`V2/F5–F6`)**:
  - Mode **Easy / Pro**, Fluent Dark/Light theme tokens, toast notifikasi, *floating action bar*, dan pesan error yang mudah dipahami.
  - Preset target **YouTube Shorts** & **TikTok**, *Topic Outline* pembahasan, selector alternatif klip, **Demo Offline 5 Pembicara / 3 Kamera Tanpa API Key**, cover generator, dan ekspor **Publish Kit ZIP**.
- **Paket Gaya "GiNUX" (`imGinux3`) & Editor Komedi Cepat**:
  - **3 Template Baru (`G1`)**: `ginux_clip_meme`, `ginux_reaction_zoom`, dan `ginux_drama_banner` dengan default font **Impact**, sorotan kuning `#FACC15`, tanpa banner/emoji berlebihan.
  - **Jump-Cut Otomatis (`G2`)**: Pemangkasan jeda hening (`silence_trim_enabled`) dan kata *filler* dari transkrip kata Whisper.
  - **Zoom Punch Berulang (`G3`)**: Pengulangan efek punch berkala (`zoom_punch_repeat_sec`) sepanjang durasi klip di renderer & preview.
  - **Caption `above_speaker` (`G8`)**: Penempatan teks subtitle instan di atas kepala pembicara aktif dengan clamp zona aman Shorts, konsisten antara GUI PreviewCanvas dan render ASS/FFmpeg.
  - **Custom Meme SFX & Contextual SFX Cues (`G4`)**: Pendeteksian folder `atro/assets/sfx/`, `sfx/`, dan `workspace/sfx/` beserta 50+ koleksi meme SFX bawaan, rencana `sfx_cues` AI maupun heuristik otomatis, dan tabel editor SFX di Tab Audio Studio.

### Perbaikan
- Memperbaiki `_load_offline_demo_project` di `atro/ui/main_window.py` agar membaca `preflight.ffmpeg.path` secara benar.
- Memperbarui `build/atro.spec` agar membundel aset template JSON, folder SFX, dan model diarization ke build Windows PyInstaller.
- Memperbaiki *audio resampling* (`aresample=48000`) sebelum pencampuran stream SFX di FFmpeg.
