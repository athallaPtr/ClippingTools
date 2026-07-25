"""
downloader.py
Bagian yang paling sering butuh update, karena YouTube rutin mengubah
sistem deteksinya. Simpan file ini di GitHub biar bisa di-fix tanpa
kirim ulang notebook ke buyer.
"""

import subprocess
import os
import shutil
import sys

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")

# Pola pesan error yt-dlp yang sering muncul, dipetakan ke penjelasan
# manusiawi + saran yang SESUAI penyebabnya (bukan selalu "export cookies").
ERROR_PATTERNS = [
    ("Private video", "Video ini di-set PRIVATE oleh pemiliknya. Nggak bisa didownload siapapun, termasuk tool ini -- pastikan link video publik."),
    ("Video unavailable", "Video ini sudah tidak tersedia (mungkin dihapus pemiliknya, atau linknya salah). Cek ulang link-nya."),
    ("This video is not available", "Video ini nggak bisa diakses dari lokasi/region server Colab. Coba video lain, atau pakai upload manual."),
    ("age", "Video ini ada batasan umur (age-restricted) dari YouTube. Butuh cookies dari akun yang sudah login & verifikasi umur."),
    ("Sign in to confirm you're not a bot", "YouTube minta verifikasi bahwa ini bukan bot. Ini video/kondisi yang di-flag ketat -- coba export ulang cookies.txt, atau pakai upload manual."),
    ("HTTP Error 429", "Kena rate-limit sementara dari YouTube (kebanyakan request). Coba lagi beberapa menit ke depan."),
    ("Unable to download webpage", "Gangguan koneksi ke YouTube. Coba jalankan ulang cell ini."),
]


def classify_error(stderr_text):
    """Cari tau kemungkinan penyebab gagal dari teks error yt-dlp, kasih pesan yang sesuai."""
    for pattern, message in ERROR_PATTERNS:
        if pattern.lower() in stderr_text.lower():
            return message
    return "Penyebab belum diketahui pasti. Coba video lain, atau pakai upload manual ke folder Drive sebagai alternatif."


def _run_with_progress(cmd):
    """
    Jalankan yt-dlp sambil nampilin progress real-time ke output (bukan
    disembunyiin total sampai selesai), supaya user tau prosesnya masih
    hidup, bukan macet. Return (returncode, full_stderr_text).
    """
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True
    )

    stderr_lines = []
    last_percent_shown = -10
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        stderr_lines.append(line)

        if "%" in line and "ETA" in line:
            # baris progress bar yt-dlp, misal: "[download]  42.3% of 120MiB at 5MiB/s ETA 00:15"
            try:
                percent = float(line.split("%")[0].split()[-1])
                if percent - last_percent_shown >= 10:
                    print(f"  Download: {percent:.0f}%...")
                    last_percent_shown = percent
            except (ValueError, IndexError):
                pass
        elif "ERROR" in line or "Sign in" in line:
            print(f"  {line}")

    process.wait()
    return process.returncode, "\n".join(stderr_lines)


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
        "--retries", "3",             # ulang otomatis kalau gangguan jaringan sesaat
        "--fragment-retries", "3",
        "--sleep-requests", "1",      # jeda antar-request, ngurangin peluang kena rate-limit
        "--geo-bypass",               # coba lewatin pembatasan region kalau ada
        "-o", out_path,
        url
    ]

    if os.path.exists(cookies_path):
        base_cmd = base_cmd + ["--cookies", cookies_path]
        print("Pakai cookies.txt yang sudah di-upload.")

    # Urutan dari yang biasanya paling lengkap/stabil ke yang paling "beda jalur"
    # dari sisi YouTube -- makin ke bawah, makin kemungkinan lolos dari deteksi
    # yang nge-block client di atasnya.
    clients_to_try = [
        "default", "tv_embedded", "android", "ios", "web",
        "mweb", "web_creator", "android_vr",
    ]

    last_error_text = ""
    for client in clients_to_try:
        cmd = list(base_cmd)
        if client != "default":
            cmd = cmd + ["--extractor-args", f"youtube:player_client={client}"]
        print(f"Mencoba client: {client} ...")

        returncode, error_text = _run_with_progress(cmd)
        if returncode == 0:
            print(f"Berhasil dengan client '{client}'.")
            return True

        last_error_text = error_text
        print(f"Gagal dengan client '{client}'.")

    # Semua client gagal -- kasih tau kemungkinan penyebab yang SESUAI,
    # bukan selalu asumsi "butuh cookies".
    reason = classify_error(last_error_text)
    print(f"\nSemua metode download gagal. Kemungkinan penyebab: {reason}")

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

    print("\nMengecek folder upload manual di Drive...")
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
