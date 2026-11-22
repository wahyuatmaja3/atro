# ATRO — Rencana Template "Gaya GiNUX" (imGinux3) — Belum Dieksekusi

> Dokumen ini hanya **analisis & rencana**. Tidak ada kode yang diubah.
> Tanggal: 2026-11-22 · Referensi: https://www.youtube.com/@imGinux3/shorts

## 0. Batasan Sumber (Jujur)

- Halaman YouTube Shorts dirender JavaScript, jadi **saya belum bisa melihat video aslinya**. Ciri gaya di bawah berasal dari ringkasan hasil pencarian web, bukan dari analisis frame per frame.
- Ciri yang **belum terverifikasi**: font persis, warna teks, posisi caption, jenis meme/SFX, ada tidaknya musik latar, dan rasio zoom.
- **Tindakan untuk Anda (opsional, sangat membantu):** kirim 2–3 link Short favorit atau screenshot caption/layout-nya. Bagian 2 dan 4 akan saya sesuaikan sebelum implementasi.

## 1. Ciri Gaya GiNUX (Dugaan dari Riset)

| # | Ciri | Terjemahan ke editing |
| --- | --- | --- |
| 1 | Hook langsung ke momen konflik atau lucu di detik 1–2 | Tanpa intro. Klip dipotong mulai tepat di kalimat pemicu. |
| 2 | Jump-cut sangat rapat, tanpa dead air atau filler | Hapus jeda dan "eee/anu" otomatis, ritme cepat. |
| 3 | Punchline ditandai SFX, zoom-in, atau gambar meme | Zoom punch di punchline, SFX pas momennya, overlay stiker/meme. |
| 4 | Fokus ke ekspresi mikro (tatapan canggung, reaksi) | Zoom ke wajah yang bereaksi (bukan hanya yang bicara). |
| 5 | Caption besar dan mudah dibaca, humor lokal Indonesia | Teks 1–3 kata per layar, kata kunci diberi warna. |
| 6 | Durasi pendek (±30–50 detik) | Target durasi klip diatur 25–50 detik. |

## 2. Kondisi ATRO Saat Ini

Template berupa file JSON di [templates](file:///d:/projects/atro/atro/assets/templates) (25 pack, 9 kategori), dimuat oleh [template_packs.py](file:///d:/projects/atro/atro/core/template_packs.py) dan divalidasi oleh [composer_schema.py](file:///d:/projects/atro/atro/core/composer_schema.py).

**Sudah ada dan bisa dipakai ulang:** `single_focus`, `zoom_punch_enabled`, transisi `whip`/`zoom_in`/`slide_*`, SFX `pop`/`whoosh`/`swipe`, caption `streamer_kinetic`, subtitle `kinetic_1_2`, `focus_strategy`.

**Yang belum ada (celah):**

| Celah | Dampak | Perlu kode? |
| --- | --- | --- |
| Pemotongan otomatis dead air / filler (jump-cut rapat) | Ciri #2, inti gaya GiNUX | Ya (modul cutter) |
| Zoom ke wajah yang **bereaksi** (bukan hanya pembicara) | Ciri #4 | Ya (`shot_planner`) |
| Overlay stiker/meme di punchline | Ciri #3 | Ya (layer baru) |
| Zoom punch berulang di beberapa titik (saat ini hanya 1 kali per klip: `zoom_punch_start_sec`) | Ciri #3 | Ya (`ClimaxEffectsConfig`) |
| SFX komedi tambahan (mis. "vine boom", "ding", "record scratch") | Ciri #3 | Aset audio + `TransitionSfx` |
| Caption recipe khusus GiNUX | Ciri #5 | Ya (`CaptionRecipeId`) |

## 3. Usulan Template Baru (Kategori: Komedi, 3 varian)

Semua varian memakai `layout_mode: single_focus`, 9:16.

| ID | Judul | Karakter | Dibangun dari |
| --- | --- | --- | --- |
| `ginux_clip_meme` | Short Meme Cut | Jump-cut rapat, zoom punch, SFX pop, caption 1–2 kata. **Varian utama.** | `comedy_punch_whip` + fitur baru |
| `ginux_reaction_zoom` | Short Reaksi Wajah | Zoom ke ekspresi mikro, SFX swipe, caption bawah kecil | `story_drama_zoom` + zoom reaksi |
| `ginux_drama_banner` | Short Drama Podcast | Hook banner pendek, caption 3–4 kata, tanpa zoom berlebihan | `podcast` + hook |

### 3.1 Draf JSON `ginux_clip_meme`

> Hanya memakai nilai yang **sudah valid** di skema sekarang. Field baru ditandai `TODO`.

```json
{
  "id": "ginux_clip_meme",
  "category": "Komedi",
  "title": "Short Meme Cut (Gaya GiNUX)",
  "badge": "Jump-Cut Rapat • Zoom + SFX",
  "description": "Potongan cepat tanpa jeda, zoom punch di punchline, SFX pop, caption besar 1-2 kata.",
  "base_preset": "podcast",
  "job_edit_format": "podcast",
  "layout_mode": "single_focus",
  "caption_recipe": "streamer_kinetic",
  "placement_preset": "bottom_safe",
  "hook_enabled": false,
  "vignette_enabled": false,
  "topic_header_enabled": false,
  "zoom_punch_enabled": true,
  "font_family": "Impact",
  "active_word_bg_hex": "#FACC15",
  "transition_mode": "auto",
  "transition_type": "whip",
  "transition_sfx": "pop",
  "focus_strategy": "active_speaker"
}
```

`TODO` (butuh kode, lihat bagian 4): `silence_trim`, `zoom_punch_repeat`, `reaction_zoom`, `meme_overlay`, `caption_recipe: "ginux_meme"`.

## 4. Rencana Pekerjaan (Bertahap)

| Fase | Isi | File utama | Usaha |
| --- | --- | --- | --- |
| **G1** | Tambah 3 template JSON dengan fitur yang **sudah ada** (hasil mirip, belum sempurna) | `assets/templates/*.json`, daftar seed di `template_packs.py` | Kecil |
| **G2** | Pemotongan dead air/filler (opsi `silence_trim`: ambang jeda, jeda maksimum, buang filler dari kata Whisper) | `modules/cutter/service.py`, `composer_schema.py` | Sedang |
| **G3** | Zoom punch berulang: daftar titik zoom dari kata penekanan/tawa, menggantikan 1 titik tetap | `ClimaxEffectsConfig`, `viral_effects.py`, `composer_renderer.py` | Sedang |
| **G4** | SFX komedi baru (butuh aset `.wav` bebas lisensi) + perluas `TransitionSfx` | `assets/sfx/`, `sfx.py` | Kecil–sedang |
| **G5** | Zoom ke wajah yang bereaksi (pakai data wajah/diarization F3) | `framing/shot_planner.py` | Sedang–besar |
| **G6** | Layer stiker/meme di punchline. Sumber gambar: folder pengguna (**tidak** mengambil dari internet otomatis, hindari masalah hak cipta) | `composer_schema.py`, `composer_renderer.py`, UI | Besar |
| **G7** | Caption recipe `ginux_meme` (font, warna kata kunci, outline) setelah ada referensi visual | `CaptionRecipeId`, `subtitle_burner.py` | Kecil–sedang |

**Urutan disarankan:** G1 → G7 (setelah referensi) → G2 → G3 → G4 → G5 → G6.
G1 bisa selesai cepat dan langsung dipakai. G2–G3 memberi dampak terbesar pada kesan "ala GiNUX".

## 5. Risiko & Catatan

- **Hak cipta:** meniru *gaya* editing tidak masalah, tetapi jangan menyalin aset (meme/SFX/musik) dari video GiNUX. Gunakan aset sendiri atau berlisensi bebas.
- **Akurasi gaya:** tanpa referensi visual, hasil G1 berupa perkiraan. Template bisa dituning lewat JSON tanpa ubah kode.
- **Galeri:** menambah template menaikkan jumlah dari 25 ke 28. Cek apakah ada tes/hitungan yang mengasumsikan angka 25 dan filter galeri kategori Komedi.
- **Jump-cut vs sinkron subtitle:** pemotongan jeda menggeser timestamp kata. Perlu dipetakan ulang agar caption tetap sinkron (risiko utama G2).

## 6. Keputusan yang Dibutuhkan

1. Mulai dari **G1 saja** (cepat), atau langsung G1 + G2 + G3?
2. Bisa kirim 2–3 link/screenshot Short favorit agar warna, font, dan posisi caption akurat?
3. Apakah Anda punya kumpulan SFX/meme sendiri, atau saya cari aset berlisensi bebas?

## 7. Definisi Selesai

- 3 template baru muncul di galeri kategori **Komedi** dan bisa dirender tanpa error.
- Tes yang ada tetap lulus, dan pratinjau (WYSIWYG) cocok dengan hasil render.
- Fitur G2–G7 yang dikerjakan punya opsi on/off sehingga template lama tidak berubah perilakunya.
