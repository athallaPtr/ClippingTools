"""
face_tracker.py
Logic deteksi wajah (buat mode crop "dynamic") dan penentuan area crop 9:16.
Sengaja dipisah dari renderer.py biar gampang di-upgrade tanpa nyentuh
bagian rendering.

Pakai OpenCV YuNet (deep-learning based, ~2023) -- jauh lebih akurat
dibanding Haar Cascade lama (2001), terutama buat wajah miring/kecil/
pencahayaan kurang bagus. Model diambil sekali di awal (file .onnx kecil,
~230KB) dari repo resmi OpenCV Zoo, lalu jalan 100% lokal (tidak ada
koneksi internet lagi setelah file itu berhasil didownload).
"""

import os
import cv2

MODEL_PATH = "/content/face_detector_yunet.onnx"
MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
CONFIDENCE_THRESHOLD = 0.6  # abaikan deteksi yang confidence-nya di bawah ini

_face_detector = None  # lazy-loaded, biar mode "center" tidak ikut kena
                        # kalau download model gagal (mode itu tidak butuh ini sama sekali)


def _get_detector():
    """Load model YuNet sekali saja, dipakai berulang. Download file model
    kalau belum ada di disk."""
    global _face_detector
    if _face_detector is None:
        if not os.path.exists(MODEL_PATH):
            import urllib.request
            urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        _face_detector = cv2.FaceDetectorYN.create(MODEL_PATH, "", (0, 0))
    return _face_detector


def get_face_center_x(video_path, start, end, sample_every=15, confidence_threshold=CONFIDENCE_THRESHOLD):
    """Ambil rata-rata posisi horizontal wajah selama segmen, buat crop dinamis.
    Kalau model gagal dimuat (misal download gagal karena jaringan), fallback
    diam-diam ke tengah frame -- tidak sampai bikin seluruh klip gagal."""
    try:
        detector = _get_detector()
    except Exception as e:
        print(f"[face_tracker] Gagal memuat model deteksi wajah ({e}), fallback ke tengah frame.")
        return 0.5

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start * fps))

    centers = []
    frame_idx = 0
    input_size_set = False

    while cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 < end:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample_every == 0:
            h, w = frame.shape[:2]
            if not input_size_set:
                detector.setInputSize((w, h))
                input_size_set = True

            _, faces = detector.detect(frame)
            if faces is not None and len(faces) > 0:
                # tiap baris "faces": [x, y, w, h, ...10 nilai landmark..., confidence]
                # ambil wajah dengan confidence tertinggi
                best = max(faces, key=lambda f: f[-1])
                if best[-1] >= confidence_threshold:
                    fx, fy, fw, fh = best[0], best[1], best[2], best[3]
                    centers.append((fx + fw / 2) / w)
        frame_idx += 1

    cap.release()
    if not centers:
        return 0.5  # fallback: tengah frame (tidak ada wajah confidence cukup terdeteksi)
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
