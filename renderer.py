"""
renderer.py
Proses satu klip end-to-end: potong probe (mundur+maju dari timestamp asli)
-> transkrip -> cari titik awal & akhir yang natural -> crop 9:16 -> hook
judul -> burn subtitle -> fade in/out -> render final.
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

FORWARD_SEARCH_SECONDS = 10   # batas cari akhir kalimat MAJU dari target_end
BACKWARD_PAD_SECONDS = 4      # batas cari awal kalimat MUNDUR dari target_start
TAIL_PADDING_SECONDS = 3.5    # ekstra napas SETELAH kalimat terakhir selesai, sebelum fade out
FADE_DURATION = 1.2           # durasi fade in/out di ujung klip (detik)
HOOK_DURATION = 3             # lama overlay judul hook di pembukaan (detik)
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def process_clip(index, clip, source_path, output_dir, mode, whisper_model, total_clips):
    """
    index         : urutan klip (buat penamaan file & progress print)
    clip          : dict {"start", "end", "label"}
    source_path   : path video sumber hasil download
    output_dir    : folder Google Drive tempat hasil akhir disimpan
    mode          : "dynamic" atau "center"
    whisper_model : instance WhisperModel yang sudah di-load (dari transcriber.load_whisper_model)
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

    # 1) Potong segmen "probe" yang diperluas ke 2 arah: mundur BACKWARD_PAD_SECONDS
    #    dari target_start, dan maju FORWARD_SEARCH_SECONDS dari target_end.
    probe_start_abs = max(0, target_start - BACKWARD_PAD_SECONDS)
    probe_end_abs = target_end + FORWARD_SEARCH_SECONDS
    probe_duration = probe_end_abs - probe_start_abs

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(probe_start_abs), "-i", source_path, "-t", str(probe_duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        probe_path
    ], capture_output=True)

    # 2) Transkrip kata-per-kata dari probe (timestamp sekarang relatif ke probe_start_abs)
    words = transcribe_words(whisper_model, probe_path)

    # Posisi target_start & target_end relatif ke awal probe
    target_start_rel = target_start - probe_start_abs
    target_end_rel = target_end - probe_start_abs

    # 3) Cari titik awal & akhir yang natural (tidak motong di tengah kalimat)
    actual_start_rel = find_natural_start(words, target_start_rel, max_extra=BACKWARD_PAD_SECONDS)
    actual_end_rel = find_natural_end(words, target_end_rel, max_extra=FORWARD_SEARCH_SECONDS)

    # 4) Tambah padding napas ekstra di akhir, biar fade out tidak langsung
    #    mepet begitu kata terakhir selesai diucapkan
    actual_end_rel = min(actual_end_rel + TAIL_PADDING_SECONDS, probe_duration)

    actual_start_abs = probe_start_abs + actual_start_rel
    actual_duration = max(1.0, actual_end_rel - actual_start_rel)

    # 5) Kata yang dipakai buat subtitle = yang jatuh dalam rentang [actual_start_rel, actual_end_rel],
    #    di-geser supaya waktunya relatif ke actual_start_rel (awal klip final)
    clip_words = [
        {"text": w["text"], "start": w["start"] - actual_start_rel, "end": w["end"] - actual_start_rel}
        for w in words
        if actual_start_rel <= w["start"] < actual_end_rel
    ]
    build_ass_subtitle(clip_words, actual_duration, ass_path)

    # 6) Susun semua filter jadi satu pass:
    #    crop 9:16 -> overlay hook judul (0-3 detik) -> burn subtitle animasi -> fade in/out
    crop_filter = build_crop_filter(source_path, actual_start_abs, actual_start_abs + actual_duration, mode)
    hook_text = escape_drawtext(label.upper())
    fade_in_dur = min(0.4, actual_duration / 4)
    fade_start = max(0, actual_duration - FADE_DURATION)

    vf_chain = (
        f"{crop_filter},"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.55:t=fill:enable='between(t,0,{HOOK_DURATION})',"
        f"drawtext=fontfile={FONT_PATH}:text=\'{hook_text}\':fontcolor=white:fontsize=64:"
        f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t,0,{HOOK_DURATION})',"
        f"ass={ass_path},"
        f"fade=t=in:st=0:d={fade_in_dur},"
        f"fade=t=out:st={fade_start}:d={FADE_DURATION}"
    )
    af_chain = (
        f"afade=t=in:st=0:d={fade_in_dur},"
        f"afade=t=out:st={fade_start}:d={FADE_DURATION}"
    )

    # 7) Render final: ambil ulang dari source_path pakai rentang [actual_start_abs, +actual_duration]
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(actual_start_abs), "-i", source_path, "-t", str(actual_duration),
        "-vf", vf_chain, "-af", af_chain,
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
        final_out
    ], capture_output=True)

    print(
        f"[{index+1}/{total_clips}] Selesai: {base_name}.mp4 "
        f"(diminta {target_end-target_start:.1f}s -> jadi {actual_duration:.1f}s)"
    )
    return final_out
