"""
renderer.py
Proses satu klip end-to-end:
  1. Tentukan batas konten utama (cari titik natural biar tidak motong tengah
     kalimat) -> transkrip -> crop 9:16
  2. Bumper 3 detik BISU di depan & belakang (freeze frame), dengan overlay
     gelap 70% opacity yang HOLD selama 2 detik lalu transisi ke normal
     dalam 1 detik (bukan fade linear dari 0 -> 100% penuh).
  3. Judul di atas bumper depan: font size otomatis nyesuain panjang teks,
     dipecah jadi 2 baris.
  4. Burn subtitle di bagian konten utama.
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

FORWARD_SEARCH_SECONDS = 10    # batas cari akhir kalimat MAJU dari target_end (konten utama)
BACKWARD_SEARCH_SECONDS = 4    # batas cari awal kalimat MUNDUR dari target_start (konten utama)

INTRO_PAD_SECONDS = 3.0        # bumper depan: freeze frame + judul, BISU
OUTRO_PAD_SECONDS = 3.0        # bumper belakang: freeze frame, BISU

DIM_OPACITY = 0.7              # opacity overlay gelap (0.0 - 1.0)
DIM_TRANSITION_SECONDS = 1.0   # lama transisi dim -> normal (di ujung intro) / normal -> dim (di awal outro)

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

TITLE_MAX_FONTSIZE = 72
TITLE_MIN_FONTSIZE = 36
TITLE_MARGIN_PX = 100          # margin kiri-kanan, biar teks nggak mepet tepi


def wrap_label_two_lines(label):
    """Pecah judul jadi 2 baris sepanjang mungkin seimbang (potong di spasi terdekat tengah)."""
    words = label.strip().split()
    if len(words) <= 1:
        return label.strip(), ""

    best_split = 1
    best_diff = None
    for i in range(1, len(words)):
        line1 = " ".join(words[:i])
        line2 = " ".join(words[i:])
        diff = abs(len(line1) - len(line2))
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_split = i

    return " ".join(words[:best_split]), " ".join(words[best_split:])


def compute_title_fontsize(line1, line2):
    """Font size mengecil otomatis kalau baris terpanjang bakal overflow lebar kanvas."""
    longest_chars = max(len(line1), len(line2), 1)
    usable_width = CANVAS_WIDTH - (2 * TITLE_MARGIN_PX)
    # perkiraan lebar rata-rata karakter huruf kapital bold ~0.62x fontsize
    est_fontsize = int(usable_width / (longest_chars * 0.62))
    return max(TITLE_MIN_FONTSIZE, min(TITLE_MAX_FONTSIZE, est_fontsize))


def process_clip(index, clip, source_path, output_dir, mode, whisper_model, total_clips):
    target_start = clip["start"]
    target_end = clip["end"]
    label = clip.get("label", f"clip_{index}")
    safe_label = "".join(c if c.isalnum() or c in " -_" else "" for c in label).strip().replace(" ", "_")
    base_name = f"{index:02d}_{safe_label}"

    probe_path = f"/content/{base_name}_probe.mp4"
    final_out = f"{output_dir}/{base_name}.mp4"
    ass_path = f"/content/{base_name}.ass"

    # 1) Probe buat nyari titik kalimat natural di konten utama
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

    content_start_rel = find_natural_start(words, target_start_rel, max_extra=BACKWARD_SEARCH_SECONDS)
    content_end_rel = find_natural_end(words, target_end_rel, max_extra=FORWARD_SEARCH_SECONDS)

    content_start_abs = probe_start_abs + content_start_rel
    content_duration = max(1.0, content_end_rel - content_start_rel)
    total_duration = INTRO_PAD_SECONDS + content_duration + OUTRO_PAD_SECONDS

    # 2) Subtitle: waktu digeser +INTRO_PAD_SECONDS (konten utama baru mulai setelah bumper depan)
    clip_words = [
        {
            "text": w["text"],
            "start": w["start"] - content_start_rel + INTRO_PAD_SECONDS,
            "end": w["end"] - content_start_rel + INTRO_PAD_SECONDS,
        }
        for w in words
        if content_start_rel <= w["start"] < content_end_rel
    ]
    build_ass_subtitle(clip_words, total_duration, ass_path)

    # 3) Judul: pecah 2 baris + font size otomatis
    line1, line2 = wrap_label_two_lines(label.upper())
    title_fontsize = compute_title_fontsize(line1, line2)
    line1_esc = escape_drawtext(line1)
    line2_esc = escape_drawtext(line2)
    line_gap = int(title_fontsize * 1.15)

    crop_filter = build_crop_filter(source_path, content_start_abs, content_start_abs + content_duration, mode)

    # 4) Filter graph:
    #    [0:v] crop -> tpad (bumper depan/belakang, freeze frame) -> [main]
    #    lavfi hitam transparan (intro): alpha konstan DIM_OPACITY, lalu di
    #        DIM_TRANSITION_SECONDS terakhir turun ke 0 -> [introdim]
    #    lavfi hitam transparan (outro): alpha 0 di awal, naik ke DIM_OPACITY
    #        dalam DIM_TRANSITION_SECONDS pertama, lalu tetap -> [outrodim]
    #    overlay introdim di [0, INTRO_PAD], overlay outrodim di [total-OUTRO_PAD, total]
    #    -> drawtext judul (2 baris, cuma nongol pas intro) -> burn subtitle
    intro_fade_start = INTRO_PAD_SECONDS - DIM_TRANSITION_SECONDS
    outro_dim_shift = total_duration - OUTRO_PAD_SECONDS

    filter_complex = (
        f"[0:v]{crop_filter},"
        f"tpad=start_duration={INTRO_PAD_SECONDS}:start_mode=clone:"
        f"stop_duration={OUTRO_PAD_SECONDS}:stop_mode=clone[main];"

        f"color=c=black:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:d={INTRO_PAD_SECONDS}:r=30,"
        f"format=yuva420p,colorchannelmixer=aa={DIM_OPACITY},"
        f"fade=t=out:st={intro_fade_start}:d={DIM_TRANSITION_SECONDS}:alpha=1[introdim];"

        f"color=c=black:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:d={OUTRO_PAD_SECONDS}:r=30,"
        f"format=yuva420p,colorchannelmixer=aa={DIM_OPACITY},"
        f"fade=t=in:st=0:d={DIM_TRANSITION_SECONDS}:alpha=1,"
        f"setpts=PTS+{outro_dim_shift}/TB[outrodim];"

        f"[main][introdim]overlay=enable='between(t,0,{INTRO_PAD_SECONDS})'[tmp1];"
        f"[tmp1][outrodim]overlay=enable='between(t,{outro_dim_shift},{total_duration})'[tmp2];"

        f"[tmp2]drawtext=fontfile={FONT_PATH}:text=\'{line1_esc}\':fontcolor=white:fontsize={title_fontsize}:"
        f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h/2)-{line_gap}:"
        f"enable='between(t,0,{INTRO_PAD_SECONDS})',"
        f"drawtext=fontfile={FONT_PATH}:text=\'{line2_esc}\':fontcolor=white:fontsize={title_fontsize}:"
        f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h/2)+{int(line_gap*0.2)}:"
        f"enable='between(t,0,{INTRO_PAD_SECONDS})',"
        f"ass={ass_path}[outv];"

        f"[0:a]adelay={int(INTRO_PAD_SECONDS*1000)}|{int(INTRO_PAD_SECONDS*1000)},"
        f"apad=pad_dur={OUTRO_PAD_SECONDS}[outa]"
    )

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(content_start_abs), "-i", source_path, "-t", str(content_duration),
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
        final_out
    ], capture_output=True)

    print(
        f"[{index+1}/{total_clips}] Selesai: {base_name}.mp4 "
        f"(konten {content_duration:.1f}s + bumper {INTRO_PAD_SECONDS+OUTRO_PAD_SECONDS:.1f}s = {total_duration:.1f}s, "
        f"judul: \"{line1}\" / \"{line2}\" @ {title_fontsize}px)"
    )
    return final_out
