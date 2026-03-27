from fastapi import FastAPI, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.background import BackgroundTask
from TTS.api import TTS
import uvicorn
import tempfile
import os
import re
import secrets
from typing import Optional

app = FastAPI()
security = HTTPBearer()

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
WORKSPACE_DIR = "/workspace"
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
MAX_TEXT_LENGTH = 5000
SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

model_path = "/root/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
tos_file = os.path.join(model_path, "tos_agreed.txt")

os.makedirs(model_path, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)

if not os.path.exists(tos_file):
    with open(tos_file, "w") as f:
        f.write("agreed")

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="AUTH_TOKEN not configured")
    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid authorization token")
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

    tts.tts_to_file(
        text=text, speaker_wav=speaker_path, language="en", file_path=tmp_out_path
    )

    return FileResponse(
        tmp_out_path,
        media_type="audio/wav",
        filename="output.wav",
        background=BackgroundTask(cleanup_file, tmp_out_path),
    )


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5002)
