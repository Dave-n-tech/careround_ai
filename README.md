# CareRound AI Service — Integration Guide

This document describes the structure of `careround-ai`, its full request/response contract, and how `careround-core` should connect to it in both production and local development.

---

## Table of Contents

1. [Service Overview](#service-overview)
2. [Project Structure](#project-structure)
3. [Configuration](#configuration)
4. [Local Development](#local-development)
5. [Endpoints](#endpoints)
   - GET /health
   - POST /process-voice-note (SSE)
6. [Response Schema Reference](#response-schema-reference)
7. [Error Responses](#error-responses)
8. [Connecting from careround-core](#connecting-from-careround-core)

---

## Service Overview

`careround-ai` is a standalone Python/FastAPI service that accepts a ward-round audio recording and returns structured clinical data for doctor review. It does not persist data, publish events, or make any clinical decisions. All output is draft output for human review.

- **Runtime:** Python 3.12, FastAPI
- **Port:** `8000`
- **Production:** `http://<careround-ai-private-ip>:8000` (VPC-private only)
- **Local dev:** `http://localhost:8000`

---

## Project Structure

```
careround-ai/
├── main.py                          # FastAPI app, lifespan startup
├── app/
│   ├── config.py                    # Settings from environment / .env
│   ├── routes/
│   │   ├── health.py                # GET /health
│   │   └── process_voice_note.py    # POST /process-voice-note (SSE)
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
├── pytest.ini
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
| `LLM_MODEL` | `mistral` | Ollama model tag (`mistral`, `llama3.2:3b`, `mistral:7b`, etc.) |

**Stub mode** (`AI_PROVIDER=stub`, `TRANSCRIPTION_PROVIDER=stub`) bypasses all model loading and returns predictable structured output. Use this for local development and CI when you don't have Ollama or GPU access.

---

## Local Development

### Prerequisites

- Python 3.11 or 3.12
- [Ollama](https://ollama.ai) — only required for `AI_PROVIDER=ollama` (not needed for stub mode)

### Setup

```bash
git clone <repo>
cd careround-ai
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Option A — Stub mode (no models required)

The fastest way to run the service. No Ollama installation needed. The service returns predictable structured output that exercises the full HTTP contract.

Create or update `.env`:
```
AI_PROVIDER=stub
TRANSCRIPTION_PROVIDER=stub
```

Start the service:
```bash
uvicorn main:app --reload --port 8000
```

The service is immediately ready — `/health` returns `ready` with no warm-up wait.

Verify with curl:
```bash
# Health check
curl http://localhost:8000/health

# Ward round — observe SSE events arriving progressively
curl -N -X POST http://localhost:8000/process-voice-note \
  -F "audio=@any-file.wav" \
  -F "patient_id=test-patient" \
  -F "current_time=2025-05-19T10:00:00" \
  -F "mode=ward_round"

# Transcription only
curl -N -X POST http://localhost:8000/process-voice-note \
  -F "audio=@any-file.wav" \
  -F "patient_id=test-patient" \
  -F "current_time=2025-05-19T10:00:00" \
  -F "mode=transcription_only"
```

The `-N` flag disables curl's output buffering so SSE events print as they arrive. The stub transcription always returns `"Stub transcription for local development."` and the stub LLM returns a fixed SOAP note with one prescription.

### Option B — Real Ollama (local LLM, no GPU required)

Use this to test actual LLM output locally.

1. [Install Ollama](https://ollama.ai/download) and pull the model:
   ```bash
   ollama pull llama3.2:3b
   ```

2. Update `.env`:
   ```
   AI_PROVIDER=ollama
   TRANSCRIPTION_PROVIDER=stub    # keep Whisper stubbed unless you want real audio
   OLLAMA_HOST=http://localhost:11434
   LLM_MODEL=llama3.2:3b
   ```

3. Start the service:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

4. Wait for `/health` to return `"status": "ready"` — the LLM warm-up inference runs on startup.

### Interactive API docs

FastAPI generates interactive docs at `http://localhost:8000/docs`. You can send test requests directly from the browser, including file uploads for the audio field. Note that the browser docs interface will show the raw SSE text rather than rendering the stream.

### Running the tests

```bash
pytest tests
```

No models or Ollama connection required — all tests patch the service layer.

### Connecting careround-core to the local AI service

Set the following in `careround-core`'s local `.env` or `application-local.properties`:

```
AI_SERVICE_URL=http://localhost:8000
```

`careround-core` should poll `${AI_SERVICE_URL}/health` before forwarding requests. In stub mode the service is ready immediately, so the first poll will return `ready`.

If `careround-core` and `careround-ai` are both running in Docker Compose on the same network, use the service name instead:

```
AI_SERVICE_URL=http://careround-ai:8000
```

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
**Response:** `200 OK`, `Content-Type: text/event-stream`

Results are streamed progressively as Server-Sent Events. The `transcription_complete` event arrives as soon as Whisper finishes (~30–60 s on CPU), so the UI can show the raw transcription without waiting for the LLM.

#### Request fields

| Field | Type | Required | Description |
|---|---|---|---|
| `audio` | file | Yes | Audio recording. Any format faster-whisper supports (wav, mp3, m4a, webm, ogg, flac). |
| `patient_id` | string | Yes | Patient identifier. Used for logging only; not persisted. |
| `current_time` | string | Yes | ISO 8601 datetime used as the base for administration time calculation. Example: `2025-05-19T10:00:00`. |
| `mode` | string | No | `ward_round` (default) or `transcription_only`. |

Readiness is checked before the stream starts. If models are loading, the server responds with a standard `503` before emitting any events.

#### Event sequence — `ward_round`

`transcription_complete` is a lightweight progress signal emitted as soon as Whisper finishes. It carries no payload — the full transcription arrives in `processing_complete` along with the structured clinical data.

```
event: transcription_complete
data: {}

event: processing_complete
data: {
  "rawTranscription": "Patient is a 54 year old male...",
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

event: done
data: {}
```

#### Event sequence — `transcription_only`

```
event: transcription_complete
data: {}

event: processing_complete
data: {
  "rawTranscription": "Nurse handover note. Patient in bed 4 had a settled night...",
  "mode": "transcription_only",
  "prescriptions": []
}

event: done
data: {}
```

#### Error event

If a stage fails after the stream has started (HTTP headers already sent), an `error` event is emitted and the stream closes. The client must treat `error` as terminal.

```
event: transcription_complete
data: {}

event: error
data: {"detail": "LLM returned unprocessable output"}
```

#### Event summary

| Event | When emitted | Data fields |
|---|---|---|
| `transcription_complete` | Whisper finishes (~30–60 s on CPU) | *(empty object — UX progress signal only)* |
| `processing_complete` | LLM + admin time calc finishes (~5–30 s) | `rawTranscription`, `mode`, `clinicalNote` (ward_round only), `prescriptions` |
| `done` | Stream ends normally | *(empty object)* |
| `error` | Any stage fails after headers sent | `detail` |

#### Prescription edge cases

**Incomplete timing** — if the doctor didn't clearly state frequency or total doses, `frequencyHours` and `totalDoses` are omitted and `administrationTimes` is an empty array. The prescription is still included for human correction in the review UI.

**Missing dose or route** — if the LLM returns `null` for `dose` or `route`, they are normalised to `"Not specified"` so the review UI always has a displayable value.

#### Consuming from `careround-core` (Spring Boot / WebFlux)

Spring's `WebClient` handles SSE natively. Consume the stream and forward each event to the frontend via your own SSE emitter or WebSocket.

```java
WebClient webClient = WebClient.builder()
    .baseUrl(aiServiceUrl)
    .build();

Flux<ServerSentEvent<String>> sseStream = webClient.post()
    .uri("/process-voice-note")
    .contentType(MediaType.MULTIPART_FORM_DATA)
    .bodyValue(multipartBody)
    .retrieve()
    .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {});

sseStream.subscribe(event -> {
    switch (event.event()) {
        case "transcription_complete" -> forwardProgressSignal();             // empty payload — triggers UI loading state
        case "processing_complete"    -> forwardFullResult(event.data());     // rawTranscription + clinicalNote + prescriptions
        case "done"                   -> closeClientStream();
        case "error"                  -> handleAiError(event.data());
    }
});
```

#### Consuming directly in the browser (frontend)

Use `fetch` with a `ReadableStream` rather than `EventSource` — `EventSource` reconnects automatically on close, which is unwanted for a one-shot request.

```javascript
const response = await fetch('/api/v1/ai/process-voice-note', {
  method: 'POST',
  body: formData,   // FormData containing audio, patient_id, current_time, mode
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const messages = buffer.split('\n\n');
  buffer = messages.pop();          // keep any incomplete trailing chunk

  for (const message of messages) {
    const eventName = message.match(/^event: (.+)$/m)?.[1];
    const dataLine  = message.match(/^data: (.+)$/ms)?.[1];
    if (!eventName || !dataLine) continue;

    const data = JSON.parse(dataLine);
    if (eventName === 'transcription_complete') showLoadingSpinner();          // empty payload — UX signal only
    if (eventName === 'processing_complete')    showFullResult(data);          // rawTranscription, clinicalNote, prescriptions
    if (eventName === 'done')                   finalise();
    if (eventName === 'error')                  showError(data.detail);
  }
}
```

---

## Response Schema Reference

### `ClinicalNote` (in `processing_complete` event)

| Field | Type |
|---|---|
| `subjective` | `string` |
| `objective` | `string` |
| `assessment` | `string` |
| `plan` | `string` |

### `PrescriptionExtracted` (in `processing_complete` event)

| Field | Type | Notes |
|---|---|---|
| `drugName` | `string` | Medication name as spoken |
| `dose` | `string` | e.g. `"500mg"`, `"1g"`. `"Not specified"` if LLM returned null. |
| `route` | `string` | e.g. `"oral"`, `"IV"`. `"Not specified"` if LLM returned null. |
| `frequencyString` | `string` | Exactly as spoken. Omitted if null. |
| `frequencyHours` | `integer` | Hours between doses. Omitted if not clearly stated. |
| `totalDoses` | `integer` | Total number of doses. Omitted if not clearly stated. |
| `administrationTimes` | `string[]` | ISO 8601 datetime strings. Empty array if timing fields are missing. |

Administration times are calculated as:
```
administrationTimes[i] = current_time + (i × frequencyHours hours)   for i in 0..totalDoses-1
```

### Recommended Java DTOs

```java
// transcription_complete carries no data — it is a UX progress signal only.
// No DTO required; just use the event name to transition the UI state.

@JsonIgnoreProperties(ignoreUnknown = true)
public record AiProcessingCompleteEvent(
    String                  rawTranscription,
    String                  mode,
    ClinicalNoteDto         clinicalNote,        // null when mode = "transcription_only"
    List<PrescriptionAiDto> prescriptions
) {}

@JsonIgnoreProperties(ignoreUnknown = true)
public record ClinicalNoteDto(
    String subjective,
    String objective,
    String assessment,
    String plan
) {}

@JsonIgnoreProperties(ignoreUnknown = true)
public record PrescriptionAiDto(
    String       drugName,
    String       dose,
    String       route,
    String       frequencyString,
    Integer      frequencyHours,        // nullable — omitted when null
    Integer      totalDoses,            // nullable — omitted when null
    List<String> administrationTimes
) {}
```

---

## Error Responses

| Status / Event | Condition |
|---|---|
| HTTP `503` | Models are still loading. Returned before stream starts. `careround-core` should retry after a delay. |
| SSE `error` event | A stage failed after the stream started. `careround-core` should surface a generic AI error to the doctor. |

HTTP error bodies follow FastAPI's default shape:
```json
{ "detail": "human-readable message" }
```

---

## Connecting from `careround-core`

### Base URL

| Environment | Value |
|---|---|
| Local dev | `http://localhost:8000` |
| Production | `http://<careround-ai-private-ip>:8000` |

Configure as `AI_SERVICE_URL` in the core service. Do not hard-code the IP.

### Readiness check before forwarding

Before forwarding any `POST /process-voice-note` request, `careround-core` must verify the AI service is ready:

```
GET {AI_SERVICE_URL}/health
→ 200 { "status": "ready" }   → proceed
→ 200 { "status": "loading" } → return 503 to client
→ connection error             → return 503 to client
```

On deployment, poll `/health` every 5 seconds for up to 5 minutes. Roll back if `ready` is never reached.

### Timeout and retry guidance

| Concern | Recommendation |
|---|---|
| Connect timeout | 5 seconds |
| Read timeout | 300 seconds — stream stays open for the full pipeline duration |
| Retry on HTTP 503 | Yes — exponential backoff, max 3 retries, only while models are loading |
| Retry on SSE `error` | No — LLM/transcription failures are not transient |

### Security

- The AI service must not be reachable from the internet. Place it in a private subnet with no inbound rule from `0.0.0.0/0`.
- `careround-core` reaches it over the VPC private network only.
- Do not log `rawTranscription`, `clinicalNote` content, or prescription text. Log `patient_id`, mode, response latency, and prescription count only.
