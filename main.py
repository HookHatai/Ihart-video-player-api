import ipaddress
import os
import shutil
import socket
import subprocess
import tempfile
import zipfile
from urllib.parse import urlparse

import httpx

from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

app = FastAPI(title="Video to Frames + Audio API")

API_KEY = os.getenv("API_KEY")  # необязательно: если задан, нужен заголовок X-API-Key
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
HARD_MAX_FRAMES = int(os.getenv("HARD_MAX_FRAMES", "300"))


def check_key(x_api_key):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")


def save_upload(video: UploadFile, work: str) -> str:
    """Сохраняет видео на диск потоком, с лимитом размера."""
    src = os.path.join(work, "input")
    size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
    with open(src, "wb") as f:
        while chunk := video.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"Video larger than {MAX_UPLOAD_MB} MB")
            f.write(chunk)
    return src


def _check_public_url(url: str):
    """Защита от SSRF: только http(s) и только публичные IP."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise HTTPException(400, "url must be http(s)")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443)
    except socket.gaierror:
        raise HTTPException(400, "Cannot resolve url host")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise HTTPException(400, "url points to a non-public address")


def download_video(url: str, work: str) -> str:
    src = os.path.join(work, "input")
    limit = MAX_UPLOAD_MB * 1024 * 1024
    try:
        with httpx.Client(follow_redirects=False, timeout=60) as client:
            for _ in range(5):  # вручную идём по редиректам, проверяя каждый
                _check_public_url(url)
                with client.stream("GET", url) as r:
                    if r.is_redirect:
                        url = str(r.url.join(r.headers["location"]))
                        continue
                    if r.status_code != 200:
                        raise HTTPException(400, f"Download failed: HTTP {r.status_code}")
                    size = 0
                    with open(src, "wb") as f:
                        for chunk in r.iter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > limit:
                                raise HTTPException(413, f"Video larger than {MAX_UPLOAD_MB} MB")
                            f.write(chunk)
                    return src
            raise HTTPException(400, "Too many redirects")
    except httpx.HTTPError as e:
        raise HTTPException(400, f"Download error: {e}")


def get_source(video, url, work: str) -> str:
    if video is not None:
        return save_upload(video, work)
    if url:
        return download_video(url, work)
    raise HTTPException(400, "Provide a video file or a url")


def run_ffmpeg(cmd):
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=240)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "ffmpeg timeout")
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, f"ffmpeg error: {e.stderr.decode()[-300:]}")


def has_audio(src: str) -> bool:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", src],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


def make_mp3(src: str, dst: str, bitrate: str):
    run_ffmpeg(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src,
                "-vn", "-c:a", "libmp3lame", "-b:a", bitrate, dst])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract")
def extract(
    background: BackgroundTasks,
    video: UploadFile | None = File(default=None),
    url: str | None = None,   # ссылка на видео (вместо файла)
    fps: float = 1.0,            # сколько кадров в секунду брать
    max_frames: int = 100,       # лимит кадров
    width: int = 0,              # 0 = оригинальный размер
    fmt: str = "jpg",            # jpg или png
    include_audio: bool = False, # добавить audio.mp3 в архив
    bitrate: str = "128k",       # битрейт mp3
    x_api_key: str | None = Header(default=None),
):
    check_key(x_api_key)
    if fmt not in ("jpg", "png"):
        raise HTTPException(400, "fmt must be jpg or png")
    if not (0 < fps <= 60):
        raise HTTPException(400, "fps must be in (0, 60]")
    max_frames = max(1, min(max_frames, HARD_MAX_FRAMES))

    work = tempfile.mkdtemp(prefix="frames_")
    background.add_task(shutil.rmtree, work, ignore_errors=True)
    src = get_source(video, url, work)

    out_dir = os.path.join(work, "out")
    os.makedirs(out_dir)

    vf = f"fps={fps}"
    if width > 0:
        vf += f",scale={width}:-2"

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src,
           "-vf", vf, "-frames:v", str(max_frames)]
    if fmt == "jpg":
        cmd += ["-q:v", "3"]
    cmd.append(os.path.join(out_dir, f"frame_%05d.{fmt}"))
    run_ffmpeg(cmd)

    files = sorted(os.listdir(out_dir))
    if not files:
        raise HTTPException(400, "No frames extracted")

    audio_included = False
    if include_audio and has_audio(src):
        make_mp3(src, os.path.join(work, "audio.mp3"), bitrate)
        audio_included = True

    zip_path = os.path.join(work, "frames.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for name in files:
            z.write(os.path.join(out_dir, name), name)
        if audio_included:
            z.write(os.path.join(work, "audio.mp3"), "audio.mp3")

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="frames.zip",
        headers={
            "X-Frame-Count": str(len(files)),
            "X-Has-Audio": str(audio_included).lower(),
        },
    )


@app.post("/audio")
def audio(
    background: BackgroundTasks,
    video: UploadFile | None = File(default=None),
    url: str | None = None,   # ссылка на видео (вместо файла)
    bitrate: str = "128k",
    x_api_key: str | None = Header(default=None),
):
    """Возвращает только звук из видео в формате mp3."""
    check_key(x_api_key)
    work = tempfile.mkdtemp(prefix="audio_")
    background.add_task(shutil.rmtree, work, ignore_errors=True)
    src = get_source(video, url, work)

    if not has_audio(src):
        raise HTTPException(400, "Video has no audio track")

    dst = os.path.join(work, "audio.mp3")
    make_mp3(src, dst, bitrate)
    return FileResponse(dst, media_type="audio/mpeg", filename="audio.mp3")
