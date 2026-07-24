"""
face_tracker.py
Logic deteksi wajah (buat mode crop "dynamic") dan penentuan area crop 9:16.
Sengaja dipisah dari renderer.py biar gampang di-upgrade (misal ganti Haar
Cascade ke MediaPipe) tanpa nyentuh bagian rendering.
"""

import cv2

# Pakai Haar Cascade bawaan OpenCV (bukan mediapipe) — lebih stabil dan tidak
# butuh install tambahan. Model .xml ini sudah termasuk dalam opencv-python.
_face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


def get_face_center_x(video_path, start, end, sample_every=15):
    """Ambil rata-rata posisi horizontal wajah selama segmen, buat crop dinamis sederhana."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start * fps))
    centers = []
    frame_idx = 0
    while cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 < end:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample_every == 0:
            h, w, _ = frame.shape
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = _face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
            if len(faces) > 0:
                # ambil wajah terbesar (biasanya yang paling dekat/relevan)
                x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                centers.append((x + fw / 2) / w)
        frame_idx += 1
    cap.release()
    if not centers:
        return 0.5  # fallback: tengah frame
    return sum(centers) / len(centers)


def build_crop_filter(video_path, start, end, mode):
    """Hasilkan ffmpeg crop filter buat output 9:16 dari video sumber (biasanya 16:9)."""
    cap = cv2.VideoCapture(video_path)
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    target_w = int(src_h * 9 / 16)
    if mode == "dynamic":
        center_x_ratio = get_face_center_x(video_path, start, end)
    else:
        center_x_ratio = 0.5

    center_x = int(center_x_ratio * src_w)
    x_offset = max(0, min(src_w - target_w, center_x - target_w // 2))
    return f"crop={target_w}:{src_h}:{x_offset}:0,scale=1080:1920"
