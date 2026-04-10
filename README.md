<p align="center">
  <img src="logo.png" alt="Athena Logo" width="200">
</p>

# athena-tts

A text-to-speech agent using the XTTS v2 model from Coqui TTS. Operates as a distributed worker that registers with athena-server and processes TTS jobs from a shared queue.

## Features

- XTTS v2 multilingual text-to-speech
- Agent-based architecture (registers with athena-server)
- Heartbeat monitoring (reports liveness every minute)
- Speaker voice cloning from WAV files
- **Automatic voice sync from athena-server** - Downloads and caches voices
- Persistent speaker and model storage
- Single-worker model with job polling
- GPU acceleration support

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│  athena-server  │◀────│   athena-tts    │
│    (central)    │────▶│    (agent)      │
└────────┬────────┘     └─────────────────┘
         │                      │
         │  1. Register         │  voices sync
         │  2. Heartbeat        │  (download on
         │  3. Poll for jobs    │   heartbeat)
         │  4. Complete jobs    │
         │                      │
┌────────▼────────┐     ┌───────▼─────────┐
│      Redis      │     │   /workspace    │
│   (job queue)   │     │  (voice cache)  │
└─────────────────┘     └─────────────────┘
```

The agent:
1. Registers with athena-server on startup
2. Syncs voices from server (downloads missing/changed voices)
3. Sends heartbeats every 60 seconds with voice sync
4. Polls for TTS jobs from the server
5. Processes jobs using XTTS v2 model
6. Reports job completion with base64-encoded audio

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `ATHENA_SERVER_URL` | URL of athena-server | (required) |
| `AGENT_KEY` | Shared secret for agent authentication | (required) |
| `POLL_INTERVAL` | Seconds between job polls | `1.0` |

## Quick Start (Docker)

```bash
# Build the image
make build

# Run with GPU (requires athena-server URL and agent key)
docker run -d \
  -e ATHENA_SERVER_URL=https://your-athena-server.com \
  -e AGENT_KEY=your-agent-key \
  -v /path/to/speakers:/workspace \
  -v /path/to/models:/root/.local/share/tts \
  --gpus all \
  ebennerv/athena-tts:latest
```

## Systemd Service

For running on a dedicated GPU server, use the provided systemd service file.

### Setup

1. Create the environment file:

```bash
sudo mkdir -p /etc/athena
sudo cp tts.env.example /etc/athena/tts.env
sudo chmod 600 /etc/athena/tts.env
# Edit with your values
sudo nano /etc/athena/tts.env
```

2. Create storage directories:

```bash
sudo mkdir -p /opt/athena/tts/workspace
sudo mkdir -p /opt/athena/tts/share
sudo chown -R athena:athena /opt/athena
```

3. Install the service:

```bash
sudo cp athena-tts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable athena-tts
sudo systemctl start athena-tts
```

### Service Commands

```bash
# Start/stop/restart
sudo systemctl start athena-tts
sudo systemctl stop athena-tts
sudo systemctl restart athena-tts

# View logs
sudo journalctl -u athena-tts -f

# Check status
sudo systemctl status athena-tts
```

### Environment File (`/etc/athena/tts.env`)

```bash
ATHENA_SERVER_URL=https://your-athena-server.com
AGENT_KEY=your-shared-agent-key
POLL_INTERVAL=1.0
```

## Makefile Commands

| Command | Description |
|---------|-------------|
| `make build` | Build Docker image |
| `make push` | Build and push to registry |
| `make run` | Run container with GPU |
| `make stop` | Stop container |
| `make logs` | View container logs |
| `make health` | Check health endpoint |
| `make lint` | Check code formatting |
| `make fmt` | Format code with black |

## Speaker Files

Speaker WAV files are stored in `/workspace` and are automatically synced from athena-server.

### Voice Sync
On startup and every heartbeat (60 seconds), the agent:
1. Fetches the voice list from athena-server (with checksums)
2. Compares with local voice files
3. Downloads any missing or changed voices

This means voice management is centralized on athena-server - just upload voices there and all TTS agents will automatically sync them.

### Voice Requirements
- Format: WAV
- Sample rate: 22050 Hz
- Channels: Mono
- Duration: 6-30 seconds of clean speech

Use the [athena-voice-print](https://github.com/eb3095/athena-voice-print) tool to convert voice clips to the correct format.

## Persistent Volumes

Two volumes are needed:
- **workspace** (`/workspace`): Speaker WAV files
- **model-cache** (`/root/.local/share/tts`): XTTS model files

The model cache speeds up startup by persisting the downloaded model (~2GB).

## Kubernetes Deployment

For Kubernetes deployment, use the Helm chart:

```bash
helm install athena-tts ./helm/athena-tts \
  --set agent.serverUrl=http://athena-server:5003 \
  --set agent.key=your-agent-key
```

### Helm Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `image.repository` | Container image | `ebennerv/athena-tts` |
| `image.tag` | Image tag | `latest` |
| `agent.serverUrl` | athena-server URL | (required) |
| `agent.key` | Agent authentication key | (required) |
| `agent.pollInterval` | Seconds between polls | `1.0` |
| `persistence.workspace.size` | Speaker storage size | `10Gi` |
| `persistence.modelCache.size` | Model cache size | `20Gi` |
| `resources.limits.nvidia.com/gpu` | GPU allocation | `1` |

## Monitoring

The agent reports its status to athena-server:

```bash
# Check if agent is registered (via athena-server)
curl https://your-athena-server.com/api/agents \
  -H "Authorization: Bearer your-token"
```

Response shows agent status and last seen time:

```json
{
  "agents": [
    {
      "agent_id": "tts-abc123",
      "service_type": "tts",
      "status": "active",
      "last_seen": 1234567890.0
    }
  ]
}
```

To see available voices, query the server directly:

```bash
curl https://your-athena-server.com/api/voices \
  -H "Authorization: Bearer your-token"
```

## Ethical Use & Disclaimer

**You are solely responsible for the ethical use of this software.** By using athena-tts, you agree to:

- Only clone voices for which you have explicit permission from the voice owner
- Not use this software to create deceptive, fraudulent, or harmful content
- Comply with all applicable laws and regulations regarding synthetic media

**NO LIABILITY:** This software is provided "as is" without warranty of any kind. The authors and contributors accept no responsibility or liability for any misuse, damages, or legal consequences arising from the use of this software. Use at your own risk.

## License

See Coqui TTS license for XTTS model usage terms.
