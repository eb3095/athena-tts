from fastapi import FastAPI, HTTPException, Depends, File, UploadFile, Form, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.background import BackgroundTask
from TTS.api import TTS
import uvicorn
import tempfile
import os
import re
import secrets
import time
import asyncio
from typing import Optional
from collections import defaultdict

app = FastAPI()
security = HTTPBearer()

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
WORKSPACE_DIR = "/workspace"
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
MAX_TEXT_LENGTH = 5000
SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Rate limiting and security configuration (env vars with defaults)
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", "300"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
AUTH_FAIL_BAN_THRESHOLD = int(os.environ.get("AUTH_FAIL_BAN_THRESHOLD", "3"))
AUTH_FAIL_BAN_DURATION_SECONDS = int(os.environ.get("AUTH_FAIL_BAN_DURATION_SECONDS", "604800"))  # 1 week

# In-memory stores for rate limiting and bans
rate_limit_store: dict[str, list[float]] = defaultdict(list)
auth_fail_store: dict[str, list[float]] = defaultdict(list)
banned_ips: dict[str, float] = {}


def get_client_ip(request: Request) -> str:
    # Cloudflare sets this header with the true client IP
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    # Fallback for other reverse proxies
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_ip_banned(ip: str) -> bool:
    if ip in banned_ips:
        ban_expiry = banned_ips[ip]
        if time.time() < ban_expiry:
            return True
        del banned_ips[ip]
    return False


def record_auth_failure(ip: str):
    now = time.time()
    window_start = now - AUTH_FAIL_BAN_DURATION_SECONDS
    auth_fail_store[ip] = [t for t in auth_fail_store[ip] if t > window_start]
    auth_fail_store[ip].append(now)
    if len(auth_fail_store[ip]) >= AUTH_FAIL_BAN_THRESHOLD:
        banned_ips[ip] = now + AUTH_FAIL_BAN_DURATION_SECONDS
        del auth_fail_store[ip]


def check_rate_limit(ip: str):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if t > window_start]
    if len(rate_limit_store[ip]) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS} seconds.",
        )
    rate_limit_store[ip].append(now)

model_path = "/root/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
tos_file = os.path.join(model_path, "tos_agreed.txt")

os.makedirs(model_path, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)

if not os.path.exists(tos_file):
    with open(tos_file, "w") as f:
        f.write("agreed")

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
tts_semaphore = asyncio.Semaphore(1)


def verify_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    ip = get_client_ip(request)

    if is_ip_banned(ip):
        raise HTTPException(status_code=401, detail="Unauthorized")

    check_rate_limit(ip)

    if not AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="AUTH_TOKEN not configured")

    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        record_auth_failure(ip)
        raise HTTPException(status_code=401, detail="Unauthorized")

    return credentials


def sanitize_speaker_name(name: str) -> str:
    base = os.path.basename(name)
    name_without_ext = os.path.splitext(base)[0]
    if not name_without_ext or not SAFE_NAME_PATTERN.match(name_without_ext):
        raise HTTPException(
            status_code=400,
            detail="Speaker name must contain only alphanumeric characters, hyphens, and underscores",
        )
    return name_without_ext


def cleanup_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


@app.post("/api/tts")
async def synthesize(
    text: str = Form(...),
    speaker: Optional[str] = Form(None),
    speaker_file: Optional[UploadFile] = File(None),
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Text exceeds maximum length of {MAX_TEXT_LENGTH} characters",
        )

    if not speaker and not speaker_file:
        raise HTTPException(
            status_code=400,
            detail="Either speaker name or speaker_file must be provided",
        )

    if speaker and speaker_file:
        raise HTTPException(
            status_code=400,
            detail="Provide either speaker name or speaker_file, not both",
        )

    if speaker_file:
        speaker_name = sanitize_speaker_name(speaker_file.filename or "")
        speaker_path = os.path.join(WORKSPACE_DIR, f"{speaker_name}.wav")

        content = await speaker_file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum size of {MAX_UPLOAD_SIZE // (1024*1024)}MB",
            )

        try:
            fd = os.open(speaker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                os.write(fd, content)
            finally:
                os.close(fd)
        except FileExistsError:
            raise HTTPException(
                status_code=409,
                detail=f"Speaker '{speaker_name}' already exists. Use the speaker name to reference it.",
            )
    else:
        speaker_name = sanitize_speaker_name(speaker or "")
        speaker_path = os.path.join(WORKSPACE_DIR, f"{speaker_name}.wav")

        if not os.path.isfile(speaker_path):
            raise HTTPException(
                status_code=404, detail=f"Speaker '{speaker_name}' not found"
            )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_out:
        tmp_out_path = tmp_out.name

    async with tts_semaphore:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: tts.tts_to_file(
                text=text, speaker_wav=speaker_path, language="en", file_path=tmp_out_path
            ),
        )

    return FileResponse(
        tmp_out_path,
        media_type="audio/wav",
        filename="output.wav",
        background=BackgroundTask(cleanup_file, tmp_out_path),
    )


@app.get("/api/speakers")
async def list_speakers(
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    speakers = []
    for filename in os.listdir(WORKSPACE_DIR):
        if filename.endswith(".wav"):
            speakers.append(filename[:-4])
    return {"speakers": sorted(speakers)}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5002)
