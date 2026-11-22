# ATRO — Rencana Perbaikan V2 (Belum Dieksekusi)

> Dokumen ini hanya berisi **analisis & rencana**. Tidak ada kode yang diubah.
> Tanggal: 2026-11-22

## 0. Catatan Sumber Referensi (Jujur Soal Batasan)

| Sumber | Status | Keterangan |
| --- | --- | --- |
| `github.com/zhouxiaoka/autoclip` | **Terbaca** | README (ZH/EN) dan CHANGELOG dibaca langsung. Semua perbandingan di bagian 6 berdasar ini. |
| Post X `@e1genqq/status/2123528652595798344` | **Tidak bisa dibuka** | Halaman X butuh login. Dari hasil pencarian saja: post itu membahas alur kerja "studio klip" berbasis ±20 repo GitHub (mis. `yt-dlp` → `OpenCut` timeline editor → tool AI/otomasi lain). **Belum terverifikasi.** |
| Threads `@malikisaputra/post/Df_93F6Dn0o` | **Tidak bisa dibuka** | Tidak terindeks publik, isinya tidak diketahui. |

**Tindakan untuk Anda:** kalau dua post itu penting, salin teks/screenshot-nya ke chat. Saya akan perbarui bagian 6.4.

---

## 1. Ringkasan Prioritas

| # | Area | Dampak | Usaha | Prioritas |
| --- | --- | --- | --- | --- |
| A | Fokus pembicara & *point of interest* (POI), terutama podcast ≥3 orang | Sangat tinggi (inti kualitas klip) | Besar | **P0** |
| B | Transisi antar-shot (slide, fade, zoom, whip, dll.) | Tinggi | Sedang | **P0** |
| C | Rombak UI: bersih, halus, tidak penuh | Tinggi | Besar | **P1** |
| D | Perbanyak template (gallery + pack) | Sedang–tinggi | Sedang | **P1** |
| E | Fitur yang kurang dibanding AutoClip (publish kit, cover, preset platform, dll.) | Sedang–tinggi | Besar | **P2** |
| F | Performa & stabilitas (CPU Whisper, render, build exe) | Sedang | Sedang | **P2** |

Urutan kerja disarankan: **A → B → D → C → E → F** (A dan B mengubah kontrak data, sehingga UI baru sebaiknya dibangun di atas kontrak yang sudah final).

---

## 2. Deteksi Wajah Sadar-Konteks (POI) — Area A

### 2.1 Masalah sekarang (dari kode dan uji pada video asli)

1. Pembicara aktif ditentukan **hanya dari gerakan mulut** (`_measure_face_speaking_activity` di `face_tracking.py`). Itu tidak tahu siapa yang *benar-benar bersuara*.
2. Pada uji klip #1 (video podcast asli), beberapa shot punya skor kedua wajah nyaris sama (contoh 9.9 vs 9.1 dan 11.6 vs 13.3). Itu zona rawan salah pilih. Belum ada *ground truth* untuk mengukur akurasi.
3. Tidak ada identitas orang. Saat kamera pindah angle, program tidak tahu "orang kiri di angle A" = "orang yang sama di angle B".
4. Tidak ada pemahaman isi obrolan. Sorotan tidak tahu siapa yang sedang membahas topik inti, siapa yang cuma bereaksi (tertawa, mengangguk).
5. Mode `split_screen`/`fit_all_blur` untuk ≥3 orang hanya memakai jarak horizontal. Tidak ada logika "tampilkan semua" vs "sorot pembicara".
6. Crop statis per shot. Wajah bergerak di dalam shot, tetapi crop tidak mengikuti.
7. Tidak ada *hysteresis*. Pembicara bergantian cepat (menyela, "hmm", tawa) bisa membuat sorotan ping-pong.
8. Sorotan terbatas pada wajah menghadap depan atau profil. Orang menoleh, menunduk, atau membelakangi kamera mudah hilang.
9. Tidak ada cara memperbaiki manual per segmen di Studio kalau deteksi salah.

### 2.2 Rancangan solusi

**Prinsip:** pembicara = *suara* + *wajah* yang saling cocok, lalu dipilih sesuai *peran dalam percakapan*.

#### 2.2.1 Pipeline baru ("Speaker Intelligence")

```
audio.wav ─► VAD + diarization ─► SPEAKER_1..N (timeline suara)
video     ─► deteksi shot ─► deteksi wajah ─► embedding wajah ─► klaster identitas (FACE_A..M)
                                   └─► gerak mulut per wajah (timeline visual)
Korelasi: timeline suara SPEAKER_k  ×  gerak mulut FACE_j  ⇒  peta SPEAKER_k = FACE_j
Transkrip + analisis LLM ─► peran segmen (pembicara inti, penyela, reaksi, momen punchline)
Shot Planner ─► rencana sorot per detik (siapa, framing, durasi minimal, transisi)
```

#### 2.2.2 Komponen

| Komponen | Opsi implementasi | Catatan |
| --- | --- | --- |
| Diarization | `pyannote.audio` atau `sherpa-onnx` (ONNX, lokal, ringan di CPU) | Pilih satu setelah uji kecepatan. Wajib berjalan lokal. |
| Identitas wajah | `cv2.FaceRecognizerSF` (SFace) bersama YuNet yang sudah ada | Tidak menambah dependensi besar. Klaster dengan jarak cosine. |
| Active speaker visual | Pertahankan skor mulut sekarang sebagai sinyal tambahan. Pertimbangkan model ASD ringan (Light-ASD/TalkNet ONNX) bila hasil heuristik kurang. | Skor mulut saja tidak cukup. Digabung dengan suara. |
| Pemetaan suara↔wajah | Korelasi aktivitas suara (per speaker) dengan gerak mulut (per identitas wajah) pada seluruh video, bukan per shot | Sekali dihitung dipakai semua klip di job tersebut. Disimpan di `speaker_map.json`. |
| POI dari isi obrolan | Perluas prompt analyzer: tiap klip juga mengembalikan `focus_plan` (per rentang waktu: pembicara inti, momen reaksi, kata kunci) | **Jangan kirim `temperature`/`top_p`/`top_k` ke `gemini-4-neon`.** Validasi hasil LLM dengan pydantic, fallback ke heuristik bila gagal. |
| Shot Planner | Modul baru `atro/modules/cutter/framing/shot_planner.py` | Menggabungkan semua sinyal menjadi `ShotCropSegment` + transisi. |

#### 2.2.3 Aturan Shot Planner

- Default: sorot **pembicara yang sedang bersuara** (dari diarization) bukan sekadar mulut yang bergerak.
- **Durasi minimum sorotan 1,2 dtk** dan *hysteresis*: pindah hanya jika pembicara baru dominan ≥0,6 dtk, kecuali jeda ≥0,4 dtk (giliran bicara jelas).
- **Interjeksi pendek** (<0,8 dtk, "iya", "hmm") tidak memindahkan sorotan.
- **Reaksi**: jika ada tawa/emosi (energi suara + gerak wajah) dan momen punchline dari LLM, sisipkan *reaction cutaway* 0,8–1,5 dtk ke wajah yang bereaksi.
- **≥3 orang**:
  - Pembicara tunggal dominan → `single_focus` pada pembicara.
  - Dua orang berdialog cepat → `multi_speaker_split` (atas/bawah) dengan pembicara aktif diberi penanda (border warna).
  - Diskusi ramai (≥3 bicara bergantian <3 dtk) → mode **grup** (*fit_all_blur* atau grid 2×2) lalu kembali ke fokus saat satu orang mendominasi.
- **Kamera bergerak/orang bergerak:** crop mengikuti wajah dengan *easing* (EMA/Kalman), bukan loncat. Jaga *head room* (±38% dari atas, sudah dipakai sekarang) dan batas aman.
- **Wajah hilang** (menoleh/menunduk): pertahankan posisi terakhir hingga 1,0 dtk, lalu kembali ke komposisi lebar.
- **Pembicara di luar layar:** tahan di *wide shot* atau pembicara terakhir yang terlihat.
- Semua keputusan disimpan dengan **alasan** (`reason`: "speaker_match", "reaction", "group", "fallback") agar bisa ditampilkan dan di-debug di Studio.

#### 2.2.4 Perubahan skema (`composer_schema.py`)

- `ShotCropSegment` ditambah: `speaker_id`, `face_id`, `reason`, `confidence`, `transition_in`, `keyframes` (lintasan crop dalam shot).
- `LayoutLayerConfig` ditambah: `focus_strategy` (`"auto_topic" | "active_speaker" | "manual"`), `min_shot_sec`, `group_mode`, `pinned_speaker_id`.
- File baru per job: `speaker_map.json` (peta suara↔wajah, nama alias).

#### 2.2.5 Kendali manual di Studio

- *Face lane* di bawah timeline: balok warna per pembicara, bisa diseret/dipotong, atau klik kanan → **Sorot orang ini**.
- Tombol **Kunci pembicara** (pin) dan **Deteksi ulang dari titik ini**.
- Label nama alias per wajah ("Host", "Tamu 1") sekali untuk satu job.

#### 2.2.6 Verifikasi (wajib sebelum dianggap selesai)

- Siapkan **set uji berlabel** 5–8 klip pendek (podcast 2 orang, 3 orang, 4 orang, kamera bergerak, tawa bersama) dengan label pembicara per detik, dibuat manual sekali.
- Metrik: akurasi pembicara per detik (**target ≥90%** pada 2 orang, **≥80%** pada ≥3 orang), jumlah pergantian sorotan per menit, jumlah sorotan <1 dtk (**target 0**).
- Tes otomatis: skenario sintetis (wajah ditandai warna, suara sinus per pembicara) untuk regresi di `pytest`.
- Waktu analisis target: ≤15 dtk per klip 60 dtk di CPU (sekarang ±7 dtk tanpa diarization; diarization dihitung **sekali per video**, bukan per klip).

---

## 3. Transisi Video — Area B

### 3.1 Kondisi sekarang
Perpindahan kamera hanya `trim → crop → concat` (potongan keras). Hanya *hook banner* yang punya transisi masuk/keluar.

### 3.2 Rancangan

**Skema baru `TransitionConfig`** di `layout` (global) dan boleh dioverride per `ShotCropSegment`:

| Field | Nilai |
| --- | --- |
| `type` | `none`, `fade`, `crossfade`, `dip_black`, `slide_left`, `slide_right`, `slide_up`, `slide_down`, `zoom_in`, `zoom_out`, `whip`, `blur_swap`, `glitch`, `wipe` |
| `duration_sec` | 0,10–0,60 (default 0,25) |
| `easing` | `linear`, `ease_in_out`, `ease_out` |
| `mode` | `fixed`, `auto` |
| `sfx` | `none`, `whoosh`, `swipe`, `pop` (dari folder SFX lokal, volume terpisah) |

**Mode `auto`:** pilih transisi berdasarkan konteks:
- pergantian pembicara cepat → `slide`/`whip` pendek;
- pindah ke reaksi → `zoom_in` halus;
- pindah antar-topik (jeda panjang) → `crossfade`/`dip_black`;
- awal & akhir klip → `fade` masuk/keluar (opsional).

**Implementasi FFmpeg (di `compile_composer_ffmpeg_plan`):**
- Pakai filter `xfade` (`fade`, `slideleft`, `slideright`, `slideup`, `slidedown`, `zoomin`, `wipeleft`, `dissolve`, dll.) antar potongan, bukan `concat` keras.
- `xfade` memendekkan total durasi sebesar durasi transisi. **Wajib diimbangi**: setiap potongan diperpanjang `duration_sec` di ujungnya (trim dengan *handle*), sehingga total durasi video **tetap sama dan audio tidak bergeser**. Perlu uji sinkron audio–video eksplisit.
- Transisi `whip`/`blur_swap` dibuat dengan kombinasi `xfade=slide*` + `boxblur` berumpan waktu (`enable='between(t,..)'`).
- Batasi jumlah node `xfade` (misal ≤24 per klip) dan fallback ke `concat` bila filtergraph terlalu panjang.
- Transisi tidak diterapkan pada shot <0,5 dtk (diganti potong keras).

**Pratinjau (WYSIWYG):**
- `render_preview_frame` menghitung progres transisi di sekitar titik potong (`t_cut ± duration/2`) dengan menggambar dua frame (A dan B) sesuai tipe (alpha, offset, scale). Hasil pratinjau harus identik dengan FFmpeg dalam toleransi visual.

**UI:** dropdown "Gaya Transisi" dengan pratinjau animasi kecil, slider durasi, centang "Otomatis (disarankan)".

**Tes:** render sintetis dua warna + verifikasi frame tengah transisi bukan warna murni, durasi total = durasi tanpa transisi (±1 frame), audio tidak bergeser.

---

## 4. Rombak Total UI (Bersih, Halus, Tidak Penuh) — Area C

### 4.1 Masalah sekarang

- **Studio (Langkah 4)** menumpuk toolbar panjang (preset, simpan/muat, apply-all, batch, render) + 6 tab dengan puluhan kontrol (warna, spin, combo) sekaligus. Pengguna awam kewalahan.
- Langkah 2 padat: template, recipe, warna, layout, split, PiP, header, vignette, dan preview di satu halaman.
- Teks label panjang dan campur istilah teknis ("Active Speaker", "shot_crops", "FFmpeg").
- Banyak tombol berbobot visual sama, jadi tidak jelas aksi utama.
- Tidak ada animasi/transisi antarhalaman; pergantian langkah terasa kasar.
- Tema orange sudah ada tetapi jarak, tipografi, dan hierarki belum konsisten.

### 4.2 Prinsip desain baru

1. **Satu keputusan per layar.** Progressive disclosure: yang jarang dipakai disembunyikan di "Lanjutan".
2. **Dua mode:** **Mudah** (default: pilih template → atur 3–5 hal inti) dan **Pro** (semua kontrol sekarang).
3. **Preview selalu paling besar** dan konsisten di tengah; panel kontrol sempit di kanan.
4. **Satu tombol utama per layar** (orange solid), sisanya ghost/teks.
5. **Bahasa awam**: "Orang yang berbicara otomatis disorot" bukan "Active Speaker detection".
6. **Whitespace lega**, radius & elevasi konsisten, maksimum 2 level kartu.

### 4.3 Struktur layar baru

| Layar | Isi (ringkas) |
| --- | --- |
| **1. Proyek** | Daftar job sebagai kartu bersih + tombol besar "Proyek Baru". Status berupa chip, bukan baris teks. |
| **2. Buat Klip** | Tiga blok vertikal: (1) Sumber (link/file), (2) Jumlah & durasi klip, (3) **Galeri template** dengan thumbnail hidup. Opsi lanjutan dilipat. Preview mini di samping. |
| **3. Proses** | Satu progress besar dengan 4 tahap, estimasi waktu, tombol batal. Log tersembunyi (toggle). |
| **4. Hasil** | Grid klip berminiatur (bukan daftar teks panjang), pilih banyak klip dengan checkbox, aksi massal di bar bawah mengambang. |
| **5. Editor (Studio)** | Kiri: preview 9:16 besar + timeline (dengan *face lane* dan penanda transisi). Kanan: **hanya 4 tab**: *Template*, *Tampilan* (layout+transisi), *Teks* (subtitle+hook), *Audio*. Mode **Pro** membuka sisanya. |

### 4.4 Sistem desain

- **Design tokens** satu file (`atro/ui/tokens.py`): warna (orange primer `#F97316` tetap), skala jarak (4/8/12/16/24/32), radius (8/12/16), tipografi (Segoe UI Variable, ukuran 12/14/16/20/28), elevasi.
- Komponen reusable: `Card`, `SectionHeader`, `PrimaryButton`, `GhostButton`, `Chip`, `Segmented`, `Collapsible`, `EmptyState`, `Toast`, `FloatingActionBar`.
- **Animasi halus** (`QPropertyAnimation`, 150–250 ms, ease-out): *fade+slide* antar halaman, hover kartu, expand/collapse panel, toast. Hormati opsi "kurangi animasi".
- Tema gelap (default) + terang; ikon seragam (satu set, SVG).
- Toast untuk notifikasi menggantikan `QMessageBox` yang menghalangi.
- Status kosong dan error dengan bahasa manusia dan tombol aksi jelas.

### 4.5 Kualitas UI
- Tes ukuran jendela min 1020×680 tanpa teks terpotong.
- Pemeriksaan kontras teks (WCAG AA) pada tema orange.
- `pytest-qt` smoke test per layar; tangkapan layar otomatis untuk regresi visual (opsional).

---

## 5. Perbanyak Template — Area D

### 5.1 Sekarang
8 template siap pakai (`podcast`, `mrbeast_energetic`, `gaming_split`, `pip_facecam_bulat`, `pip_facecam_kotak`, `twitch_comment`, `subathon`, `podcast_mageran_glass`) + 8 caption recipe.

### 5.2 Usulan paket template baru (target **±24 template**, tiap template = layout + recipe subtitle + hook + transisi + efek + audio)

| Kategori | Template baru |
| --- | --- |
| **Podcast** | `podcast_clean_white` (minimalis terang), `podcast_duo_split` (2 orang atas/bawah, penanda pembicara), `podcast_panel_grid` (≥3 orang, grid 2×2 + sorot pembicara), `podcast_lowerthird` (nama tamu di kiri-bawah), `podcast_reaction_pop` (reaction cutaway otomatis), `podcast_quote_card` (kutipan besar + tanda kutip) |
| **Edukasi/Motivasi** | `edu_keypoints` (poin kunci muncul bertahap), `edu_step_numbered`, `motivasi_cinematic` (letterbox + vignette), `motivasi_bold_yellow` |
| **Bisnis/Finansial** | `bisnis_data_card` (angka/stat disorot), `bisnis_serif_editorial` |
| **Storytime/Drama** | `story_comic_bubble`, `story_dark_suspense` (desaturasi + zoom perlahan) |
| **Komedi/Meme** | `comedy_impact_caption` (font Impact + emoji), `comedy_zoom_punch` (zoom otomatis di punchline) |
| **Gaming** | `gaming_neon_split`, `gaming_highlight_hud` (frame HUD), `gaming_reaction_corner` |
| **Berita/Info** | `news_ticker` (running text bawah), `news_breaking_banner` |
| **Religi/Tenang** | `kajian_calm_soft` (warna lembut, tanpa efek ramai) |
| **Musik/Vlog** | `vlog_aesthetic_film` (grain + warna hangat), `vlog_kinetic_beat` (potongan cepat + transisi whip) |

Setiap template wajib menyertakan: pilihan **transisi default**, font aman (tersedia di Windows atau di-bundle), dan pratinjau thumbnail otomatis.

### 5.3 Infrastruktur template
- **Template Pack JSON** (`atro/assets/templates/*.json`) dimuat dinamis (tidak lagi di-hardcode di `presets.py`). Skema `schema_version`, `category`, `tags`, `thumbnail`.
- **Galeri**: filter kategori, pencarian, favorit, "Terbaru".
- **Impor/ekspor** template pengguna (`.atrotpl` = JSON + font + logo dalam ZIP).
- **Pratinjau thumbnail otomatis** dirender dari frame contoh bawaan agar konsisten.
- **Rekomendasi template** berdasarkan deteksi konten (jumlah orang, ada gameplay, musik) dengan label "Disarankan".
- Tes: setiap template harus lolos `render_preview_frame` + kompilasi FFmpeg + ASS.

---

## 6. Perbandingan dengan AutoClip (zhouxiaoka) — Fitur yang Belum Ada di ATRO

Dibaca dari README/CHANGELOG repo tersebut. Kolom ATRO = kondisi saat ini menurut pengetahuan saya tentang kode ATRO.

### 6.1 Tabel kesenjangan

| Fitur AutoClip | ATRO sekarang | Gap | Prioritas |
| --- | --- | --- | --- |
| **Preset platform** (Douyin, Xiaohongshu, TikTok, Reels, Shorts, B-site, YouTube): aturan rasio, durasi, bahasa/copy per platform | Hanya rasio 9:16/16:9 dan durasi bebas | Tidak ada preset platform | **P1** |
| **Publish kit**: cover, judul, deskripsi, hashtag, ZIP per klip | Ada generator copywriting sosial sederhana di Studio; tidak ada cover/ZIP | Kurang cover + ekspor kit | **P1** |
| **Cover/thumbnail** otomatis | Tidak ada | Perlu | **P1** |
| **10 klip teratas + klip alternatif sesuai permintaan** | Jumlah klip tetap saat job | Tidak ada "buat alternatif" | P2 |
| **Pipeline terstruktur**: outline → lokasi timestamp → skor sorotan → judul & copy | Analyzer langsung memilih klip + skor | Tidak ada outline topik/chapter | P2 |
| **Pakai subtitle kreator jika ada** (lebih cepat, lebih murah) | Ada (subtitle YouTube dievaluasi kualitasnya) | Sudah ada | OK |
| **Banyak pilihan ASR**: Whisper lokal, SenseVoice, ASR cloud | Hanya faster-whisper lokal (CPU jika CUDA gagal; 1 jam video ±21 menit) | Tidak ada opsi cepat/cloud, GPU sering gagal (`cublas64_12.dll`) | **P1** |
| **BYOK + katalog penyedia + tes koneksi + deteksi kunci dari clipboard + estimasi biaya + pre-check kuota** | Pengaturan penyedia OpenAI-compatible ada; tanpa tes koneksi penuh/estimasi biaya | Perlu "Tes Koneksi", estimasi biaya per job | P2 |
| **Progres per tahap + pesan error manusiawi + retry satu klik + batas waktu** | Ada tahap & resume; pesan error teknis | Humanisasi error, timeout per tahap, retry per tahap | **P1** |
| **Proyek contoh tanpa API key** | Tidak ada | Perlu agar onboarding mulus | P2 |
| **Editor untuk penyesuaian** | Studio sudah ada (lebih kaya di beberapa sisi) | Perlu dirapikan (bagian 4) | — |
| **Gaya kemasan**: wawancara, podcast layar penuh, majalah, *street fast-cut* | 8 template + 8 recipe | Tambah gaya (bagian 5) | P1 |
| **Bilah nama pembicara (lower third)** dari judul/metadata | Tidak ada | Perlu | P2 |
| **Subtitle halus**: tidak terputus di kata fungsi, tanda baca tidak di awal/akhir baris, kalimat panjang dibagi beberapa layar | Chunk berdasar jumlah kata & jeda | Perbaiki aturan pemenggalan (kata sambung ID: "yang", "dan", "di", "ke", "untuk") | **P1** |
| **Loudness standar (normalisasi)** | `loudnorm` opsional (vocal_loudnorm) | Pastikan default ke target tetap (mis. −14 LUFS) + true peak | P2 |
| **Outro brand opsional** | Tidak ada | Opsional (outro/watermark sudah ada sebagian) | P3 |
| **Framing mengikuti pembicara; bila tidak ada orang, tampilkan full frame** | Ada, tetapi heuristik (bagian 2) | Perbaiki total | **P0** |
| **Antrian video panjang** (long video queue) | Satu worker; job berurutan | Perlu antrian eksplisit + jeda/lanjut | P2 |
| **CLI + MCP + Docker** | Ada CLI `atro check`; tidak ada MCP/Docker | Opsional, bukan prioritas aplikasi desktop | P3 |
| **Installer Windows resmi** (setup.exe, FFmpeg/Python di-bundle) | Folder `dist\ATRO\ATRO.exe` via PyInstaller | Perlu installer (Inno Setup/MSIX) + pembaruan | P2 |
| **Pemulihan crash & keep-awake saat proses** | `recover_stale` ada; tidak ada keep-awake | Tambah pencegah tidur Windows saat render | P2 |
| **Terjemah/subtitle bahasa lain, UI multi-bahasa** | UI Indonesia saja | Opsional | P3 |
| **Analitik opt-out, privasi** | Tidak ada telemetri | Tidak perlu | — |
| **Riset "apa yang mendorong views"** (dokumen riset) | Tidak ada | Inspirasi untuk skor kurasi | P3 |

### 6.2 Kekuatan ATRO yang tidak dimiliki AutoClip (pertahankan)
Layout *mix-and-match* (split, PiP, blurred), multi-speaker color, hook banner multi-baris, comment bubble ala Twitch, TTS pembuka, backsound + ducking, live preview WYSIWYG dengan JSON tunggal, batch re-render tanpa transcribe ulang, editor kata (transcript override).

### 6.3 Fitur tambahan yang disarankan (di luar perbandingan)
- **Auto B-roll/zoom punch di punchline** (dari LLM).
- **Word-level emphasis** otomatis (kata kunci diberi warna/animasi).
- **Auto-hapus jeda/"eee"/"umm"** (jump-cut halus) dengan opsi aman.
- **Normalisasi suara per pembicara** dan *noise reduction* ringan.
- **Penjadwalan/ekspor multi-rasio** (9:16, 1:1, 16:9) dalam satu klik.
- **Riwayat versi per klip** (undo render sebelumnya).

### 6.4 Bagian yang menunggu post X/Threads
Setelah isi dua post tersedia, saya cocokkan: alat apa yang disebut (mis. timeline editor `OpenCut`, tool upscaling, dll.), lalu tambahkan temuan ke tabel 6.1.

---

## 7. Performa & Stabilitas — Area F

| Temuan (dari log job nyata 59 menit) | Perbaikan |
| --- | --- |
| Transkripsi Whisper CPU ±21 menit untuk 1 jam video; GPU gagal (`cublas64_12.dll` tidak ada) | Bundle/deteksi DLL CUDA atau pakai `ctranslate2` dengan CPU int8 + `vad_filter`, model `distil`/`small` pilihan; dokumentasikan GPU. Tawarkan ASR cloud opsional. |
| Render ±30–50 dtk per klip (libx264 CPU, berurutan) | Opsi encoder NVENC/QSV/AMF otomatis jika tersedia; render paralel 2 klip; preset `veryfast` untuk pratinjau. |
| Deteksi wajah ±7 dtk/klip | Hitung diarization/identitas sekali per video, cache per job (`speaker_map.json`, `shots.json`). |
| `ATRO.exe` sering tidak diperbarui karena proses lama mengunci file | `build_exe.ps1`: bangun ke folder baru lalu tukar; deteksi proses ATRO dan tawarkan menutup. |
| Perubahan terakhir belum di-commit; exe di `dist` masih build lama | Commit bertahap + build ulang setelah tiap fase. |
| Teks UI/error bercampur teknis | Kamus pesan error manusiawi (satu modul) dipakai seluruh UI. |

---

## 8. Rencana Fase (Usulan Urutan)

| Fase | Isi | Hasil yang bisa diuji | Perkiraan |
| --- | --- | --- | --- |
| **F1** | Kontrak data baru (`TransitionConfig`, `speaker_id`/`reason`/`keyframes`, `speaker_map.json`) + migrasi config lama | Config lama tetap terbaca; tes skema | 1–2 hari |
| **F2** | Transisi FFmpeg + pratinjau + UI dasar transisi | Video dengan slide/fade/zoom; sinkron audio terbukti | 3–4 hari |
| **F3** | Speaker Intelligence: diarization, identitas wajah, pemetaan, Shot Planner, face lane manual | Akurasi terukur pada set uji berlabel | 6–9 hari |
| **F4** | Template Pack (≥24) + galeri + impor/ekspor | Semua template lolos preview + render | 3–4 hari |
| **F5** | Rombak UI (token, komponen, 5 layar, mode Mudah/Pro, animasi) | Tangkapan layar & tes smoke tiap layar | 6–8 hari |
| **F6** | Preset platform + publish kit + cover + subtitle paging | ZIP kit per klip; aturan platform | 4–5 hari |
| **F7** | Performa (ASR, encoder, cache) + installer + build exe | Waktu benchmark sebelum/sesudah | 3–4 hari |

Total kasar: **±4–5 minggu kerja** satu orang. Fase bisa dijalankan terpisah dan di-commit per fase.

---

## 9. Keputusan Pengguna (2026-11-22)

| Pertanyaan | Jawaban | Dampak ke rencana |
| --- | --- | --- |
| Jumlah orang / kamera | **5 orang, 3 kamera** | Mode grup jadi default untuk diskusi ramai; identitas wajah antar-angle wajib (F3); set uji berlabel harus 5 orang. |
| ASR | **Lokal saja** | Tidak ada ASR cloud. Fokus mempercepat Whisper lokal. |
| Platform | **YouTube Shorts & TikTok** | Preset platform hanya dua ini (9:16, durasi ≤60 dtk Shorts / ≤3 mnt TikTok). Preset lain ditunda. |
| GPU | **GTX 1050** | VRAM kecil (2–4 GB): pakai model `small` int8 di GPU; wajib bundle DLL CUDA 12 (cublas/cudnn) agar tidak jatuh ke CPU; encoder NVENC (Pascal didukung). Diarization tetap di CPU. |
| Mode UI | **Mudah (default) + Advanced opsional** | Seperti di bagian 4: Mudah untuk awam, Advanced membuka kontrol lengkap. |
| Commit | **Commit dulu** | Selesai: commit `28e02b6`, `6432980`, `448f6f2`, `8e79190`, `dc70fc8` (belum di-push). |

## 9b. Risiko & Pertanyaan Terbuka (sisa)

**Risiko**
1. Diarization lokal pada CPU bisa lambat untuk video >2 jam → perlu cache dan pilihan "lewati".
2. `xfade` rawan salah hitung durasi → wajib tes sinkron audio.
3. Embedding wajah bisa salah klaster bila pencahayaan berubah drastis → sediakan koreksi manual dan label alias.
4. Rombak UI besar berisiko merusak alur yang sudah stabil → bangun di atas `ClipComposerConfig` yang sama, jangan ubah logika render saat F5.
5. Akurasi LLM untuk POI tidak deterministik (tanpa `temperature`) → selalu ada fallback heuristik dan validasi.
6. 5 orang dengan 3 kamera: satu kamera bisa menampilkan 2–3 orang sekaligus, sehingga pemetaan suara↔wajah bergantung pada korelasi seluruh video, bukan per shot.

**Masih terbuka:** isi post X/Threads (bagian 0).

---

## 10. Definisi Selesai (Ringkas)

- Sorotan pembicara ≥90% (2 orang) / ≥80% (≥3 orang) pada set uji berlabel; tidak ada sorotan <1 dtk.
- Setiap perpindahan kamera memakai transisi sesuai pengaturan; durasi dan audio sinkron (±1 frame).
- ≥24 template lolos tes otomatis; galeri dengan thumbnail.
- UI: tiap layar ≤1 aksi utama; Studio mode Mudah ≤4 tab; tidak ada teks terpotong pada 1020×680.
- Publish kit (cover + judul + deskripsi + hashtag + ZIP) per klip dan preset platform tersedia.
- Seluruh `pytest` lulus; `ATRO.exe` dibangun ulang dan terbuka <3 dtk.
