# athena-tts

A lightweight text-to-speech webserver using the XTTS v2 model from Coqui TTS.

## Features

- XTTS v2 multilingual text-to-speech
- Bearer token authentication
- Speaker voice cloning from uploaded WAV files
- Persistent speaker and model storage
- Rate limiting per IP address
- IP banning after repeated auth failures
- Synchronous API with request queuing (one TTS operation at a time)
- Async job queue API for long-running requests
- Cloudflare-compatible IP detection
- Kubernetes deployment via Helm

## Quick Start (Makefile)

```bash
# Build the image
make build

# Run with GPU
AUTH_TOKEN=your-secret-token make run

# Run without GPU
AUTH_TOKEN=your-secret-token make run-cpu

# Check health
make health

# View logs
make logs

# Stop container
make stop
```

## Docker

### Build

```bash
docker build -t ebennerv/athena-tts:latest .
```

### Run

```bash
docker run -d \
  -p 5002:5002 \
  -e AUTH_TOKEN=your-secret-token \
  -v /path/to/speakers:/workspace \
  -v /path/to/models:/root/.local/share/tts \
  --gpus all \
  ebennerv/athena-tts:latest
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AUTH_TOKEN` | Bearer token for API auth | `""` (required) |
| `RATE_LIMIT_REQUESTS` | Max requests per window | `300` |
| `RATE_LIMIT_WINDOW_SECONDS` | Rate limit window | `60` |
| `AUTH_FAIL_BAN_THRESHOLD` | Auth failures before IP ban | `3` |
| `AUTH_FAIL_BAN_DURATION_SECONDS` | Ban duration | `604800` (1 week) |
| `JOB_EXPIRY_SECONDS` | How long completed jobs are retained | `3600` (1 hour) |
| `MAX_QUEUE_SIZE` | Maximum pending jobs in queue | `100` |

## API

### POST /api/tts

Generate speech from text using a speaker voice.

**Headers:**
- `Authorization: Bearer <AUTH_TOKEN>` (required)

**Form Data:**

Option 1 - Use existing speaker:
- `text` (required): Text to synthesize (max 5000 characters)
- `speaker` (required): Name of existing speaker (without .wav extension)

Option 2 - Upload new speaker:
- `text` (required): Text to synthesize (max 5000 characters)
- `speaker_file` (required): WAV file to use as speaker voice (max 50MB)

**Speaker name constraints:**
- Alphanumeric characters, hyphens, and underscores only
- File extension is stripped automatically

**Example - Existing speaker:**

```bash
curl -X POST http://localhost:5002/api/tts \
  -H "Authorization: Bearer your-secret-token" \
  -F "text=Hello, this is a test." \
  -F "speaker=john" \
  --output output.wav
```

**Example - Upload new speaker:**

```bash
curl -X POST http://localhost:5002/api/tts \
  -H "Authorization: Bearer your-secret-token" \
  -F "text=Hello, this is a test." \
  -F "speaker_file=@jane.wav" \
  --output output.wav
```

The uploaded speaker file is saved to `/workspace` and can be reused by name in subsequent requests.

**Error responses:**
- `400`: Invalid input (missing fields, invalid speaker name, text too long)
- `401`: Unauthorized (invalid token or banned IP)
- `404`: Speaker not found
- `409`: Speaker already exists (when uploading)
- `413`: File too large
- `429`: Rate limit exceeded
- `500`: Server error (AUTH_TOKEN not configured)

### POST /api/tts/job

Submit an async TTS job. Returns immediately with a job ID for polling.

**Headers:**
- `Authorization: Bearer <AUTH_TOKEN>` (required)

**Form Data:**
- `text` (required): Text to synthesize (max 5000 characters)
- `speaker` (required): Name of existing speaker (without .wav extension)

**Example:**

```bash
curl -X POST http://localhost:5002/api/tts/job \
  -H "Authorization: Bearer your-secret-token" \
  -F "text=Hello, this is a test." \
  -F "speaker=john"
```

**Response (202 Accepted):**
```json
{"job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "pending"}
```

**Error responses:**
- `400`: Invalid input (text too long, invalid speaker name)
- `401`: Unauthorized
- `404`: Speaker not found
- `429`: Rate limit exceeded
- `503`: Queue full

### GET /api/tts/job/{job_id}

Get the status of a TTS job.

**Headers:**
- `Authorization: Bearer <AUTH_TOKEN>` (required)

**Example:**

```bash
curl http://localhost:5002/api/tts/job/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer your-secret-token"
```

**Response (pending/processing):**
```json
{"job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "processing"}
```

**Response (completed):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "audio": "BASE64_ENCODED_WAV_DATA..."
}
```

**Response (failed):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "error": "TTS synthesis failed: ..."
}
```

**Error responses:**
- `401`: Unauthorized
- `404`: Job not found
- `429`: Rate limit exceeded

### GET /api/speakers

List available speaker voices.

**Headers:**
- `Authorization: Bearer <AUTH_TOKEN>` (required)

```bash
curl http://localhost:5002/api/speakers \
  -H "Authorization: Bearer your-secret-token"
```

**Response:**
```json
{"speakers": ["jane", "john", "narrator"]}
```

### GET /health

Health check endpoint (no authentication required).

```bash
curl http://localhost:5002/health
```

## Kubernetes Deployment

### Prerequisites

- Kubernetes cluster with GPU nodes
- Helm 3.x
- NVIDIA device plugin installed

### Install

```bash
helm install athena-tts ./helm/athena-tts \
  --set auth.token=your-secret-token
```

### Configuration

Key values in `values.yaml`:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `image.repository` | Container image | `ebennerv/athena-tts` |
| `image.tag` | Image tag | `latest` |
| `auth.token` | Bearer token for API auth | `""` |
| `service.type` | Kubernetes service type | `ClusterIP` |
| `service.port` | Service port | `5002` |
| `security.rateLimitRequests` | Max requests per window | `300` |
| `security.rateLimitWindowSeconds` | Rate limit window | `60` |
| `security.authFailBanThreshold` | Auth failures before IP ban | `3` |
| `security.authFailBanDurationSeconds` | Ban duration | `604800` |
| `persistence.workspace.enabled` | Enable speaker file persistence | `true` |
| `persistence.workspace.size` | PVC size for speaker files | `10Gi` |
| `persistence.modelCache.enabled` | Enable model cache persistence | `true` |
| `persistence.modelCache.size` | PVC size for model cache | `20Gi` |
| `resources.limits.nvidia.com/gpu` | GPU allocation | `1` |

**Important notes:**
- Only `replicaCount: 1` is supported with `ReadWriteOnce` PVCs
- For production, use external secrets management instead of `--set auth.token`
- The `auth.token` value must be non-empty for the API to accept requests

### Upgrade

```bash
helm upgrade athena-tts ./helm/athena-tts \
  --set auth.token=your-secret-token
```

### Uninstall

```bash
helm uninstall athena-tts
```

## Persistent Volumes

Two PVCs are created:
- **workspace**: Speaker WAV files (`/workspace`)
- **model-cache**: XTTS model files (`/root/.local/share/tts`)

The model cache PVC speeds up startup on subsequent deploys by persisting the downloaded model.

## Speaker Files

Speaker WAV files are stored in `/workspace`. Requirements:
- Format: WAV
- Sample rate: 22050 Hz recommended
- Duration: 6-30 seconds of clean speech

## Ethical Use & Disclaimer

**You are solely responsible for the ethical use of this software.** By using athena-tts, you agree to:

- Only clone voices for which you have explicit permission from the voice owner
- Not use this software to create deceptive, fraudulent, or harmful content
- Comply with all applicable laws and regulations regarding synthetic media

**NO LIABILITY:** This software is provided "as is" without warranty of any kind. The authors and contributors accept no responsibility or liability for any misuse, damages, or legal consequences arising from the use of this software. Use at your own risk.

## License

See Coqui TTS license for XTTS model usage terms.
