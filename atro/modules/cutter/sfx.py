"""Offline transition sound effects generator & resolver (whoosh, swipe, pop)."""

from __future__ import annotations

import math
import re
import sys
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

BUILTIN_SFX_NAMES: tuple[str, ...] = ("whoosh", "swipe", "pop")


@dataclass(frozen=True, slots=True)
class SfxContextSpec:
    """Gaming meme Shorts context specification for an existing SFX asset."""

    name: str
    category: str  # transition | shock_twist | fail_rage | awkward_freeze | game_action | hype_rizz
    usage_id: str
    keywords: tuple[str, ...] = ()
    zoom_role: str = "center"  # facecam | gameplay | center | none
    default_volume: float = 0.72
    max_stinger_sec: float = 3.5
    is_transition_sfx: bool = False


SFX_GAMING_CONTEXT_CATALOG: tuple[SfxContextSpec, ...] = (
    # --- Transition / UI Micro-Motion ---
    SfxContextSpec(
        name="whoosh",
        category="transition",
        usage_id="Transisi cepat antar scene, zoom-in atau zoom-out kamera ke facecam/gameplay",
        keywords=("transisi", "pindah", "zoom", "langsung", "tiba-tiba"),
        zoom_role="none",
        default_volume=0.55,
        max_stinger_sec=0.8,
        is_transition_sfx=True,
    ),
    SfxContextSpec(
        name="swipe",
        category="transition",
        usage_id="Geser layar/angle cepat, buka-tutup panel, atau zoom-out kembali ke gameplay",
        keywords=("geser", "ganti", "lanjut", "swipe"),
        zoom_role="none",
        default_volume=0.55,
        max_stinger_sec=0.8,
        is_transition_sfx=True,
    ),
    SfxContextSpec(
        name="pop",
        category="transition",
        usage_id="Munculnya teks/stiker/notif, poin kecil, atau jump-cut dialog cepat",
        keywords=("muncul", "klik", "poin", "notif", "oke", "sip", "nah"),
        zoom_role="center",
        default_volume=0.68,
        max_stinger_sec=0.5,
        is_transition_sfx=True,
    ),
    SfxContextSpec(
        name="Whoosh (7 Different Whooshes)",
        category="transition",
        usage_id="Kompilasi 7 efek whoosh cepat untuk montase (diambil potongan awal ~0.9s)",
        keywords=("montase", "cepat", "speedrun", "swipe"),
        zoom_role="none",
        default_volume=0.55,
        max_stinger_sec=0.95,
        is_transition_sfx=False,
    ),
    # --- Shock / Plot Twist / Suspicion / Dramatic Impact ---
    SfxContextSpec(
        name="Vine Boom",
        category="shock_twist",
        usage_id="Tatapan curiga ke kamera, statement nyeleneh, plot twist, atau zoom wajah deadlock",
        keywords=("anjir", "serius", "buset", "plot twist", "ternyata", "waduh", "gila", "parah", "wait", "hah", "bro", "cocok"),
        zoom_role="facecam",
        default_volume=0.72,
        max_stinger_sec=2.6,
    ),
    SfxContextSpec(
        name="Vine Boom but perfectly bass boosted",
        category="shock_twist",
        usage_id="Punchline ekstrem, kena mental berat, one-tap kill, atau roast pedas ke teammate",
        keywords=("kena mental", "mampus", "savage", "goblok", "tolol", "anjay", "parah banget", "roast", "hancur mental"),
        zoom_role="facecam",
        default_volume=0.62,
        max_stinger_sec=2.0,
    ),
    SfxContextSpec(
        name="Among Us SUS",
        category="shock_twist",
        usage_id="Curiga sama teammate/musuh (impostor, nge-troll, bohong, ngaku pro padahal noob)",
        keywords=("sus", "curiga", "mencurigakan", "bohong", "boong", "penipu", "impostor", "troll", "khianat", "ketahuan"),
        zoom_role="facecam",
        default_volume=0.72,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Metal Gear Solid Alert",
        category="shock_twist",
        usage_id="Tanda seru (!) kaget mendadak, kepergok musuh, sadar ada bahaya di belakang",
        keywords=("awas", "belakang", "musuh", "ketahuan", "kaget", "bahaya", "alert", "ngumpet", "nongol", "sniper"),
        zoom_role="gameplay",
        default_volume=0.70,
        max_stinger_sec=2.2,
    ),
    SfxContextSpec(
        name="Bass Drop",
        category="shock_twist",
        usage_id="Momen clutch berhasil, masuk fase serius/comeback, ulti kena semua, wipeout tim musuh",
        keywords=("clutch", "comeback", "ulti", "maniac", "savage", "ace", "wipe", "rata", "bantai", "gacor", "lock in"),
        zoom_role="gameplay",
        default_volume=0.68,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Hell_s Kitchen Dramatic Sound",
        category="shock_twist",
        usage_id="Tensi tinggi sebelum blunder fatal, dimarahi squad, atau detik-detik mau kalah",
        keywords=("mati kita", "habis kita", "panik", "tensi", "bahaya banget", "darah tipis", "sekarat", "chaos"),
        zoom_role="facecam",
        default_volume=0.64,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Prowler Goku",
        category="shock_twist",
        usage_id="Aura mengancam saat boss/musuh OP muncul atau player menatap serius sebelum membantai",
        keywords=("bos", "boss", "monster", "pro player", "smurf", "cheater", "hacker", "serem", "aura", "tatapan"),
        zoom_role="facecam",
        default_volume=0.68,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Taco Bell",
        category="shock_twist",
        usage_id="Headshot telak, karma instan, pukulan kenyataan pahit pas sok jago langsung mati",
        keywords=("headshot", "karma", "kena", "telak", "one tap", "makan tuh", "rasain", "langsung mati"),
        zoom_role="gameplay",
        default_volume=0.68,
        max_stinger_sec=2.4,
    ),
    SfxContextSpec(
        name="Crowd Gasp",
        category="shock_twist",
        usage_id="Kaget berjamaah saat nyaris clutch 1 HP, hampir jatuh, atau gacha nyaris dapat mythic",
        keywords=("nyaris", "hampir", "astaga", "ya ampun", "yaelah", "deg-degan", "satu hp", "1 hp"),
        zoom_role="facecam",
        default_volume=0.70,
        max_stinger_sec=1.8,
    ),
    SfxContextSpec(
        name="FAHH",
        category="shock_twist",
        usage_id="Teriakan panik kocak saat jatuh ke jurang, dikejar musuh, atau jumpscare tiba-tiba",
        keywords=("tolong", "lari", "kabur", "kejar", "jurang", "jumpscare", "teriak", "panik"),
        zoom_role="facecam",
        default_volume=0.66,
        max_stinger_sec=2.6,
    ),
    # --- Fail / Blunder / Rage / Damage / Death ---
    SfxContextSpec(
        name="Bonk",
        category="fail_rage",
        usage_id="Kena pukul/ketimpa di kepala, blunder ringan, karma kocak, atau teammate ngelakuin hal bodoh",
        keywords=("bonk", "pukul", "kepala", "bodoh", "bego", "blunder", "kepentok", "ketimpa", "salah"),
        zoom_role="gameplay",
        default_volume=0.76,
        max_stinger_sec=1.2,
    ),
    SfxContextSpec(
        name="Metal Pipe",
        category="fail_rage",
        usage_id="Jatuh keras/nabrak absurd, blunder paling chaos, earrape meme pas momen mati konyol",
        keywords=("nabrak", "jatuh keras", "pipa", "ledak", "ancur", "konyol", "absurd", "rusuh"),
        zoom_role="gameplay",
        default_volume=0.55,
        max_stinger_sec=2.5,
    ),
    SfxContextSpec(
        name="Lego Break Fall Apart",
        category="fail_rage",
        usage_id="Karakter/player langsung mati berkeping-keping, mental pecah, atau turret/base hancur instan",
        keywords=("mati", "tewas", "pecah", "hancur", "roboh", "meledak", "knock", "tumbang", "gagal"),
        zoom_role="gameplay",
        default_volume=0.72,
        max_stinger_sec=2.0,
    ),
    SfxContextSpec(
        name="Garry_s Mod Ragdoll",
        category="fail_rage",
        usage_id="Karakter terpental/ragdoll fisika kocak, mati berulang-ulang di game ragdoll/shooter",
        keywords=("terpental", "terbang", "ragdoll", "kelempar", "nyangkut", "bug", "error"),
        zoom_role="gameplay",
        default_volume=0.65,
        max_stinger_sec=2.6,
    ),
    SfxContextSpec(
        name="Bone Break Crack Snap",
        category="fail_rage",
        usage_id="Fall damage parah dari ketinggian, patah tulang di game survival, atau kena critical hit sakit",
        keywords=("patah", "sakit", "critical", "fall damage", "ketinggian", "kepeleset", "jatuh"),
        zoom_role="gameplay",
        default_volume=0.70,
        max_stinger_sec=2.2,
    ),
    SfxContextSpec(
        name="Minecraft Take Damage",
        category="fail_rage",
        usage_id="Kena hit/damage mendadak ('oof'), kena trap jebakan, atau HP berkurang drastis",
        keywords=("kena hit", "damage", "sakit banget", "jebakan", "trap", "creeper", "hp", "darah"),
        zoom_role="gameplay",
        default_volume=0.74,
        max_stinger_sec=1.8,
    ),
    SfxContextSpec(
        name="Splat",
        category="fail_rage",
        usage_id="Jatuh gepeng, kena lempar slime/bom, atau mati konyol jarak dekat",
        keywords=("gepeng", "nyungsep", "kecemplung", "meleleh", "kena bom", "splat"),
        zoom_role="gameplay",
        default_volume=0.74,
        max_stinger_sec=1.2,
    ),
    SfxContextSpec(
        name="Bamboo Hit",
        category="fail_rage",
        usage_id="Tamparan/pukulan cepat komedi atau cut pukulan berturut-turut saat duel",
        keywords=("tampar", "geplak", "tabok", "smack", "pukul"),
        zoom_role="gameplay",
        default_volume=0.74,
        max_stinger_sec=1.2,
    ),
    SfxContextSpec(
        name="Spongebob Sad Face",
        category="fail_rage",
        usage_id="Kecewa berat, lose streak ranked, gagal clutch 1 HP, atau misi gagal di detik akhir ('boowomp')",
        keywords=("kalah", "lose streak", "turun rank", "sedih", "kecewa", "nyesek", "sayang banget", "gagal total", "rip"),
        zoom_role="facecam",
        default_volume=0.68,
        max_stinger_sec=3.0,
    ),
    SfxContextSpec(
        name="Cartoon Dizzybirds",
        category="fail_rage",
        usage_id="Pusing kena flashbang/stun, salah pencet skill/ulti, atau bingung arah map",
        keywords=("pusing", "stun", "flashbang", "buta", "bingung", "kepencet", "nyasar", "ngakak"),
        zoom_role="facecam",
        default_volume=0.68,
        max_stinger_sec=2.6,
    ),
    # --- Awkward / Freeze Frame / Confused / Chaos ---
    SfxContextSpec(
        name="DJ Stop",
        category="awkward_freeze",
        usage_id="Record scratch! Freeze frame pas salah ngomong, rencana gagal total, atau momen 'yep, that's me'",
        keywords=("eh salah", "tunggu", "bentar", "stop", "freeze", "malah", "kecolongan", "eh kok"),
        zoom_role="facecam",
        default_volume=0.72,
        max_stinger_sec=1.8,
        is_transition_sfx=True,
    ),
    SfxContextSpec(
        name="Awkward",
        category="awkward_freeze",
        usage_id="Hening canggung (cricket), joke garing, satu party kicep diam semua setelah blunder",
        keywords=("canggung", "krik", "diam", "hening", "awkward", "kicep", "bengong", "garing", "sepi"),
        zoom_role="facecam",
        default_volume=0.72,
        max_stinger_sec=2.0,
    ),
    SfxContextSpec(
        name="Blinking Eyes",
        category="awkward_freeze",
        usage_id="Ekspresi bengong loading otak ('huh?'), gak paham kejadian barusan di layar",
        keywords=("huh", "apaan", "kok bisa", "bingung", "loading", "mikir", "gimana", "lah"),
        zoom_role="facecam",
        default_volume=0.70,
        max_stinger_sec=2.4,
    ),
    SfxContextSpec(
        name="Fart with Reverb",
        category="awkward_freeze",
        usage_id="Anti-klimaks konyol saat momen sok serius dirusak fail receh atau troll teammate",
        keywords=("kentut", "receh", "ampas", "zonk", "gagal keren", "noob", "cupu"),
        zoom_role="facecam",
        default_volume=0.62,
        max_stinger_sec=2.5,
    ),
    SfxContextSpec(
        name="Arabic Nokia Ringtone",
        category="awkward_freeze",
        usage_id="Meme chaos/absurd tingkat dewa, kondisi server lag/rusuh gak karuan",
        keywords=("chaos", "rusuh", "server", "lag parah", "aneh banget", "random", "absurd"),
        zoom_role="center",
        default_volume=0.58,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Cartoon Phone Talking",
        category="awkward_freeze",
        usage_id="Teammate/NPC bacot panjang lebar tanpa henti (yapping / ngomel cepat)",
        keywords=("bacot", "ngomel", "ceramah", "bawel", "berisik", "yapping", "komen mulu"),
        zoom_role="facecam",
        default_volume=0.64,
        max_stinger_sec=2.8,
    ),
    SfxContextSpec(
        name="Teeth Clattering",
        category="awkward_freeze",
        usage_id="Gemeteran ketakutan saat main game horor, sembunyi dari monster, atau HP tinggal 1%",
        keywords=("takut", "horor", "gemeter", "deg-degan", "merinding", "serem", "hantu", "sembunyi"),
        zoom_role="facecam",
        default_volume=0.66,
        max_stinger_sec=2.8,
    ),
    SfxContextSpec(
        name="Wobble",
        category="awkward_freeze",
        usage_id="Gerakan ragu-ragu, karakter/kamera goyang aneh, atau glitch lucu",
        keywords=("goyang", "oleng", "ragu", "mabuk", "glitch"),
        zoom_role="gameplay",
        default_volume=0.68,
        max_stinger_sec=2.2,
    ),
    SfxContextSpec(
        name="Censor",
        category="awkward_freeze",
        usage_id="Sensor kata kasar/toxic/misuh saat gamer lagi emosi atau rage",
        keywords=("anjing", "bangsat", "babi", "monyet", "tai", "cok", "asu", "tolol", "sensor", "toxic", "kasar"),
        zoom_role="facecam",
        default_volume=0.66,
        max_stinger_sec=1.2,
    ),
    # --- Game-Specific Actions / Lobby / Loot / Environment ---
    SfxContextSpec(
        name="Counter Strike Item Pickup",
        category="game_action",
        usage_id="Dapat loot bagus, beli senjata/upgrade gear, atau ambil item penting di match",
        keywords=("loot", "senjata", "item", "senapan", "beli", "upgrade", "armor", "equip", "drop"),
        zoom_role="gameplay",
        default_volume=0.74,
        max_stinger_sec=1.2,
    ),
    SfxContextSpec(
        name="Cash Register (Kaching)",
        category="game_action",
        usage_id="Cuan/duit masuk, top-up skin/diamond, dapat bounty besar, atau momen pay-to-win",
        keywords=("cuan", "duit", "uang", "juta", "miliar", "omzet", "kaya", "harga", "bayar", "mahal", "top up", "diamond", "skin", "bounty", "gold"),
        zoom_role="gameplay",
        default_volume=0.74,
        max_stinger_sec=2.2,
    ),
    SfxContextSpec(
        name="Discord Join",
        category="game_action",
        usage_id="Teman mabar baru masuk voice call, player join squad, atau karakter baru muncul",
        keywords=("join", "masuk", "mabar", "datang", "gabung", "discord", "spawn"),
        zoom_role="facecam",
        default_volume=0.76,
        max_stinger_sec=1.4,
        is_transition_sfx=True,
    ),
    SfxContextSpec(
        name="Discord Leave",
        category="game_action",
        usage_id="Rage quit! Teman kabur keluar voice/game, AFK, atau disconnect pas tim lagi kalah",
        keywords=("rage quit", "keluar", "leave", "disconnect", "dc", "afk", "cabut", "ninggalin"),
        zoom_role="facecam",
        default_volume=0.74,
        max_stinger_sec=2.4,
    ),
    SfxContextSpec(
        name="Minecraft Enderman Teleport",
        category="game_action",
        usage_id="Blink/dash/teleport kabur mendadak, lag rubberband, atau musuh tiba-tiba muncul di depan muka",
        keywords=("teleport", "blink", "flicker", "lag", "pindah tempat", "tiba-tiba depan", "enderman"),
        zoom_role="gameplay",
        default_volume=0.70,
        max_stinger_sec=2.4,
    ),
    SfxContextSpec(
        name="Minecraft Cave Ambience",
        category="game_action",
        usage_id="Suasana horor/misterius mendadak, masuk ruangan gelap, atau feeling gak enak sebelum jumpscare",
        keywords=("gelap", "gua", "misteri", "feeling gak enak", "merinding", "horor", "suara aneh"),
        zoom_role="gameplay",
        default_volume=0.68,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Minecraft Walking",
        category="game_action",
        usage_id="Momen jalan santai/sneaking menuju lokasi musuh sebagai build-up sebelum kejadian",
        keywords=("jalan", "ngendap", "sneak", "maju pelan", "nyari musuh"),
        zoom_role="gameplay",
        default_volume=0.66,
        max_stinger_sec=2.5,
    ),
    SfxContextSpec(
        name="Minecraft Drinking",
        category="game_action",
        usage_id="Minum potion/heal/energi di tengah situasi genting atau santai di tengah baku tembak",
        keywords=("minum", "potion", "heal", "shield", "haus", "energi"),
        zoom_role="gameplay",
        default_volume=0.72,
        max_stinger_sec=2.0,
    ),
    SfxContextSpec(
        name="Minecraft Eating",
        category="game_action",
        usage_id="Makan/regen HP santai sementara tim lagi war chaos",
        keywords=("makan", "regen", "ngemil", "santai", "nyawa"),
        zoom_role="gameplay",
        default_volume=0.70,
        max_stinger_sec=2.6,
    ),
    SfxContextSpec(
        name="Munch",
        category="game_action",
        usage_id="Gigitan cepat komedi, nyemil di stream, atau karakter makan item sekilas",
        keywords=("gigit", "nyemil", "krupuk", "makan"),
        zoom_role="facecam",
        default_volume=0.72,
        max_stinger_sec=1.1,
    ),
    SfxContextSpec(
        name="Water Droplet",
        category="game_action",
        usage_id="Efek tetesan air/chat ping ringan, momen jeda mikir atau keringat dingin",
        keywords=("keringat", "tetes", "ping", "notif", "pelan"),
        zoom_role="center",
        default_volume=0.75,
        max_stinger_sec=0.7,
        is_transition_sfx=True,
    ),
    SfxContextSpec(
        name="Stone Sliding",
        category="game_action",
        usage_id="Pintu rahasia terbuka, objek berat bergeser, atau reveal muka datar/Moai pelan",
        keywords=("pintu", "rahasia", "buka", "geser", "patung", "reveal"),
        zoom_role="gameplay",
        default_volume=0.66,
        max_stinger_sec=2.8,
    ),
    # --- Hype / Victory / Rizz / Anime / Vanish ---
    SfxContextSpec(
        name="Ding",
        category="hype_rizz",
        usage_id="Ide cemerlang (aha moment!), poin benar, skor bertambah, atau highlight target tercapai",
        keywords=("ide", "jenius", "pinter", "bener", "poin", "skor", "berhasil", "aha", "dapet"),
        zoom_role="center",
        default_volume=0.74,
        max_stinger_sec=1.2,
    ),
    SfxContextSpec(
        name="Crowd Applause",
        category="hype_rizz",
        usage_id="Tepuk tangan kemenangan epic atau sarkasme pas teammate ngelakuin blunder konyol ('selamat ya')",
        keywords=("menang", "booyah", "victory", "juara", "hebat", "selamat", "tepuk tangan", "gg", "wkwk", "haha", "ngakak"),
        zoom_role="facecam",
        default_volume=0.65,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Holy",
        category="hype_rizz",
        usage_id="Hoki tingkat dewa, RNG mythic/legendary, atau terselamatkan oleh keajaiban pas sekarat",
        keywords=("hoki", "ajaib", "dewa", "mythic", "legendary", "gacha", "jackpot", "berkah", "selamat"),
        zoom_role="gameplay",
        default_volume=0.68,
        max_stinger_sec=3.2,
    ),
    SfxContextSpec(
        name="Rizz",
        category="hype_rizz",
        usage_id="Momen sok keren, emote ke musuh, pamer skin sultan, atau gombal ke karakter/player lain",
        keywords=("keren", "ganteng", "rizz", "sultan", "emote", "taunt", "gombal", "sigma", "kece"),
        zoom_role="facecam",
        default_volume=0.70,
        max_stinger_sec=2.8,
    ),
    SfxContextSpec(
        name="Blush",
        category="hype_rizz",
        usage_id="Malu-malu salah tingkah saat dipuji tim atau ketahuan hal lucu di stream",
        keywords=("malu", "salting", "dipuji", "ciee", "blush", "imut"),
        zoom_role="facecam",
        default_volume=0.72,
        max_stinger_sec=2.0,
    ),
    SfxContextSpec(
        name="Oppai",
        category="hype_rizz",
        usage_id="Stinger anime/wibu super cepat untuk reaksi kaget kocak atau meme pop-culture",
        keywords=("anime", "wibu", "waifu", "nani", "kaget lucu"),
        zoom_role="facecam",
        default_volume=0.70,
        max_stinger_sec=0.8,
    ),
    SfxContextSpec(
        name="Japanese Drum",
        category="hype_rizz",
        usage_id="Pengumuman duel/showdown dramatis atau teriakan hype gaya anime ('YOOO!')",
        keywords=("duel", "by one", "by1", "showdown", "lawan", "siap", "yooo", "mulai"),
        zoom_role="gameplay",
        default_volume=0.72,
        max_stinger_sec=1.8,
    ),
    SfxContextSpec(
        name="Disappear",
        category="hype_rizz",
        usage_id="Teammate kabur menghilang saat lagi war atau karakter lenyap perlahan ('dadah')",
        keywords=("hilang", "menghilang", "ngilang", "kabur", "lenyap", "dadah"),
        zoom_role="facecam",
        default_volume=0.70,
        max_stinger_sec=2.4,
    ),
    SfxContextSpec(
        name="Vanish",
        category="hype_rizz",
        usage_id="Ninja vanish / musuh lenyap seketika saat mau di-kill atau frame kosong mendadak",
        keywords=("lenyap", "ilang", "vanish", "ninja", "mana musuhnya", "kosong"),
        zoom_role="gameplay",
        default_volume=0.70,
        max_stinger_sec=2.2,
    ),
)

_CATALOG_BY_NORM_KEY: dict[str, SfxContextSpec] = {}
for _spec in SFX_GAMING_CONTEXT_CATALOG:
    _CATALOG_BY_NORM_KEY[re.sub(r"[^a-z0-9]+", "", _spec.name.lower())] = _spec


def list_sfx_context_catalog() -> tuple[SfxContextSpec, ...]:
    """Return the curated gaming/meme usage catalog for all bundled SFX files."""
    return SFX_GAMING_CONTEXT_CATALOG


def get_sfx_context(sfx_name: str) -> Optional[SfxContextSpec]:
    """Look up gaming/meme context metadata for an SFX name (case/punctuation-insensitive)."""
    key = re.sub(r"[^a-z0-9]+", "", Path(sfx_name or "").stem.lower())
    if not key:
        return None
    return _CATALOG_BY_NORM_KEY.get(key)


def format_sfx_catalog_for_prompt(
    available_sfx: Sequence[str],
    edit_format: str = "podcast",
) -> str:
    """Format available SFX with their gaming/meme usage contexts for LLM clip curation prompts."""
    avail_set = {re.sub(r"[^a-z0-9]+", "", s.lower()): s for s in available_sfx if s and s.strip()}
    lines: list[str] = []
    seen: set[str] = set()
    for spec in SFX_GAMING_CONTEXT_CATALOG:
        nkey = re.sub(r"[^a-z0-9]+", "", spec.name.lower())
        if nkey not in avail_set:
            continue
        exact_name = avail_set[nkey]
        seen.add(nkey)
        if spec.is_transition_sfx and spec.category == "transition" and edit_format == "game":
            # Keep transition whoosh/swipe separate from contextual meme punchlines unless useful
            lines.append(f"  * {exact_name}: {spec.usage_id} [transisi]")
        else:
            lines.append(f"  * {exact_name}: {spec.usage_id}")
    for nkey, orig in avail_set.items():
        if nkey not in seen:
            lines.append(f"  * {orig}: SFX kustom pengguna")
    return "\n".join(lines)


def get_builtin_sfx_dir() -> Path:
    """Locate built-in `atro/assets/sfx` cleanly for both dev and PyInstaller frozen exe."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "atro" / "assets" / "sfx"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parents[2] / "assets" / "sfx"


def _write_stereo_wav(path: Path, samples: np.ndarray, sample_rate: int = 48000) -> None:
    """Write normalized float [-1..1] mono or stereo array to 16-bit 48kHz stereo WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.ndim == 1:
        stereo = np.stack([samples, samples], axis=1)
    else:
        stereo = samples
    clipped = np.clip(stereo, -0.98, 0.98)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def ensure_builtin_sfx_assets(target_dir: Optional[Path] = None) -> dict[str, Path]:
    """Generate procedural transition SFX WAVs if missing and return `{name: Path}`."""
    sfx_dir = target_dir or get_builtin_sfx_dir()
    sfx_dir.mkdir(parents=True, exist_ok=True)
    sr = 48000
    out: dict[str, Path] = {}

    # 1. Whoosh: airy resonant sweep 180Hz -> 1250Hz -> 220Hz with smooth bell envelope
    whoosh_path = sfx_dir / "whoosh.wav"
    if not whoosh_path.is_file() or whoosh_path.stat().st_size < 1024:
        rng = np.random.default_rng(42)
        n = int(sr * 0.34)
        t = np.linspace(0.0, 0.34, n, dtype=np.float64)
        env = np.sin(math.pi * (t / 0.34)) ** 1.6
        noise = rng.normal(0.0, 1.0, n)
        # Moving-window low-pass / bandpass shaping via FFT sweep weighting
        spec = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1.0 / sr)
        bp = np.exp(-0.5 * ((np.log2(np.maximum(freqs, 20.0)) - np.log2(520.0)) / 1.1) ** 2)
        bp[freqs < 80.0] = 0.0
        bp[freqs > 3600.0] *= 0.2
        shaped = np.fft.irfft(spec * bp, n=n)
        shaped /= max(1e-6, float(np.max(np.abs(shaped))))
        sweep_tone = np.sin(2.0 * math.pi * (140.0 * t + 260.0 * np.sin(math.pi * t / 0.34))) * 0.14
        sig = (shaped * 0.62 + sweep_tone) * env * 0.75
        _write_stereo_wav(whoosh_path, sig, sr)
    out["whoosh"] = whoosh_path

    # 2. Swipe: crisp directional air/chirp transient
    swipe_path = sfx_dir / "swipe.wav"
    if not swipe_path.is_file() or swipe_path.stat().st_size < 1024:
        rng = np.random.default_rng(99)
        n = int(sr * 0.24)
        t = np.linspace(0.0, 0.24, n, dtype=np.float64)
        env = (t / 0.04).clip(0.0, 1.0) * np.exp(-((t - 0.04).clip(min=0.0)) / 0.055)
        phase = 2.0 * math.pi * (950.0 * t - 1600.0 * (t ** 2))
        chirp = np.sin(phase) * 0.45
        noise = rng.normal(0.0, 1.0, n)
        spec = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1.0 / sr)
        hp = np.clip((freqs - 900.0) / 2200.0, 0.0, 1.0) * np.exp(-freqs / 5200.0)
        air = np.fft.irfft(spec * hp, n=n)
        air /= max(1e-6, float(np.max(np.abs(air))))
        sig = (chirp + air * 0.48) * env * 0.78
        _write_stereo_wav(swipe_path, sig, sr)
    out["swipe"] = swipe_path

    # 3. Pop: clean rubber-like bubble transient
    pop_path = sfx_dir / "pop.wav"
    if not pop_path.is_file() or pop_path.stat().st_size < 1024:
        n = int(sr * 0.14)
        t = np.linspace(0.0, 0.14, n, dtype=np.float64)
        env = (t / 0.008).clip(0.0, 1.0) * np.exp(-t / 0.028)
        freq = 360.0 + 620.0 * np.exp(-t / 0.025)
        phase = 2.0 * math.pi * np.cumsum(freq) / sr
        sig = np.sin(phase) * env * 0.80
        _write_stereo_wav(pop_path, sig, sr)
    out["pop"] = pop_path

    return out


CUSTOM_SFX_EXTENSIONS: tuple[str, ...] = (".wav", ".mp3", ".ogg", ".m4a", ".aac", ".flac")


def get_custom_sfx_dirs() -> list[Path]:
    """Folders scanned for user SFX: `<app base>/sfx` first, then bundled `assets/sfx`."""
    dirs: list[Path] = []
    try:
        from atro.config import get_app_base_dir

        dirs.append(Path(get_app_base_dir()) / "sfx")
    except Exception:
        pass
    dirs.append(get_builtin_sfx_dir())
    return dirs


def list_custom_sfx_names() -> list[str]:
    """Return sorted file stems of user SFX (excluding built-in names)."""
    names: set[str] = set()
    for d in get_custom_sfx_dirs():
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in CUSTOM_SFX_EXTENSIONS and f.stem.lower() not in BUILTIN_SFX_NAMES:
                names.add(f.stem)
    return sorted(names, key=str.lower)


import re

_SFX_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm_sfx_key(name: str) -> str:
    return _SFX_NORM_RE.sub("", (name or "").lower())


def list_all_sfx_names() -> list[str]:
    """Return built-in SFX names followed by sorted user custom SFX stems."""
    return [*BUILTIN_SFX_NAMES, *list_custom_sfx_names()]


def _find_custom_sfx(name: str) -> Optional[Path]:
    stem = Path(name).stem.lower()
    norm_stem = _norm_sfx_key(Path(name).stem)
    candidates: list[Path] = []
    for d in get_custom_sfx_dirs():
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in CUSTOM_SFX_EXTENSIONS:
                if f.stem.lower() == stem:
                    return f
                if norm_stem and _norm_sfx_key(f.stem) == norm_stem:
                    candidates.append(f)
    return candidates[0] if candidates else None


def resolve_transition_sfx_path(sfx_name: str) -> Optional[Path]:
    """Return local WAV path for a built-in transition SFX name or `None` if `none`."""
    clean = (sfx_name or "none").strip().lower()
    if clean in ("", "none"):
        return None
    if clean not in BUILTIN_SFX_NAMES:
        return _find_custom_sfx(sfx_name.strip())
    sfx_dir = get_builtin_sfx_dir()
    candidate = sfx_dir / f"{clean}.wav"
    if candidate.is_file() and candidate.stat().st_size >= 1024:
        return candidate
    assets = ensure_builtin_sfx_assets(sfx_dir)
    return assets.get(clean)
