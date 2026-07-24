"""
transcriber.py
Transkripsi audio (Whisper), pencarian titik potong kalimat yang natural,
dan pembuatan file subtitle animasi format ASS.
"""

from faster_whisper import WhisperModel

SENTENCE_ENDERS = (".", "!", "?", "...", ".\"", "!\"", "?\"")


def load_whisper_model(size="small", device="cuda", compute_type="float16"):
    """Load model Whisper sekali di awal notebook, lalu dipakai berulang kali."""
    return WhisperModel(size, device=device, compute_type=compute_type)


def transcribe_words(model, path, language="id"):
    """Transkrip audio jadi flat list kata: [{"text","start","end"}, ...] (detik, relatif ke awal file)."""
    segments, _ = model.transcribe(path, language=language, word_timestamps=True)
    words = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            words.append({"text": w.word.strip(), "start": w.start, "end": w.end})
    return words


def find_natural_end(words, target_end, max_extra=10, min_shrink=3):
    """
    Cari titik akhir kalimat paling natural di sekitar target_end, supaya klip
    tidak berhenti di tengah kalimat.
    Prioritas: 1) akhir kalimat pertama SETELAH target_end (dalam batas max_extra)
    -> konteks selesai dulu baru dipotong. 2) kalau tidak ada, akhir kalimat
    SEBELUM target_end (mundur maksimal min_shrink detik). 3) fallback: kata utuh
    terakhir dalam batas max_extra (minimal tidak motong di tengah kata).
    """
    if not words:
        return target_end

    forward = [
        w["end"] for w in words
        if target_end <= w["end"] <= target_end + max_extra
        and w["text"].endswith(SENTENCE_ENDERS)
    ]
    if forward:
        return min(forward)

    backward = [
        w["end"] for w in words
        if target_end - min_shrink <= w["end"] <= target_end
        and w["text"].endswith(SENTENCE_ENDERS)
    ]
    if backward:
        return max(backward)

    within_range = [w["end"] for w in words if w["end"] <= target_end + max_extra]
    if within_range:
        return max(within_range)

    return target_end


def find_natural_start(words, target_start, max_extra=4):
    """
    Cari titik awal kalimat paling natural SEBELUM target_start, supaya klip
    tidak mulai di tengah kalimat (dan ada 'napas' sebelum kata pertama
    diucapkan -- penting karena bagian ini ketutup hook judul + fade in).
    Cari kata yang merupakan AWAL kalimat (kata pertama, atau kata setelah
    tanda baca akhir kalimat sebelumnya) paling dekat sebelum target_start,
    dalam batas mundur max_extra detik. Kalau tidak ketemu, mundur tetap
    sejauh max_extra (asal tidak sampai negatif).
    """
    lower_bound = max(0, target_start - max_extra)
    if not words:
        return lower_bound

    candidates = []
    for i, w in enumerate(words):
        if lower_bound <= w["start"] <= target_start:
            is_sentence_start = (i == 0) or words[i - 1]["text"].endswith(SENTENCE_ENDERS)
            if is_sentence_start:
                candidates.append(w["start"])

    if candidates:
        return max(candidates)  # ambil yang PALING DEKAT ke target_start (bukan yang paling awal)

    return lower_bound


def sec_to_ass_time(sec):
    sec = max(0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def build_ass_subtitle(words, clip_duration, out_path, words_per_group=2):
    """
    Subtitle animasi format ASS: muncul 1-2 kata per giliran (bukan satu
    paragraf sekaligus), dengan efek 'pop' membesar tiap kali teks berganti.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Word,DejaVu Sans,90,&H00FFFFFF,&H000000FF,&H003AA9F0,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,60,60,260,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    groups = []
    for i in range(0, len(words), words_per_group):
        chunk = words[i:i + words_per_group]
        if not chunk:
            continue
        text = " ".join(w["text"] for w in chunk).upper()
        groups.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "text": text})

    lines = [header]
    for i, g in enumerate(groups):
        start = g["start"]
        # sambung ke awal kata berikutnya biar tidak ada jeda kosong di layar
        end = groups[i + 1]["start"] if i + 1 < len(groups) else min(g["end"] + 0.4, clip_duration)
        start_ts = sec_to_ass_time(start)
        end_ts = sec_to_ass_time(end)
        # \t bikin scale dari 70% ke 100% dalam 120ms = efek "pop" tiap kata berganti
        override = "{\\an2\\fscx70\\fscy70\\t(0,120,\\fscx100\\fscy100)}"
        lines.append(f"Dialogue: 0,{start_ts},{end_ts},Word,,0,0,0,,{override}{g['text']}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def escape_drawtext(text):
    """Escape karakter spesial biar aman dipakai di filter ffmpeg drawtext."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")
    text = text.replace("%", "\\%")
    return text
