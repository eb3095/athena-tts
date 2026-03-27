# athena-tts

A lightweight text-to-speech webserver using the XTTS v2 model from Coqui TTS.

## Features

- XTTS v2 multilingual text-to-speech
- Bearer token authentication
- Speaker voice cloning from uploaded WAV files
- Persistent speaker and model storage
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
- `401`: Invalid or missing authorization token
- `404`: Speaker not found
- `409`: Speaker already exists (when uploading)
- `413`: File too large
- `500`: Server error (AUTH_TOKEN not configured)

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

## License

See Coqui TTS license for XTTS model usage terms.
