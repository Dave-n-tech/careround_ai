# CareRound AI Service — Integration Guide

This document describes the structure of `careround-ai`, its full request/response contract, and how `careround-core` should connect to it.

---

## Service Overview

`careround-ai` is a standalone Python/FastAPI service that accepts a ward-round audio recording and returns structured clinical data for doctor review. It does not persist data, publish events, or make any clinical decisions. All output is draft output for human review.

- **Runtime:** Python 3.12, FastAPI
- **Port:** `8000`
- **Reached by core at:** `http://<careround-ai-private-ip>:8000` (VPC-private only)

---

## Project Structure

```
careround-ai/
├── main.py                          # FastAPI app, lifespan startup
├── app/
│   ├── config.py                    # Settings from environment / .env
│   ├── routes/
│   │   ├── health.py                # GET /health
│   │   └── process_voice_note.py    # POST /process-voice-note
│   ├── services/
│   │   ├── whisper_service.py       # faster-whisper transcription
│   │   └── llm_service.py          # Ollama LLM, JSON validation, repair
│   ├── models/
│   │   └── schemas.py               # Pydantic request / response / LLM models
│   └── prompts/
│       └── medical_system_prompt.txt
├── tests/
│   ├── conftest.py
│   └── test_process_voice_note.py
└── requirements.txt
```

### Startup sequence

On startup (`main.py`), a background thread calls `load_models()`:

1. `llm_service.load()` — reads the system prompt, creates the Ollama client, fires a warm-up inference, and sets `_ready = True`.
2. `whisper_service.load()` — loads the Whisper model into memory and sets `_ready = True`.

The app begins accepting HTTP requests immediately. `/health` returns `loading` until both services complete warm-up. `careround-core` must poll `/health` and wait for `ready` before forwarding AI requests.

---

## Configuration

All configuration is via environment variables (or `.env` at the project root).

| Variable | Default | Description |
|---|---|---|
| `AI_PROVIDER` | `ollama` | `ollama` = real LLM via Ollama; `stub` = deterministic test output |
| `TRANSCRIPTION_PROVIDER` | `whisper` | `whisper` = real faster-whisper; `stub` = fixed string |
| `OLLAMA_HOST` | `http://localhost:11434` | Full URL of the Ollama server |
| `WHISPER_MODEL` | `base` | Whisper model name (`base.en`, `small`, `large-v3`, etc.) |
| `LLM_MODEL` | `llama3.2:3b` | Ollama model tag (`llama3.2:3b`, `mistral`,  `mistral:7b`, etc.) |

**Stub mode** (`AI_PROVIDER=stub`, `TRANSCRIPTION_PROVIDER=stub`) bypasses all model loading and returns predictable structured output. Use this for local development and CI.

---

## Endpoints

### `GET /health`

Used by `careround-core` to check readiness before forwarding requests.

**Response — loading:**
```json
{
  "status": "loading",
  "whisperLoaded": false,
  "llmLoaded": false
}
```

**Response — ready:**
```json
{
  "status": "ready",
  "whisperLoaded": true,
  "llmLoaded": true
}
```

`careround-core` must return `503` to clients while `status != "ready"`.

---

### `POST /process-voice-note`

**Content-Type:** `multipart/form-data`

#### Request fields

| Field | Type | Required | Description |
|---|---|---|---|
| `audio` | file | Yes | Audio recording. Any format faster-whisper supports (wav, mp3, m4a, webm, ogg, flac). |
| `patient_id` | string | Yes | Patient identifier. Used for logging only; not persisted. |
| `current_time` | string | Yes | ISO 8601 datetime used as the base for administration time calculation. Example: `2025-05-19T10:00:00`. |
| `mode` | string | No | `ward_round` (default) or `transcription_only`. |

#### Mode: `ward_round`

Transcribes the audio, structures it as a SOAP clinical note, extracts prescriptions, and calculates administration times.

**Response — 200 OK:**
```json
{
  "rawTranscription": "Patient is a 54 year old male presenting with fever...",
  "mode": "ward_round",
  "clinicalNote": {
    "subjective": "Patient reports fever and cough for three days.",
    "objective": "Temperature 38.4°C. Reduced breath sounds left base.",
    "assessment": "Community-acquired pneumonia.",
    "plan": "Start Amoxicillin 500mg oral every 6 hours for 4 doses."
  },
  "prescriptions": [
    {
      "drugName": "Amoxicillin",
      "dose": "500mg",
      "route": "oral",
      "frequencyString": "every 6 hours",
      "frequencyHours": 6,
      "totalDoses": 4,
      "administrationTimes": [
        "2025-05-19T10:00:00",
        "2025-05-19T16:00:00",
        "2025-05-19T22:00:00",
        "2025-05-20T04:00:00"
      ]
    }
  ]
}
```

**Prescription with incomplete timing** — if the doctor did not specify frequency or total doses, the LLM sets those fields to `null`. The prescription is still included for human correction:
```json
{
  "drugName": "Paracetamol",
  "dose": "1g",
  "route": "oral",
  "frequencyString": "as needed",
  "frequencyHours": null,
  "totalDoses": null,
  "administrationTimes": []
}
```

**Prescription with missing dose or route** — if the LLM returns `null` for `dose` or `route`, they are normalised to `"Not specified"` rather than failing validation, so the review UI always has a displayable value.

#### Mode: `transcription_only`

Transcribes the audio only. The LLM is not called. Use this for handover notes, nurse notes, and any recording that should not generate prescriptions.

**Response — 200 OK:**
```json
{
  "rawTranscription": "Nurse handover note. Patient in bed 4 had a settled night...",
  "mode": "transcription_only",
  "prescriptions": []
}
```

`clinicalNote` is absent from the response entirely in this mode.

---

## Response Schema Reference

### `ProcessVoiceNoteResponse`

| Field | Type | Present when |
|---|---|---|
| `rawTranscription` | `string` | Always |
| `mode` | `"ward_round"` \| `"transcription_only"` | Always |
| `clinicalNote` | `ClinicalNote` | `mode == "ward_round"` only |
| `prescriptions` | `PrescriptionExtracted[]` | Always (may be empty) |

### `ClinicalNote`

| Field | Type |
|---|---|
| `subjective` | `string` |
| `objective` | `string` |
| `assessment` | `string` |
| `plan` | `string` |

### `PrescriptionExtracted`

| Field | Type | Notes |
|---|---|---|
| `drugName` | `string` | Medication name as spoken |
| `dose` | `string` | e.g. `"500mg"`, `"1g"`. `"Not specified"` if LLM returned null. |
| `route` | `string` | e.g. `"oral"`, `"IV"`. `"Not specified"` if LLM returned null. |
| `frequencyString` | `string \| null` | Exactly as spoken, e.g. `"every 6 hours"` |
| `frequencyHours` | `integer \| null` | Integer hours between doses. `null` if not clearly stated. |
| `totalDoses` | `integer \| null` | Total number of doses. `null` if not clearly stated. |
| `administrationTimes` | `string[]` | ISO 8601 datetime strings. Empty if `frequencyHours` or `totalDoses` is null. |

Administration times are calculated as:
```
administrationTimes[i] = current_time + (i × frequencyHours hours)   for i in 0..totalDoses-1
```

---

## Error Responses

| Status | Condition |
|---|---|
| `503 Service Unavailable` | Models are still loading. `careround-core` should retry after a delay. |
| `422 Unprocessable Entity` | Audio could not be transcribed (unreadable file, zero-length, corrupt). |
| `502 Bad Gateway` | LLM returned output that could not be parsed or validated after one repair attempt. Core should surface a generic AI error to the doctor. |

All error bodies follow FastAPI's default shape:
```json
{ "detail": "human-readable message" }
```

---

## Local Development

### Prerequisites

- Python 3.11 or 3.12
- (Optional, for real LLM) [Ollama](https://ollama.com) installed and running

### First-time setup

```bash
# From the careround-ai repo root
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env               # starts in full stub mode
```

### Option A — Stub mode (no models required)

The fastest way to get the endpoint responding. No Ollama, no Whisper download.

`.env`:
```
AI_PROVIDER=stub
TRANSCRIPTION_PROVIDER=stub
```

Start the service:
```bash
uvicorn main:app --reload --port 8000
```

The service is ready immediately — `/health` returns `ready` on the first request.

Stub responses are deterministic: transcription returns `"Stub transcription for local development."` and the LLM returns one fixed prescription (`Stub Medication 500mg oral twice daily`). This is enough to exercise the full request/response contract from `careround-core` without any AI infrastructure.

### Option B — Real Ollama + real Whisper

Use this when you need to test with actual model output.

1. Install and start Ollama, then pull the model:
   ```bash
   ollama pull llama3.2:3b
   ```

2. Set `.env`:
   ```
   AI_PROVIDER=ollama
   TRANSCRIPTION_PROVIDER=whisper
   OLLAMA_HOST=http://localhost:11434
   WHISPER_MODEL=base.en
   LLM_MODEL=llama3.2:3b
   ```

3. Start the service:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

   Whisper (`base.en`, ~145 MB) downloads automatically on first startup via `faster-whisper`. The LLM warm-up inference fires against Ollama. Watch the logs — `/health` stays `loading` until both are done (typically 15–30 seconds on first run).

### Verifying the service is up

```bash
curl http://localhost:8000/health
# → {"status":"ready","whisperLoaded":true,"llmLoaded":true}
```

FastAPI also serves interactive API docs at [http://localhost:8000/docs](http://localhost:8000/docs) — useful for manually sending test requests without curl.

### Sending a test request with curl

```bash
# Stub mode: any audio file works, content is ignored
curl -X POST http://localhost:8000/process-voice-note \
  -F "audio=@/path/to/any.wav;type=audio/wav" \
  -F "patient_id=local-test" \
  -F "current_time=$(date -u +%Y-%m-%dT%H:%M:%S)" \
  -F "mode=ward_round"
```

For a quick audio fixture without a real recording, generate a 1-second silent WAV:

```python
# generate_test_audio.py
import math, struct, wave
with wave.open("test.wav", "w") as f:
    f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
    for i in range(16000):
        f.writeframes(struct.pack("<h", int(4000 * math.sin(2 * math.pi * 440 * i / 16000))))
```

```bash
python generate_test_audio.py
curl -X POST http://localhost:8000/process-voice-note \
  -F "audio=@test.wav;type=audio/wav" \
  -F "patient_id=local-test" \
  -F "current_time=2025-05-19T10:00:00" \
  -F "mode=ward_round"
```

### Running the tests

Tests use full stub mode and do not require Ollama or Whisper:

```bash
pytest tests -v
```

### Connecting `careround-core` to the local AI service

Set this in `careround-core`'s local `.env` or `application-local.properties`:

```
AI_SERVICE_URL=http://localhost:8000
```

If `careround-core` runs inside Docker and `careround-ai` runs directly on the host, use the host gateway address instead of `localhost`:

```
# Docker Desktop (Mac/Windows)
AI_SERVICE_URL=http://host.docker.internal:8000

# Linux Docker (add --add-host flag when running the container)
AI_SERVICE_URL=http://172.17.0.1:8000
```

The readiness check and request format are identical to production — no code changes needed in core between local and deployed environments, only the URL changes.

---

## Connecting from `careround-core`

### Base URL

```
AI_SERVICE_URL=http://<careround-ai-private-ip>:8000
```

Configure as an environment variable in the core service. Do not hard-code the IP.

### Readiness check before forwarding

Before forwarding any `POST /process-voice-note` request, `careround-core` must verify the AI service is ready:

```
GET {AI_SERVICE_URL}/health
→ 200 { "status": "ready" }   → proceed
→ 200 { "status": "loading" } → return 503 to client
→ connection error             → return 503 to client
```

On deployment, poll `/health` every 5 seconds for up to 5 minutes. Roll back if `ready` is never reached.

### Sending a ward-round request (Java/Spring Boot example)

```java
// Build the multipart request
MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
body.add("audio",       new ByteArrayResource(audioBytes) {
    @Override public String getFilename() { return "recording.wav"; }
});
body.add("patient_id",   patientId);
body.add("current_time", Instant.now().toString());   // ISO 8601
body.add("mode",         "ward_round");

HttpHeaders headers = new HttpHeaders();
headers.setContentType(MediaType.MULTIPART_FORM_DATA);

ResponseEntity<AiVoiceNoteResponse> response = restTemplate.exchange(
    aiServiceUrl + "/process-voice-note",
    HttpMethod.POST,
    new HttpEntity<>(body, headers),
    AiVoiceNoteResponse.class
);
```

### Recommended `AiVoiceNoteResponse` DTO (Java)

```java
public record AiVoiceNoteResponse(
    String                    rawTranscription,
    String                    mode,
    ClinicalNoteDto           clinicalNote,       // null in transcription_only mode
    List<PrescriptionAiDto>   prescriptions
) {}

public record ClinicalNoteDto(
    String subjective,
    String objective,
    String assessment,
    String plan
) {}

public record PrescriptionAiDto(
    String       drugName,
    String       dose,
    String       route,
    String       frequencyString,    // nullable
    Integer      frequencyHours,     // nullable
    Integer      totalDoses,         // nullable
    List<String> administrationTimes
) {}
```

Use `@JsonIgnoreProperties(ignoreUnknown = true)` on the records/classes to tolerate any future additions to the AI response without breaking deserialization.

### Timeout and retry guidance

| Concern | Recommendation |
|---|---|
| Connect timeout | 5 seconds |
| Read timeout | 120 seconds (Whisper on CPU can take 30–60 s for a long recording) |
| Retry on 503 | Yes — exponential backoff, max 3 retries, only while models are loading |
| Retry on 502 | No — LLM parse failure is not transient |
| Retry on 422 | No — audio is unreadable |

### Security

- The AI service must not be reachable from the internet. Place it in a private subnet with no inbound rule from `0.0.0.0/0`.
- `careround-core` reaches it over the VPC private network only.
- Do not log `rawTranscription`, `clinicalNote` content, or prescription text. Log `patient_id`, mode, response latency, and prescription count only.
