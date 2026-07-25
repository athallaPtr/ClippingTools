"""
renderer.py
Proses satu klip end-to-end dengan pendekatan 3-segmen terpisah (lebih aman
dari sisi sinkronisasi ffmpeg dibanding 1 filter_complex raksasa):
  1. main.mp4   -> potong+crop konten utama (natural start/end) + subtitle
  2. intro.mp4  -> freeze frame pertama, 3 detik, dim 70% + judul 2 baris
  3. outro.mp4  -> freeze frame terakhir, 3 detik, dim 70%
  4. concat ketiganya jadi hasil akhir.
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

FORWARD_SEARCH_SECONDS = 10
BACKWARD_SEARCH_SECONDS = 4

INTRO_PAD_SECONDS = 3.0
OUTRO_PAD_SECONDS = 3.0
DIM_OPACITY = 0.7
DIM_TRANSITION_SECONDS = 1.0

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
FPS = 30
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

TITLE_MAX_FONTSIZE = 72
TITLE_MIN_FONTSIZE = 36
TITLE_MARGIN_PX = 100


def wrap_label_two_lines(label):
    words = label.strip().split()
    if len(words) <= 1:
        return label.strip(), ""
    best_split, best_diff = 1, None
    for i in range(1, len(words)):
        line1, line2 = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(line1) - len(line2))
        if best_diff is None or diff < best_diff:
            best_diff, best_split = diff, i
    return " ".join(words[:best_split]), " ".join(words[best_split:])


def compute_title_fontsize(line1, line2):
    longest_chars = max(len(line1), len(line2), 1)
    usable_width = CANVAS_WIDTH - (2 * TITLE_MARGIN_PX)
    est_fontsize = int(usable_width / (longest_chars * 0.62))
    return max(TITLE_MIN_FONTSIZE, min(TITLE_MAX_FONTSIZE, est_fontsize))


def _extract_frame(source_path, timestamp, out_png):
    """Ambil 1 frame mentah (belum di-crop) dari source_path di detik tertentu."""
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(max(0, timestamp)), "-i", source_path,
        "-vframes", "1", out_png
    ], capture_output=True)


def _build_bumper(frame_png, crop_filter, duration, dim_fade_type, dim_fade_start,
                   title_lines, out_path):
    """
    Bikin video freeze-frame `duration` detik dari 1 gambar diem, dikasih
    overlay gelap (hold + transisi 1 arah), opsional judul 2 baris, dan
    audio bisu -- semua SELF-CONTAINED (tidak ada penggeseran timestamp
    lintas-stream), supaya tidak rawan masalah sync di ffmpeg.
    """
    line1, line2 = title_lines if title_lines else ("", "")
    title_fontsize = compute_title_fontsize(line1, line2) if line1 else 0
    line_gap = int(title_fontsize * 1.15) if title_fontsize else 0

    draw_title = ""
    if line1:
        l1 = escape_drawtext(line1)
        draw_title += (
            f",drawtext=fontfile={FONT_PATH}:text=\'{l1}\':fontcolor=white:"
            f"fontsize={title_fontsize}:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=(h/2)-{line_gap}"
        )
    if line2:
        l2 = escape_drawtext(line2)
        draw_title += (
            f",drawtext=fontfile={FONT_PATH}:text=\'{l2}\':fontcolor=white:"
            f"fontsize={title_fontsize}:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=(h/2)+{int(line_gap*0.2)}"
        )

    filter_complex = (
        f"[0:v]{crop_filter}[base];"
        f"color=c=black:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:d={duration}:r={FPS},"
        f"format=yuva420p,colorchannelmixer=aa={DIM_OPACITY},"
        f"fade=t={dim_fade_type}:st={dim_fade_start}:d={DIM_TRANSITION_SECONDS}:alpha=1[dim];"
        f"[base][dim]overlay=shortest=1{draw_title}[outv]"
    )

    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", frame_png,
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "1:a",
        "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac",
        out_path
    ], capture_output=True)


def process_clip(index, clip, source_path, output_dir, mode, whisper_model, total_clips):
    target_start = clip["start"]
    target_end = clip["end"]
    label = clip.get("label", f"clip_{index}")
    safe_label = "".join(c if c.isalnum() or c in " -_" else "" for c in label).strip().replace(" ", "_")
    base_name = f"{index:02d}_{safe_label}"

    probe_path = f"/content/{base_name}_probe.mp4"
    intro_frame_png = f"/content/{base_name}_intro.png"
    outro_frame_png = f"/content/{base_name}_outro.png"
    intro_clip = f"/content/{base_name}_intro.mp4"
    outro_clip = f"/content/{base_name}_outro.mp4"
    main_clip = f"/content/{base_name}_main.mp4"
    ass_path = f"/content/{base_name}.ass"
    final_out = f"{output_dir}/{base_name}.mp4"

    # 1) Probe buat nyari titik kalimat natural
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
    content_end_abs = content_start_abs + content_duration

    # 2) Subtitle -- waktunya relatif ke MAIN clip sendiri (mulai dari 0), TIDAK
    #    digeser lagi, karena sekarang main.mp4 adalah file terpisah, bukan
    #    digabung dalam 1 timeline besar.
    clip_words = [
        {"text": w["text"], "start": w["start"] - content_start_rel, "end": w["end"] - content_start_rel}
        for w in words
        if content_start_rel <= w["start"] < content_end_rel
    ]
    build_ass_subtitle(clip_words, content_duration, ass_path)

    # 3) Render main.mp4: crop + burn subtitle
    main_crop_filter = build_crop_filter(source_path, content_start_abs, content_end_abs, mode)
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(content_start_abs), "-i", source_path, "-t", str(content_duration),
        "-vf", f"{main_crop_filter},ass={ass_path}",
        "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-c:a", "aac",
        main_clip
    ], capture_output=True)

    # 4) Ambil frame pertama & terakhir konten (buat bumper freeze)
    _extract_frame(source_path, content_start_abs, intro_frame_png)
    _extract_frame(source_path, max(content_start_abs, content_end_abs - 0.1), outro_frame_png)

    intro_crop_filter = build_crop_filter(source_path, content_start_abs, content_start_abs + 0.5, mode)
    outro_crop_filter = build_crop_filter(source_path, max(content_start_abs, content_end_abs - 0.5), content_end_abs, mode)

    # 5) Render intro.mp4: freeze depan, dim HOLD 2 detik lalu transisi 1 detik ke normal, + judul 2 baris
    line1, line2 = wrap_label_two_lines(label.upper())
    intro_dim_fade_start = INTRO_PAD_SECONDS - DIM_TRANSITION_SECONDS
    _build_bumper(
        intro_frame_png, intro_crop_filter, INTRO_PAD_SECONDS,
        dim_fade_type="out", dim_fade_start=intro_dim_fade_start,
        title_lines=(line1, line2), out_path=intro_clip,
    )

    # 6) Render outro.mp4: freeze belakang, transisi 1 detik ke dim lalu HOLD, tanpa judul
    _build_bumper(
        outro_frame_png, outro_crop_filter, OUTRO_PAD_SECONDS,
        dim_fade_type="in", dim_fade_start=0,
        title_lines=None, out_path=outro_clip,
    )

    # 7) Concat intro + main + outro
    subprocess.run([
        "ffmpeg", "-y",
        "-i", intro_clip, "-i", main_clip, "-i", outro_clip,
        "-filter_complex",
        "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[outv][outa]",
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-c:a", "aac",
        final_out
    ], capture_output=True)

    total_duration = INTRO_PAD_SECONDS + content_duration + OUTRO_PAD_SECONDS
    print(
        f"[{index+1}/{total_clips}] Selesai: {base_name}.mp4 "
        f"(konten {content_duration:.1f}s + bumper {INTRO_PAD_SECONDS+OUTRO_PAD_SECONDS:.1f}s = {total_duration:.1f}s, "
        f"judul: \"{line1}\" / \"{line2}\")"
    )
    return final_out
