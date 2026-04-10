from contextlib import asynccontextmanager
from fastapi import FastAPI
from TTS.api import TTS
import uvicorn
import httpx
import tempfile
import os
import sys
import re
import asyncio
import uuid
import base64
import hashlib
import logging
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

WORKSPACE_DIR = "/workspace"
MAX_TEXT_LENGTH = 5000
SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

ATHENA_SERVER_URL = os.environ.get("ATHENA_SERVER_URL", "").strip()
AGENT_KEY = os.environ.get("AGENT_KEY", "").strip()
AGENT_ID = os.environ.get("AGENT_ID", str(uuid.uuid4())).strip()
AGENT_SERVICE_TYPE = "tts"
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "1.0").strip())
HEARTBEAT_INTERVAL = 60.0

logger.info(
    f"Config: ATHENA_SERVER_URL={ATHENA_SERVER_URL[:30] + '...' if ATHENA_SERVER_URL else 'NOT SET'}"
)
logger.info(f"Config: AGENT_KEY={'SET' if AGENT_KEY else 'NOT SET'}")

http_client: Optional[httpx.AsyncClient] = None
background_tasks: list = []

model_path = "/root/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
tos_file = os.path.join(model_path, "tos_agreed.txt")

os.makedirs(model_path, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)

if not os.path.exists(tos_file):
    with open(tos_file, "w") as f:
        f.write("agreed")

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
tts_semaphore = asyncio.Semaphore(1)


def sanitize_speaker_name(name: str) -> str:
    base = os.path.basename(name)
    name_without_ext = os.path.splitext(base)[0]
    if not name_without_ext or not SAFE_NAME_PATTERN.match(name_without_ext):
        raise ValueError(
            "Speaker name must contain only alphanumeric characters, hyphens, and underscores"
        )
    return name_without_ext


def get_available_speakers() -> list[str]:
    """Get list of available speaker names from workspace directory."""
    speakers = []
    try:
        for filename in os.listdir(WORKSPACE_DIR):
            if filename.endswith(".wav"):
                speakers.append(filename[:-4])
    except OSError:
        pass
    return sorted(speakers)


def get_local_voice_checksum(name: str) -> Optional[str]:
    """Calculate MD5 checksum of a local voice file."""
    filepath = os.path.join(WORKSPACE_DIR, f"{name}.wav")
    if not os.path.isfile(filepath):
        return None
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def get_local_voices_with_checksums() -> dict[str, str]:
    """Get dict of local voice names to their checksums."""
    voices = {}
    for name in get_available_speakers():
        checksum = get_local_voice_checksum(name)
        if checksum:
            voices[name] = checksum
    return voices


async def agent_register():
    """Register with athena-server as an agent."""
    try:
        response = await http_client.post(
            f"{ATHENA_SERVER_URL}/api/agents/register",
            headers={"X-Agent-Key": AGENT_KEY},
            json={
                "agent_id": AGENT_ID,
                "service_type": AGENT_SERVICE_TYPE,
            },
            timeout=10.0,
        )
        if response.status_code == 200:
            logger.info(f"Registered as agent {AGENT_ID}")
            return True
        else:
            logger.error(f"Failed to register: {response.status_code} {response.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to register: {e}")
        return False


async def agent_heartbeat():
    """Send heartbeat to athena-server."""
    try:
        response = await http_client.post(
            f"{ATHENA_SERVER_URL}/api/agents/heartbeat",
            headers={"X-Agent-Key": AGENT_KEY},
            json={
                "agent_id": AGENT_ID,
                "service_type": AGENT_SERVICE_TYPE,
            },
            timeout=10.0,
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Heartbeat error: {e}")
        return False


async def fetch_server_voices() -> list[dict]:
    """Fetch voice list from server with checksums."""
    try:
        response = await http_client.get(
            f"{ATHENA_SERVER_URL}/api/voices/list",
            headers={"X-Agent-Key": AGENT_KEY},
            timeout=30.0,
        )
        if response.status_code == 200:
            return response.json().get("voices", [])
        else:
            logger.error(f"Failed to fetch voice list: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Error fetching voice list: {e}")
        return []


async def download_voice(name: str) -> bool:
    """Download a voice file from server."""
    try:
        response = await http_client.get(
            f"{ATHENA_SERVER_URL}/api/voices/{name}/download",
            headers={"X-Agent-Key": AGENT_KEY},
            timeout=120.0,
        )
        if response.status_code == 200:
            filepath = os.path.join(WORKSPACE_DIR, f"{name}.wav")
            with open(filepath, "wb") as f:
                f.write(response.content)
            logger.info(f"Downloaded voice: {name}")
            return True
        else:
            logger.error(f"Failed to download voice {name}: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error downloading voice {name}: {e}")
        return False


async def sync_voices():
    """Sync voices from server - download missing or changed voices."""
    server_voices = await fetch_server_voices()
    if not server_voices:
        logger.info("No voices from server or fetch failed, skipping sync")
        return
    
    local_voices = get_local_voices_with_checksums()
    
    for voice in server_voices:
        name = voice.get("name")
        server_checksum = voice.get("checksum")
        
        if not name or not server_checksum:
            continue
        
        local_checksum = local_voices.get(name)
        
        if local_checksum != server_checksum:
            if local_checksum:
                logger.info(f"Voice {name} changed, re-downloading...")
            else:
                logger.info(f"Voice {name} missing, downloading...")
            await download_voice(name)


async def agent_poll():
    """Poll for a job from athena-server."""
    try:
        response = await http_client.post(
            f"{ATHENA_SERVER_URL}/api/agents/jobs/poll",
            headers={"X-Agent-Key": AGENT_KEY},
            json={"agent_id": AGENT_ID, "service_type": AGENT_SERVICE_TYPE},
            timeout=10.0,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        return data.get("job")
    except Exception as e:
        logger.error(f"Poll error: {e}")
        return None


async def agent_complete(
    job_id: str, status: str, result: Optional[dict], error: Optional[str]
):
    """Report job completion to athena-server."""
    try:
        payload = {
            "agent_id": AGENT_ID,
            "status": status,
            "result": result,
            "error": error,
        }
        payload_size = len(str(payload))
        logger.info(
            f"Completing job {job_id} with status={status}, payload_size={payload_size}"
        )

        response = await http_client.post(
            f"{ATHENA_SERVER_URL}/api/agents/jobs/{job_id}/complete",
            headers={"X-Agent-Key": AGENT_KEY},
            json=payload,
            timeout=60.0,
        )

        if response.status_code == 200:
            logger.info(f"Job {job_id} completed successfully")
            return True
        else:
            logger.error(
                f"Job {job_id} complete failed: {response.status_code} {response.text}"
            )
            return False
    except Exception as e:
        logger.error(f"Complete error for job {job_id}: {e}")
        return False


async def process_agent_job(job: dict):
    """Process a TTS job received from athena-server."""
    job_id = job["job_id"]
    payload = job["payload"]
    text = payload.get("text", "")
    speaker = payload.get("speaker", "")

    if len(text) > MAX_TEXT_LENGTH:
        await agent_complete(
            job_id, "failed", None, f"Text exceeds max length of {MAX_TEXT_LENGTH}"
        )
        return

    try:
        speaker_name = sanitize_speaker_name(speaker)
        speaker_path = os.path.join(WORKSPACE_DIR, f"{speaker_name}.wav")

        if not os.path.isfile(speaker_path):
            await agent_complete(
                job_id, "failed", None, f"Speaker '{speaker_name}' not found"
            )
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_out:
            tmp_out_path = tmp_out.name

        async with tts_semaphore:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: tts.tts_to_file(
                    text=text,
                    speaker_wav=speaker_path,
                    language="en",
                    file_path=tmp_out_path,
                ),
            )

        with open(tmp_out_path, "rb") as f:
            audio_bytes = f.read()
        os.remove(tmp_out_path)

        audio_base64 = base64.b64encode(audio_bytes).decode()
        await agent_complete(job_id, "completed", {"audio": audio_base64}, None)

    except Exception as e:
        await agent_complete(job_id, "failed", None, str(e))


async def agent_worker():
    """Main agent worker loop - polls for jobs and processes them."""
    logger.info("Agent worker starting...")
    try:
        registered = False
        while not registered:
            logger.info("Attempting to register...")
            registered = await agent_register()
            if not registered:
                logger.info("Registration failed, retrying in 5s...")
                await asyncio.sleep(5)

        logger.info("Registration complete, starting poll loop")
        while True:
            job = await agent_poll()

            if job:
                await process_agent_job(job)
            else:
                await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        logger.info("Agent worker shutting down gracefully")
        raise


async def heartbeat_worker():
    """Background task to send heartbeats and sync voices."""
    logger.info("Heartbeat worker starting...")
    try:
        logger.info("Initial voice sync starting...")
        await sync_voices()
        logger.info("Initial voice sync complete")
        
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await agent_heartbeat()
            await sync_voices()
    except asyncio.CancelledError:
        logger.info("Heartbeat worker shutting down gracefully")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client

    logger.info("Lifespan startup beginning...")

    if not ATHENA_SERVER_URL or not AGENT_KEY:
        logger.error("ATHENA_SERVER_URL and AGENT_KEY are required!")
        raise RuntimeError("ATHENA_SERVER_URL and AGENT_KEY are required")

    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )

    logger.info(f"Agent mode - connecting to {ATHENA_SERVER_URL}")
    background_tasks.append(asyncio.create_task(agent_worker()))
    background_tasks.append(asyncio.create_task(heartbeat_worker()))
    logger.info("Background tasks started")

    yield

    for task in background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    background_tasks.clear()

    if http_client:
        await http_client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5002)
