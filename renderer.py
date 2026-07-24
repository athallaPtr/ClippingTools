"""
renderer.py
Proses satu klip end-to-end: potong probe -> transkrip -> cari titik akhir
natural -> crop 9:16 -> hook judul -> burn subtitle -> fade in/out -> render final.
Memanggil fungsi dari face_tracker.py dan transcriber.py.
"""

import subprocess

from face_tracker import build_crop_filter
from transcriber import transcribe_words, find_natural_end, build_ass_subtitle, escape_drawtext

BUFFER_SECONDS = 10       # toleransi tambahan buat cari akhir kalimat yang natural
FADE_DURATION = 1.2       # durasi fade in/out di ujung klip (detik)
HOOK_DURATION = 3         # lama overlay judul hook di pembukaan (detik)
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
    start = clip["start"]
    target_end = clip["end"]
    label = clip.get("label", f"clip_{index}")
    safe_label = "".join(c if c.isalnum() or c in " -_" else "" for c in label).strip().replace(" ", "_")
    base_name = f"{index:02d}_{safe_label}"

    probe_path = f"/content/{base_name}_probe.mp4"
    final_out = f"{output_dir}/{base_name}.mp4"
    ass_path = f"/content/{base_name}.ass"

    probe_duration = (target_end - start) + BUFFER_SECONDS

    # 1) Potong segmen "probe" lebih panjang dari target, buat dianalisis dulu.
    #    Pakai re-encode ringan (bukan -c copy) biar titik potongnya presisi,
    #    supaya timestamp kata dari Whisper nyambung akurat ke video final.
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(start), "-i", source_path, "-t", str(probe_duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        probe_path
    ], capture_output=True)

    # 2) Transkrip kata-per-kata dari probe
    words = transcribe_words(whisper_model, probe_path)

    # 3) Cari titik akhir yang natural (selesai kalimat), bukan potong paksa di tengah
    actual_duration = find_natural_end(words, target_end - start, max_extra=BUFFER_SECONDS)
    actual_duration = max(1.0, min(actual_duration, probe_duration))

    # 4) Kata yang dipakai buat subtitle = yang jatuh dalam durasi final
    clip_words = [w for w in words if w["start"] < actual_duration]
    build_ass_subtitle(clip_words, actual_duration, ass_path)

    # 5) Susun semua filter jadi satu pass:
    #    crop 9:16 -> overlay hook judul (0-3 detik) -> burn subtitle animasi -> fade in/out
    crop_filter = build_crop_filter(source_path, start, start + actual_duration, mode)
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

    # 6) Render final: ambil ulang dari source_path pakai durasi yang sudah disesuaikan
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(start), "-i", source_path, "-t", str(actual_duration),
        "-vf", vf_chain, "-af", af_chain,
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
        final_out
    ], capture_output=True)

    print(
        f"[{index+1}/{total_clips}] Selesai: {base_name}.mp4 "
        f"(diminta {target_end-start:.1f}s -> jadi {actual_duration:.1f}s)"
    )
    return final_out
