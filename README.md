# ATRO — AutoClip & Short Studio Lokal (v2.0.0)

**ATRO** adalah aplikasi desktop (PyQt6) sekaligus CLI lokal untuk mengubah video panjang (YouTube maupun file video lokal) menjadi klip vertikal **YouTube Shorts** dan **TikTok** secara end-to-end dengan transkrip Whisper lokal, analisis momen viral AI, *speaker intelligence*, transisi multi-shot, mixer SFX, dan Studio editor WYSIWYG `9:16`.

---

## Sorotan Fitur v2.0.0

### 1. Pipeline End-to-End Lokal + Asinkron
- **Download / Input Lokal**: Mendukung URL YouTube via `yt-dlp` serta file video lokal (`.mp4`, `.mkv`, `.mov`, dll.) beserta subtitle opsional (`.vtt` / `.srt`).
- **Transkripsi Lokal (`faster-whisper`)**: Menghasilkan segmen beserta timestamp per kata (*word-level timestamps*) tanpa mengirim audio penuh ke server pihak ketiga. Model dapat dipilih (`tiny`, `base`, `small`, `medium`, `large-v3-turbo`) dengan deteksi `cpu`/`cuda` otomatis.
- **Analisis Momen Viral Fleksibel (Gemini & OpenAI-Compatible)**:
  - Mendukung Google **Gemini** (`GEMINI_API_KEY`) atau endpoint **OpenAI-Compatible** seperti OpenAI, OpenRouter, Groq, LM Studio, dan **Ollama** lokal (`http://localhost:...` tidak memerlukan API key).
  - Menghasilkan daftar kandidat klip, skor viralitas, *topic outline* pembahasan, *focus hints* pembicara, serta rencana **SFX kontekstual** sesuai momen percakapan (`sfx_cues`).
- **Preset Platform**: Mode **YouTube Shorts** (`<= 58s`, hashtag Shorts) dan **TikTok** (`<= 90s`, hashtag TikTok/FYP).

### 2. Speaker Intelligence & Framing Multi-Kamera (POI)
- **Offline CPU Speaker Diarization**: Pemisahan giliran bicara pembicara berbasis `sherpa-onnx` (`pyannote-segmentation-3.0` + `speaker_embed.onnx`) dengan fallback spektral otomatis.
- **Deteksi & Identitas Wajah**: Model `YuNet` (`face_detection_yunet_2023mar.onnx`) + embedding `SFace` (`face_recognition_sface_2021dec.onnx`) untuk mencocokkan orang yang sama lintas *angle* kamera.
- **Strategi Framing (`ShotPlanner`)**: `smart_face` (aktif pembicara/reaksi + hysteresis anti ping-pong), `fit_all_blur` / `blurred_frame` (wide frame saat >1 subjek), `center_crop`, hingga `split_top_bottom` dan `pip_facecam`.

### 3. Template Gaya "GiNUX" (imGinux3) & Editing Komedi Cepat
Ditambahkan 3 template khusus pada kategori **Komedi** plus mesin editor khusus:
- `ginux_clip_meme` (**Short Meme Cut — Gaya GiNUX**): Pemotongan dead air & filler otomatis (`silence_trim_enabled`), *zoom punch* berulang, caption Impact kuning (`#FACC15`) tepat di atas pembicara aktif, dan SFX meme kontekstual.
- `ginux_reaction_zoom` (**Short Reaksi Cepat — Gaya GiNUX**): Fokus mengikuti pembicara/ekspresi dengan interval *zoom punch* cepat, vignette, dan jump-cut rapat.
- `ginux_drama_banner` (**Short Drama Podcast — Gaya GiNUX**): Varian dengan *hook banner* pembuka, jump-cut, dan teks pembicara dinamis.
- **Posisi Caption `above_speaker`**: Koordinat teks mengikuti batas kepala pembicara aktif di frame akhir (bukan hanya tengah layar), tetap aman di area aman Shorts, dan berpindah instan mengikuti pergantian pembicara bahkan saat kamera *wide/zoom-out* dua orang berargumen.
- **Jump-Cut Jeda & Filler (`jump_cut.py`)**: Menghapus jeda hening panjang (`silence_max_gap_sec`) dan kata pengisi (seperti *eee*, *anu*, *uh*, *um*) sebelum penyusunan timeline akhir.
- **Zoom Punch Berulang**: Interval pengulangan `zoom_punch_repeat_sec` (bisa diatur di Studio atau JSON template) di seluruh durasi klip, selain klimaks tunggal.
- **Custom Meme SFX & Contextual Cues**: SFX bawaan (`whoosh`, `swipe`, `pop`), folder aset `atro/assets/sfx/` & `sfx/` pengguna (`.wav`, `.mp3`, `.ogg`, `.flac`, `.m4a`, `.aac`), serta pemicu otomatis berbasis momen AI atau heuristik lokal.

### 4. Transisi Multi-Shot, Audio Broadcast & Studio WYSIWYG 1:1
- **Transisi Video & SFX**: `cut`, `crossfade`, `fade_black`, `slide_left`, `slide_right`, `slide_up`, `slide_down`, `zoom_in`, `zoom_out`, `whip`, `flash`, dan `auto`.
- **Mixing Audio Lengkap**: Pembuka suara judul TTS (`edge-tts`, default `id-ID-GadisNeural`), musik latar lokal dengan auto-*ducking* + normalisasi `-14 LUFS`, serta SFX transisi dan SFX kontekstual dengan *limiting* aman.
- **28 Template Pack JSON & 8 Resep Caption**: Tercakup 9 kategori (`Podcast`, `Edukasi`, `Bisnis`, `Storytime`, `Komedi`, `Gaming`, `Berita`, `Religi`, `Musik/Vlog`), filter galeri, favorit, rekomendasi AI, serta ekspor/impor paket `.atrotpl`.
- **8 Resep Caption**: `hormozi_viral`, `mrbeast_pill`, `lokaclip_neon`, `mageran_glass`, `abdaal_minimal`, `gamer_split`, `streamer_kinetic`, `podcast_editorial`.
- **Clip Studio 6 Tab + Preview Canvas Real-Time**:
  1. `1. Template` · `2. Tata Letak` · `3. Teks Subtitle` · `4. Judul & Komentar` · `5. Musik & Suara` · `6. Edit Kata`
  - Sinkronisasi presisi antara preview `QPainter` di GUI dan hasil render FFmpeg/ASS.
  - Mode **Easy / Pro**, tema **Dark / Light** Fluent, toast notifikasi, *batch edit & re-render*, **Publish Kit ZIP** (video + cover + copywriting hook & hashtag otomatis), serta tombol **Demo Offline** tanpa memerlukan API key.

---

## Prasyarat Sistem

- **OS**: Windows 10/11 (GUI PyQt6 + skrip build `.exe`), kompatibel juga dijalankan di Python 3.11+ lintas OS.
- **Python**: `>= 3.11`
- **Binary Eksternal** (letakkan di `PATH` sistem **atau** di folder `./bin/` di akar proyek):
  - [`ffmpeg` dan `ffprobe`](https://ffmpeg.org/) (termasuk `libass` / `subtitles` filter & `libx264` untuk burn-in subtitle dan filter kompleks)
  - [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (untuk mengunduh URL YouTube)

> Jalankan preflight check kapan saja untuk memverifikasi `yt-dlp`, `ffmpeg`, dan `ffprobe`:
> ```bash
> python run_atro.py --check
> ```

---

## Instalasi & Menjalankan

### 1. Setup Virtual Environment

```powershell
git clone https://github.com/wahyuatmaja3/atro.git
cd atro
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

### 2. Konfigurasi Environment (`.env`)

Salin `.env.example` menjadi `.env`, lalu isi sesuai provider AI Anda:

```dotenv
ATRO_LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
ATRO_WHISPER_MODEL=small
ATRO_WORKSPACE_DIR=
ATRO_BACKSOUND_DIR=
```

Semua pengaturan ini juga dapat diubah langsung lewat tombol **Pengaturan** di UI (tersimpan di `config.json`).

### 3. Menjalankan Desktop Studio (GUI)

```powershell
python run_atro.py
```

Belum punya API Key? Klik **Coba Project Demo (Tanpa API Key)** di Langkah 1 untuk langsung menguji podcast demo offline 5 orang / 3 kamera di Clip Studio dan mengekspor Publish Kit.

### 4. Menjalankan Mode CLI (Headless)

```powershell
# Cek kesiapan dependensi (yt-dlp, ffmpeg, ffprobe)
python run_atro.py check --output preflight.json

# Jalankan pipeline dari URL YouTube
python run_atro.py run "https://www.youtube.com/watch?v=VIDEO_ID" --clips 4 --min-dur 30 --max-dur 60 --aspect 9:16 --framing smart_face

# Jalankan pipeline dari video lokal + subtitle opsional
python run_atro.py run --file "D:\video\podcast.mp4" --sub "D:\video\podcast.srt" --provider openai_compatible --base-url "http://localhost:11434/v1" --model "qwen2.5:14b"

# Lihat daftar job dan lanjutkan job dari artifact terakhir
python run_atro.py jobs
python run_atro.py resume <job_id>
```

---

## Folder Aset Musik & SFX Kustom

- **Musik Latar (`backsound/`)**: Letakkan file audio `.mp3` / `.wav` / `.m4a` ke folder `backsound/` (atau path `ATRO_BACKSOUND_DIR`). ATRO akan melakukan looping dan *auto-ducking* otomatis saat suara pembicara aktif.
- **SFX Meme Kustom (`atro/assets/sfx/` atau `sfx/` / `workspace/sfx/`)**:
  - ATRO sudah menyertakan SFX transisi (`whoosh`, `swipe`, `pop`) serta pustaka meme SFX di `atro/assets/sfx/`.
  - Anda bisa menambah file `.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a`, atau `.aac` sendiri; nama file otomatis muncul di dropdown SFX Studio dan dikenali oleh perencana `sfx_cues` AI.

---

## Build Windows `.exe` (PyInstaller)

Pastikan aplikasi `ATRO.exe` tidak sedang berjalan, lalu jalankan:

```powershell
# Default (--onedir) -> dist\ATRO\ATRO.exe (aman tanpa menimpa workspace/config user)
.\build\build_exe.ps1

# Opsi single executable (--onefile) -> dist\ATRO.exe
.\build\build_exe.ps1 -OneFile
```

`build/atro.spec` otomatis membundel model `.onnx` (YuNet, SFace, PyAnnote/CAM++ diarization), template JSON bawaan (`atro/assets/templates/`), dan aset SFX (`atro/assets/sfx/`).

---

## Struktur Proyek

```text
atro/
├── run_atro.py                 # Entrypoint GUI (default) & CLI (jika ada argumen)
├── atro/
│   ├── cli.py                  # Parser CLI: check, jobs, run, resume
│   ├── config.py               # Load/save AppSettings (.env & config.json)
│   ├── assets/
│   │   ├── *.onnx              # Model deteksi wajah YuNet & identifikasi SFace
│   │   ├── diarization/        # Model ONNX sherpa-onnx speaker diarization
│   │   ├── sfx/                # SFX transisi & meme kustom bawaan
│   │   └── templates/          # 28 template pack JSON bawaan
│   ├── core/
│   │   ├── composer_schema.py  # Skema lengkap ClipComposerConfig & validasi
│   │   ├── presets.py          # 4 preset inti & 8 resep caption viral
│   │   ├── template_packs.py   # Registry 28 template pack, .atrotpl, thumbnail WYSIWYG
│   │   ├── publish_kit.py      # Generator cover, copywriting sosial & export ZIP
│   │   └── demo_sample.py      # Generator proyek podcast demo offline
│   ├── modules/
│   │   ├── downloader/         # Wrapper yt-dlp + import video lokal
│   │   ├── transcriber/        # Integrasi faster-whisper word-level
│   │   ├── analyzer/           # Prompt, validator & layanan Gemini / OpenAI-Compat
│   │   └── cutter/             # Jump-cut, shot planner, SFX mixer, ASS & FFmpeg renderer
│   ├── pipeline/               # Orchestrator asinkron & state store per job
│   └── ui/                     # MainWindow 3-langkah, ClipStudio 6-tab & PreviewCanvas
├── build/                      # Skrip build_exe.ps1 & atro.spec
├── docs/                       # Rencana arsitektur V2 & PRD Template GiNUX
└── tests/                      # Suite pengujian pytest (unit, UI & FFmpeg render)
```

---

## Pengujian

Jalankan seluruh suite pengujian:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```
