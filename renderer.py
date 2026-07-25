"""
renderer.py
Proses satu klip end-to-end:
  1. Tentukan batas konten utama (opsional: cari titik natural biar tidak
     motong tengah kalimat) -> transkrip -> crop 9:16
  2. Tambah BUMPER 3 detik BISU di depan (freeze frame pertama + judul + fade in)
     dan di belakang (freeze frame terakhir + fade out) -- ini terpisah dari
     konten asli, bukan perpanjangan konten dengan suara asli.
  3. Burn subtitle (cuma di bagian konten utama, sudah digeser +INTRO_PAD_SECONDS)
  4. Render final.
Memanggil fungsi dari face_tracker.py dan transcriber.py.
"""

import subprocess

from face_tracker import build_crop_filter
from transcriber import (
    transcribe_words,
    find_natural_start,
    find_natural_end,
    build_ass_subtitle,
    escape_drawtext,
)

FORWARD_SEARCH_SECONDS = 10   # batas cari akhir kalimat MAJU dari target_end (konten utama)
BACKWARD_SEARCH_SECONDS = 4   # batas cari awal kalimat MUNDUR dari target_start (konten utama)

INTRO_PAD_SECONDS = 3.0       # bumper depan: freeze frame + judul + fade in, BISU
OUTRO_PAD_SECONDS = 3.0       # bumper belakang: freeze frame + fade out, BISU

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def process_clip(index, clip, source_path, output_dir, mode, whisper_model, total_clips):
    """
    index         : urutan klip (buat penamaan file & progress print)
    clip          : dict {"start", "end", "label"}
    source_path   : path video sumber hasil download
    output_dir    : folder Google Drive tempat hasil akhir disimpan
    mode          : "dynamic" atau "center"
    whisper_model : instance WhisperModel yang sudah di-load
    total_clips   : total jumlah klip (buat progress print "[i/total]")
    """
    target_start = clip["start"]
    target_end = clip["end"]
    label = clip.get("label", f"clip_{index}")
    safe_label = "".join(c if c.isalnum() or c in " -_" else "" for c in label).strip().replace(" ", "_")
    base_name = f"{index:02d}_{safe_label}"

    probe_path = f"/content/{base_name}_probe.mp4"
    final_out = f"{output_dir}/{base_name}.mp4"
    ass_path = f"/content/{base_name}.ass"

    # 1) Probe diperluas dikit ke 2 arah, CUMA buat nyari titik kalimat natural
    #    di konten utama (bukan buat bumper -- bumper flat, diurus terpisah di step 4).
    probe_start_abs = max(0, target_start - BACKWARD_SEARCH_SECONDS)
    probe_end_abs = target_end + FORWARD_SEARCH_SECONDS
    probe_duration = probe_end_abs - probe_start_abs

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(probe_start_abs), "-i", source_path, "-t", str(probe_duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        probe_path
    ], capture_output=True)

    words = transcribe_words(whisper_model, probe_path)
    target_start_rel = target_start - probe_start_abs
    target_end_rel = target_end - probe_start_abs

    # 2) Batas konten utama (natural, tidak motong tengah kalimat)
    content_start_rel = find_natural_start(words, target_start_rel, max_extra=BACKWARD_SEARCH_SECONDS)
    content_end_rel = find_natural_end(words, target_end_rel, max_extra=FORWARD_SEARCH_SECONDS)

    content_start_abs = probe_start_abs + content_start_rel
    content_duration = max(1.0, content_end_rel - content_start_rel)

    # 3) Kata-kata buat subtitle, waktunya digeser: relatif ke content_start_rel,
    #    LALU +INTRO_PAD_SECONDS karena di output final, konten utama baru mulai
    #    setelah bumper depan selesai.
    clip_words = [
        {
            "text": w["text"],
            "start": w["start"] - content_start_rel + INTRO_PAD_SECONDS,
            "end": w["end"] - content_start_rel + INTRO_PAD_SECONDS,
        }
        for w in words
        if content_start_rel <= w["start"] < content_end_rel
    ]
    total_duration = INTRO_PAD_SECONDS + content_duration + OUTRO_PAD_SECONDS
    build_ass_subtitle(clip_words, total_duration, ass_path)

    # 4) Filter chain:
    #    crop dulu -> tpad clone frame pertama/terakhir jadi bumper 3 detik masing2 sisi
    #    -> overlay judul di bumper depan -> burn subtitle -> fade in di bumper depan,
    #    fade out di bumper belakang.
    crop_filter = build_crop_filter(source_path, content_start_abs, content_start_abs + content_duration, mode)
    hook_text = escape_drawtext(label.upper())

    vf_chain = (
        f"{crop_filter},"
        f"tpad=start_duration={INTRO_PAD_SECONDS}:start_mode=clone:"
        f"stop_duration={OUTRO_PAD_SECONDS}:stop_mode=clone,"
        f"drawtext=fontfile={FONT_PATH}:text=\'{hook_text}\':fontcolor=white:fontsize=64:"
        f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='between(t,0,{INTRO_PAD_SECONDS})',"
        f"ass={ass_path},"
        f"fade=t=in:st=0:d={INTRO_PAD_SECONDS},"
        f"fade=t=out:st={total_duration - OUTRO_PAD_SECONDS}:d={OUTRO_PAD_SECONDS}"
    )
    # Audio: geser mulai suara asli sejauh INTRO_PAD_SECONDS (otomatis diem di bagian itu),
    # lalu pad diem di akhir sejauh OUTRO_PAD_SECONDS. Tidak perlu fade audio -- bumper
    # memang didesain bisu total, bukan fade dari suara ke diam.
    intro_ms = int(INTRO_PAD_SECONDS * 1000)
    af_chain = (
        f"adelay={intro_ms}|{intro_ms},"
        f"apad=pad_dur={OUTRO_PAD_SECONDS}"
    )

    # 5) Render final: ambil konten dari source_path pakai rentang [content_start_abs, +content_duration],
    #    bumper depan/belakang otomatis ditambahin oleh filter tpad+adelay+apad di atas
    #    (bukan ambil rentang video tambahan dari source -- makanya "flat" persis 3 detik).
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(content_start_abs), "-i", source_path, "-t", str(content_duration),
        "-vf", vf_chain, "-af", af_chain,
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
        final_out
    ], capture_output=True)

    print(
        f"[{index+1}/{total_clips}] Selesai: {base_name}.mp4 "
        f"(konten {content_duration:.1f}s + bumper {INTRO_PAD_SECONDS+OUTRO_PAD_SECONDS:.1f}s = {total_duration:.1f}s)"
    )
    return final_out
