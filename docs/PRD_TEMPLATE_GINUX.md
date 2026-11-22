# PRD — Template Gaya GiNUX (imGinux3) untuk ATRO

> Dokumen ini hanya **PRD / daftar perubahan**. Tanggal: 2026-11-22.
> Menggantikan dan melengkapi [TEMPLATE_GINUX_RENCANA.md](file:///d:/projects/atro/docs/TEMPLATE_GINUX_RENCANA.md).
> Referensi: https://www.youtube.com/@imGinux3/shorts

## 0. Status Penting (Harap Dibaca)

> [!WARNING]
> Sebelum Anda menyampaikan "hanya PRD", saya sudah **terlanjur mengubah kode** (fase G1–G3). Itu di luar permintaan Anda. Daftar lengkapnya ada di bagian 9. **Tes belum pernah selesai dijalankan** (proses dibatalkan), jadi perubahan itu belum terverifikasi. Saya belum membatalkannya, karena `git checkout` bisa ikut menghapus perubahan Anda sendiri jika ada. Putusan ada di tangan Anda (lihat bagian 9).

## 1. Latar Belakang & Tujuan

Pengguna menyukai gaya editing Shorts channel imGinux3 (GiNUX) dan ingin template serupa di ATRO. Tujuannya: dari video podcast/streaming panjang, ATRO menghasilkan Short dengan ritme cepat, tanpa jeda, ada penekanan visual dan suara di punchline, dan teks mengikuti pembicara.

**Batasan riset:** video aslinya tidak bisa dianalisis frame per frame. Gaya di bawah berasal dari ringkasan web dan penjelasan pengguna (bagian 2). Font, warna, dan jenis SFX persis belum diverifikasi.

## 2. Ciri Gaya yang Dikonfirmasi

| # | Ciri | Sumber |
| --- | --- | --- |
| 1 | Hook langsung ke momen konflik atau lucu | Riset web |
| 2 | Jump-cut rapat, tanpa dead air atau filler | Riset web |
| 3 | Punchline ditandai zoom, SFX meme | Riset web + pengguna (punya file SFX meme sendiri) |
| 4 | Fokus ke ekspresi mikro dan reaksi | Riset web |
| 5 | **Teks selalu berada di atas subjek yang sedang bicara.** Saat kamera zoom out dan dua orang berdebat, teks muncul di atas orang yang bicara, bukan di posisi tetap | **Pengguna (baru)** |
| 6 | Pengguna tidak punya referensi caption lain (font/warna), jadi dipilih default yang aman | Pengguna |

## 3. Lingkup

**Masuk lingkup (fase G1–G3 + G8 baru):**

| Fase | Fitur | Dasar |
| --- | --- | --- |
| G1 | 3 template baru di kategori Komedi | Permintaan pengguna |
| G2 | Jump-cut otomatis (buang jeda panjang dan filler) | Ciri 2 |
| G3 | Zoom punch berulang | Ciri 3 |
| G4a | Dukungan SFX meme milik pengguna | Jawaban pertanyaan 3 |
| **G8** | **Caption mengikuti posisi pembicara aktif (pindah instan)** | Ciri 5 |
| **G4b** | **SFX meme dipicu sesuai isi bahasan / momen punchline klip** | Keputusan pengguna |

**Di luar lingkup (ditunda):** G5 zoom ke wajah yang bereaksi, G6 overlay stiker/meme, G7 caption recipe khusus GiNUX (menunggu referensi visual).

## 4. Kondisi Saat Ini di Kode

| Area | Fakta | File |
| --- | --- | --- |
| Template | 25 pack JSON, 9 kategori, dimuat dari `assets/templates` | [template_packs.py](file:///d:/projects/atro/atro/core/template_packs.py) |
| Skema | `TransitionSfx` hanya `none/whoosh/swipe/pop` | [composer_schema.py](file:///d:/projects/atro/atro/core/composer_schema.py) |
| Zoom punch | Hanya **satu** jendela per klip (`zoom_punch_start_sec` + durasi), di preview dan di FFmpeg | [composer_renderer.py](file:///d:/projects/atro/atro/modules/cutter/composer_renderer.py) |
| Subtitle | Posisi tetap via `placement_preset` (`bottom_safe`, `top_safe`, `split_line_center`, `custom`) lewat `pos_x/pos_y` global | `composer_schema.py` (`sync_subtitle_anchor`) |
| Render | Satu grafik filter FFmpeg. Timeline kamera, transkrip, SFX, dan zoom semuanya memakai waktu yang sama | [service.py](file:///d:/projects/atro/atro/modules/cutter/service.py) |
| SFX | Hanya 3 WAV bawaan di `atro/assets/sfx/` | [sfx.py](file:///d:/projects/atro/atro/modules/cutter/sfx.py) |

## 5. Kebutuhan per Fitur

### 5.1 G1 — Template Baru

Tiga template di kategori **Komedi**, semua `single_focus` 9:16, `focus_strategy: active_speaker`.

| ID | Judul | Karakter |
| --- | --- | --- |
| `ginux_clip_meme` | Short Meme Cut | Tanpa hook, zoom tiap ±3,5 detik, jeda maks 0,30 s, SFX `pop`, font Impact |
| `ginux_reaction_zoom` | Short Reaksi Cepat | Zoom tiap ±2,5 detik, jeda maks 0,40 s, SFX `swipe`, vignette |
| `ginux_drama_banner` | Short Drama Podcast | Hook banner pendek, zoom tiap ±6 detik, jeda maks 0,50 s, SFX `whoosh` |

Kriteria: muncul di galeri, bisa dirender, dan masuk rekomendasi untuk kata kunci komedi.

### 5.2 G2 — Jump-Cut

- **Input:** timestamp per kata dari Whisper.
- **Aturan:** jeda antar kata lebih panjang dari `silence_max_gap_sec` dipotong (tersisa ±0,16 s). Filler (`eee, eh, em, hmm, anu, uh, …`) dibuang jika `silence_remove_fillers` aktif.
- **Mekanisme:** render dua tahap. Tahap 1 membuat klip perantara hasil gabungan potongan (FFmpeg `trim/atrim/concat`). Tahap 2 merender seperti biasa dari klip perantara, dengan timeline mulai dari 0.
- **Yang harus dipetakan ulang:** transkrip, `transcript_overrides`, dan timeline kamera/wajah (dideteksi ulang di klip perantara).
- **Fallback:** tanpa timestamp per kata, atau penghematan di bawah 0,4 detik, atau FFmpeg gagal → render normal tanpa trim, dengan pesan log.
- **Default mati.** Template lama tidak berubah perilakunya.

Keterbatasan yang diterima: Live Preview Studio belum menampilkan hasil jump-cut (hanya saat export). Biaya: satu kali encode tambahan.

### 5.3 G3 — Zoom Punch Berulang

- Field baru `zoom_punch_repeat_sec` (0 = perilaku lama, satu kali).
- Jika > 0: jendela zoom diulang setiap `max(repeat, durasi + 0,3)` detik sampai klip habis (maks 60).
- Preview dan FFmpeg harus memakai fungsi jendela yang sama agar hasilnya identik.
- Iterasi berikutnya (opsional): titik zoom dipilih dari kata penekanan/tawa, bukan interval tetap.

### 5.4 G4a — SFX Milik Pengguna

**Folder penempatan file SFX meme Anda** (jawaban pertanyaan 3):

| Lokasi | Kegunaan |
| --- | --- |
| `<folder data aplikasi>\sfx\` (di samping folder `templates`, `backsound`) | **Disarankan.** Aman dari pembaruan aplikasi, tidak ikut git |
| `d:\projects\atro\atro\assets\sfx\` | Ikut ter-bundle ke `.exe` saat build, cocok jika ingin disertakan permanen |

- Format: `.wav, .mp3, .ogg, .m4a, .aac, .flac`. Nama file (tanpa ekstensi) menjadi nama SFX, misalnya `vine_boom.mp3` → `vine_boom`. Hindari spasi dan huruf besar.
- Nama bawaan (`whoosh`, `swipe`, `pop`) tidak boleh dipakai ulang.
- Muncul otomatis di dropdown SFX transisi Studio dengan label "(kustom)". Template bisa memakainya lewat `transition_sfx`.
- Catatan: hari ini SFX hanya dipicu di **transisi antar-shot**. Untuk memicu SFX di momen punchline/bahasan klip, lihat **G4b** di bawah.

### 5.4b G4b — SFX Kontekstual Sesuai Bahasan Klip (Baru)

**Keputusan pengguna:** SFX meme juga dipicu di momen isi bahasan / punchline klip (bukan hanya di transisi kamera), memilih dari file SFX yang sudah ditambahkan pengguna.

**Kebutuhan:**
1. **Daftar SFX tersedia:** baca nama file SFX kustom yang ada di folder SFX (`list_custom_sfx_names()` + bawaan), lalu kirim daftarnya sebagai konteks ke tahap analisis LLM (atau pemilih manual di Studio).
2. **Saran titik SFX (Analyzer):** saat LLM menganalisis transkrip klip, LLM dapat menyarankan titik waktu + nama SFX yang cocok dengan emosi/topik bahasan (mis. momen kaget, punchline, canggung, tertawa) dari daftar SFX yang tersedia.
3. **Kontrak data:** tambah daftar `sfx_cues` (timestamp relatif klip, nama SFX, volume) di `AudioLayerConfig` atau `ClipComposerConfig` agar bisa diedit manual di Studio dan dirender oleh FFmpeg (`service.py` + `backsound.py`).
4. **Sinkron jump-cut:** jika G2 aktif, timestamp `sfx_cues` ikut dipetakan ulang lewat `remap_time()` (dan dibuang jika jatuh di bagian yang dipotong).
5. **Fallback:** jika nama SFX tidak ditemukan di disk atau `sfx_cues` kosong, abaikan tanpa error.

### 5.5 G8 — Caption Mengikuti Pembicara (Baru)

**Perilaku yang diinginkan:** teks selalu tepat di atas kepala subjek yang sedang berbicara. Saat framing zoom out dan dua orang bergantian bicara, teks pindah ke atas orang yang sedang bicara.

**Kebutuhan:**

1. **Sumber posisi:** untuk setiap potongan caption, tentukan pembicara aktif dari diarization (F3), lalu cari kotak wajah orang itu pada waktu tersebut.
2. **Konversi koordinat:** kotak wajah di video sumber → koordinat kanvas 1080×1920 melalui crop dan zoom yang berlaku saat itu (termasuk zoom punch dan crop kamera per shot).
3. **Penempatan:** titik tengah teks = tengah horizontal wajah; vertikal = di atas kepala dengan jarak tetap (±40 px).
4. **Batas aman:** teks dijepit di dalam area aman (tidak keluar kanvas, tidak menutupi UI Shorts di bawah dan kanan).
5. **Stabil & instan:** posisi dihaluskan dari jitter kamera, tetapi pindah antar-pembicara dilakukan **instan** (lompat bersih di batas chunk, bukan geser halus tiap frame).
6. **Layout lain:** pada `multi_speaker_split` teks mengikuti panel pembicara. Pada `blurred_frame`, mengikuti wajah di dalam frame.
7. **Fallback:** wajah tidak terdeteksi atau pembicara tidak diketahui → posisi `bottom_safe` seperti sekarang.
8. **Konfigurasi:** `placement_preset` baru `above_speaker` (+ opsi jarak dan jepit aman). Template GiNUX memakainya secara default.
9. **Sinkron jump-cut:** posisi dihitung setelah pemetaan ulang waktu G2 (urutan: G2 dulu, baru G8).

**Area terdampak:** skema subtitle, pembuat ASS (`\pos` per dialog, saat ini satu posisi global), perencana shot/wajah, preview live Studio.

**Risiko G8 (tertinggi di proyek ini):** akurasi pembicara aktif dari diarization (sudah ditandai rawan di rencana V2), dan teks yang berpindah-pindah bisa terasa kacau jika deteksi salah. Perlu ambang keyakinan: jika ragu, pakai posisi terakhir yang yakin.

## 6. Perubahan yang Diperlukan (Ringkasan File)

| File | Perubahan | Fase |
| --- | --- | --- |
| `core/composer_schema.py` | `TransitionSfx` jadi string bebas. Field baru: `silence_trim_enabled`, `silence_max_gap_sec`, `silence_remove_fillers`, `zoom_punch_repeat_sec`. Nanti: `placement_preset="above_speaker"` | G2, G3, G4a, G8 |
| `core/template_packs.py` | 3 spec seed + field entry baru (`zoom_punch_repeat_sec`, `silence_*`) + kata kunci rekomendasi | G1 |
| `core/presets.py` | Terapkan field template baru ke `ClipComposerConfig` | G1 |
| `modules/cutter/jump_cut.py` (baru) | Perencana potongan, pemetaan ulang waktu, builder perintah FFmpeg | G2 |
| `modules/analyzer/prompts.py`, `schemas.py` | Kirim daftar SFX tersedia ke LLM & terima saran `sfx_cues` sesuai bahasan klip | G4b |
| `modules/cutter/service.py` | Tahap trim sebelum render, pemetaan ulang transkrip, `sfx_cues`, dan override, simpan config **asli** (bukan versi trim), bersihkan file sementara | G2, G4b |
| `modules/cutter/composer_renderer.py` | Fungsi jendela zoom bersama untuk preview dan FFmpeg | G3 |
| `modules/cutter/sfx.py` | Cari SFX kustom di dua folder, daftar nama | G4a |
| `ui/widgets/clip_studio.py` | Dropdown SFX menampilkan SFX kustom. Nanti: kontrol jump-cut, interval zoom, opsi `above_speaker` | G4a, G8 |
| `modules/cutter/subtitle_burner.py` | Posisi per dialog dari wajah pembicara | G8 |
| `modules/cutter/framing/*` | Kotak wajah per pembicara per waktu, dikonversi ke kanvas | G8 |
| `tests/` | Tes jump-cut, zoom berulang, SFX kustom, template. Tes G8 dengan wajah sintetis | semua |

## 7. Kriteria Penerimaan

1. Tiga template tampil di Komedi dan menghasilkan klip tanpa error di video uji.
2. Klip hasil `ginux_clip_meme` lebih pendek dari sumbernya, dan tidak ada jeda lebih dari ±0,45 detik.
3. Caption tetap sinkron dengan suara setelah jump-cut (selisih < 0,15 detik).
4. Zoom terulang pada interval yang diatur, dan preview cocok dengan hasil render.
5. File SFX di folder yang benar muncul di dropdown dan terdengar di hasil export.
6. (G8) Pada klip dua pembicara, teks berada di atas wajah yang berbicara pada ≥ 90% chunk di video uji berlabel manual.
7. Template dan klip lama menghasilkan keluaran identik seperti sebelumnya (semua fitur baru default mati).
8. Seluruh tes lama tetap lulus.

## 8. Risiko & Pertanyaan Terbuka

| # | Risiko / Pertanyaan | Catatan |
| --- | --- | --- |
| R1 | Akurasi pembicara aktif (G8) | Butuh video uji berlabel. Ambang keyakinan dengan fallback |
| R2 | Jump-cut menggeser semua timeline | Sumber bug utama. Perlu tes sinkron dan uji video asli |
| R3 | Whisper tanpa timestamp per kata (subtitle YouTube) | Jump-cut otomatis dilewati. Perlu info di UI |
| R4 | Hak cipta SFX/meme | Gunakan hanya aset yang Anda punya hak pakainya |
| R5 | Jumlah template 25 → 28 | Cek tes atau UI yang mengasumsikan angka 25 |
| Q1 | Font dan warna teks GiNUX | **Diputuskan:** Font `Impact`, warna kuning `#FACC15` |
| Q2 | Pemicu SFX meme | **Diputuskan:** Dipicu sesuai isi bahasan / punchline klip (**G4b**), bukan hanya di transisi |
| Q3 | Cara teks berpindah antar-pembicara | **Diputuskan:** **Instan** di batas chunk |
| Q4 | Jump-cut tampil di preview Studio? | Saat ini hanya saat export |

## 9. Perubahan yang Sudah Terlanjur Saya Buat (Di Luar Permintaan)

Diubah: `core/composer_schema.py`, `core/presets.py`, `core/template_packs.py`, `modules/cutter/service.py`, `modules/cutter/composer_renderer.py`, `modules/cutter/sfx.py`, `ui/widgets/clip_studio.py`.
Dibuat baru: `modules/cutter/jump_cut.py`, `tests/test_ginux_style.py`.

Status: **belum diuji** (jalankan tes dibatalkan sebelum selesai). Satu perubahan di `service.py` sempat tidak sengaja menyentuh fungsi lain, lalu saya perbaiki, tetapi belum diverifikasi.

Pilihan Anda:
- **Batalkan semua:** jalankan `git status` lalu `git checkout -- <file yang diubah>` dan hapus dua file baru. Cek dulu apakah ada perubahan Anda sendiri di file yang sama.
- **Pertahankan sebagai draf:** saya hanya menjalankan tes dan melaporkan hasilnya, tanpa mengubah apa pun lagi.
- **Saya tidak menyentuh apa pun lagi** sampai Anda menyuruh.
