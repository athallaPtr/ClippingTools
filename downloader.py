"""
downloader.py
Bagian yang paling sering butuh update, karena YouTube rutin mengubah
sistem deteksinya. Simpan file ini di GitHub biar bisa di-fix tanpa
kirim ulang notebook ke buyer.
"""

import subprocess
import os
import shutil

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")


def download_video(url, out_path, cookies_path="/content/cookies.txt"):
    """
    Coba download video dari beberapa 'client' yt-dlp secara berurutan.
    Return True kalau salah satu berhasil, False kalau semua gagal.
    """
    subprocess.run(["pip", "install", "-q", "-U", "yt-dlp"], capture_output=True)
    print("yt-dlp sudah dipastikan versi terbaru.")                
    base_cmd = [
        "yt-dlp",
        "-f", "bv*+ba/b",   # selector fleksibel: video+audio terbaik, fallback ke gabungan terbaik
        "--merge-output-format", "mp4",
        "-o", out_path,
        url
    ]

    if os.path.exists(cookies_path):
        base_cmd = base_cmd + ["--cookies", cookies_path]
        print("Pakai cookies.txt yang sudah di-upload.")

    # "default" = tidak override client sama sekali -- kalau cookies valid,
    # ini biasanya yang paling lengkap daftar formatnya. Sisanya fallback.
    clients_to_try = ["default", "tv_embedded", "android", "ios", "web"]

    for client in clients_to_try:
        cmd = list(base_cmd)
        if client != "default":
            cmd = cmd + ["--extractor-args", f"youtube:player_client={client}"]
        print(f"Mencoba client: {client} ...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Berhasil dengan client '{client}'.")
            return True
        err_lines = [l for l in result.stderr.splitlines() if "ERROR" in l or "Sign in" in l]
        print(f"Gagal dengan client '{client}': {err_lines[-1] if err_lines else result.stderr[-300:]}")

    return False


def get_source_video(video_link, source_path, drive_input_dir="/content/drive/MyDrive/AutoClipper_Input"):
    """
    Coba download otomatis dulu. Kalau gagal (video di-flag ketat oleh YouTube),
    cek apakah user sudah upload video manual ke folder Drive sebagai fallback.

    Return (True, "auto") kalau berhasil download otomatis,
           (True, "manual") kalau berhasil ambil dari upload manual Drive,
           (False, None) kalau dua-duanya belum berhasil.
    """
    ok = download_video(video_link, source_path)
    if ok:
        return True, "auto"

    print("\nDownload otomatis gagal. Mengecek folder upload manual di Drive...")
    os.makedirs(drive_input_dir, exist_ok=True)

    found = None
    for fname in sorted(os.listdir(drive_input_dir)):
        if fname.lower().endswith(VIDEO_EXTENSIONS):
            found = os.path.join(drive_input_dir, fname)
            break

    if found:
        print(f"Video ditemukan di Drive: {found}")
        print("Menyalin ke folder kerja...")
        shutil.copy(found, source_path)
        return True, "manual"

    print(f"Belum ada video di folder Drive: {drive_input_dir}")
    return False, None
