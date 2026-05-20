# CareRound AI Service Development Notes

This document extracts the parts of `CareRound-Specification(2).md` that directly affect development of the `careround-ai` service. It is intended to guide implementation decisions in this repository.

## Service Role

`careround-ai` is a separate Python/FastAPI service for AI-powered clinical voice documentation. It is called synchronously by `careround-core` over REST and returns draft structured data for human review. It does not save clinical records, create prescriptions, publish Kafka events, or own business workflow state.

The service exists separately from `careround-core` because it has a different runtime, hardware profile, deployment cadence, and model lifecycle. The core system remains responsible for authentication, tenancy, persistence, clinical confirmation, medication chart creation, task generation, audit, and notifications.

## Primary Responsibilities

- Accept a ward-round audio recording from `careround-core`.
- Transcribe the full recording using Whisper or `faster-whisper`.
- Structure the transcription into a SOAP clinical note.
- Extract prescriptions with drug, dose, route, frequency, and dose count.
- Calculate medication administration times deterministically in Python.
- Return raw transcription, SOAP note, and extracted prescriptions for doctor review.
- Report readiness through `/health` only after AI models have loaded and warmed up.

## Non-Responsibilities

- Do not save notes or prescriptions to a database.
- Do not generate medication charts or medication tasks.
- Do not publish Kafka events.
- Do not decide whether a note or prescription is clinically valid.
- Do not bypass doctor review. All AI output is draft output.
- Do not send audio, transcription, or prescription text to third-party hosted AI APIs.

## Runtime And Deployment

Target runtime:

- Python 3.11
- FastAPI
- Port `8000`
- Single instance for MVP
- CPU demo mode, GPU production mode

Deployment target:

- Private EC2 instance, not internet-facing.
- `careround-core` reaches it through `AI_SERVICE_URL=http://<careround-ai-private-ip>:8000`.
- Deployed independently from the Spring Boot services.
- Production/demo deployment uses SSH, Docker image pull, container restart, and a health-check wait.
- Deploy flow should wait up to 5 minutes for `/health` to return `ready`, then roll back if readiness fails.

Local/dev infrastructure expects Ollama on port `11434`.

## Model Strategy

The product specification requires self-hosted models because clinical notes and prescriptions contain protected health information. Audio, raw transcription, and prescription text must remain inside the hospital-controlled cloud environment.

Planned provider modes:

| Capability | Demo/CPU | Production/GPU |
| --- | --- | --- |
| Speech to text | `faster-whisper` with small/base English model | `faster-whisper` with `large-v3` |
| LLM | Ollama with `mistral:7b` or `llama3.2:3b` | vLLM serving BioMistral-7B |
| Provider switch | `AI_PROVIDER=ollama` | `AI_PROVIDER=vllm` |

Current repository state:

- `requirements.txt` includes `fastapi`, `python-multipart`, `faster-whisper`, `ollama`, `pydantic`, `pydantic-settings`, `python-dotenv`, and `httpx`.
- `app/config.py` exposes `AI_PROVIDER`, `OLLAMA_HOST`, `WHISPER_MODEL`, and `LLM_MODEL`.
- `app/services/whisper_service.py` currently loads `faster-whisper` on CPU.
- `app/services/llm_service.py` currently uses Ollama with JSON output and temperature `0.1`.
- `AI_PROVIDER=stub` bypasses Ollama and returns predictable structured LLM output for local API/UI testing.
- Provider abstractions exist under `app/providers/`, but only the Ollama path appears wired right now.

## API Contract

The specification names the AI service endpoint as:

```http
POST /process-voice-note
Content-Type: multipart/form-data
```

Fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `audio` | file | Audio recording in any format Whisper supports |
| `patient_id` | string | Used for patient context in prompting/logging |
| `current_time` | ISO datetime string | Base time for deterministic administration-time calculation |
| `mode` | string | `ward_round` by default; use `transcription_only` for handover notes and other free-text notes |

Expected response:

```json
{
  "rawTranscription": "Patient is a 54 year old male...",
  "mode": "ward_round",
  "clinicalNote": {
    "subjective": "...",
    "objective": "...",
    "assessment": "...",
    "plan": "..."
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

For handover notes, nurse notes, and other non-ward-round recordings, use the same endpoint with `mode=transcription_only`. In that mode, the service should transcribe the audio and return only the raw transcription plus the mode:

```json
{
  "rawTranscription": "Nurse handover note text...",
  "mode": "transcription_only",
  "prescriptions": []
}
```

Current repository note:

- `main.py` mounts the voice router without a prefix, so the implemented route is `/process-voice-note`.
- `careround-core` should continue exposing `/api/v1/ai/process-voice-note` to clients and proxy internally to `careround-ai` at `/process-voice-note`.

## Health Contract

Endpoint:

```http
GET /health
```

Specification-level response:

```json
{ "status": "loading" }
```

or:

```json
{ "status": "ready" }
```

Current repository response is more detailed:

```json
{
  "status": "ready",
  "whisperLoaded": true,
  "llmLoaded": true
}
```

This is compatible with the core requirement as long as `status` remains present. `ready` must only be returned after both transcription and LLM models have completed warmup inference. `careround-core` polls this endpoint before forwarding AI requests and should return 503 while the AI service is loading.

## Processing Pipeline

The service pipeline should be sequential:

1. Receive multipart audio and form fields.
2. Reject with 503 if models are not ready.
3. Read the full audio file.
4. Transcribe using `faster-whisper`.
5. Send raw transcription to the medical LLM.
6. Enforce valid JSON output from the LLM.
7. Parse SOAP note and extracted prescriptions.
8. Calculate `administrationTimes` in Python from `current_time`, `frequencyHours`, and `totalDoses`.
9. Return the complete structured response to `careround-core`.

The AI boundary is important: use the LLM for language understanding, but use Python `datetime` arithmetic for administration times. The specification explicitly avoids LLM-based date/time calculation because it can hallucinate or make arithmetic mistakes.

If `mode=transcription_only`, skip the LLM structuring and prescription extraction steps. Return only `rawTranscription`, `mode`, and an empty `prescriptions` list.

If the LLM extracts a prescription but leaves `frequencyHours` or `totalDoses` missing, keep the extracted prescription in the response but do not calculate `administrationTimes`. The review UI should show the incomplete prescription for human correction rather than dropping it.

## Long Recordings

Doctors record the full consultation in one take, including pauses and silent examination gaps. The specification relies on `faster-whisper` to process the full audio file, detect speech segments and silence, and return one complete transcription. No custom silence-splitting behavior is required for MVP.

## Prompting Requirements

The medical system prompt must instruct the LLM to:

- Act as a clinical documentation assistant.
- Receive a doctor's dictated ward-round note.
- Extract a SOAP note.
- Extract any prescriptions.
- Return only valid JSON.
- Avoid explanations, markdown, or code fences.

Required LLM output shape before post-processing:

```json
{
  "soap": {
    "subjective": "",
    "objective": "",
    "assessment": "",
    "plan": ""
  },
  "prescriptions": [
    {
      "drugName": "",
      "dose": "",
      "route": "",
      "frequencyString": "",
      "frequencyHours": 0,
      "totalDoses": 0
    }
  ]
}
```

Temperature should be `0.1` for consistent clinical output.

## Integration With `careround-core`

Core-facing flow:

1. Doctor stops a recording in the web/mobile app.
2. Client sends audio to `POST /api/v1/ai/process-voice-note` on `careround-core`.
3. `careround-core` checks AI readiness and proxies multipart audio to `careround-ai`.
4. `careround-ai` returns raw transcription, SOAP note, and prescriptions.
5. Doctor reviews and edits the output.
6. Doctor confirms via `POST /api/v1/clinical-notes/confirm`.
7. `careround-core` saves `ClinicalNote` and `Prescription` records.
8. The prescription-to-chart-to-task chain continues asynchronously through outbox and Kafka.

`careround-ai` should keep its response contract stable because the doctor review UI and `AiServiceClient` depend on it.

## Security And Privacy Requirements

- Keep the AI service private to the VPC/private subnet.
- Do not call third-party hosted AI APIs with patient data.
- Do not persist raw audio by default.
- Avoid logging raw transcription, prescription text, or full clinical notes.
- Use correlation IDs from upstream requests once the core client sends them.
- Treat `patient_id` as sensitive metadata.
- Return draft clinical output only; final clinical responsibility remains with human review and confirmation.

## Observability

The broad platform uses structured JSON logs with `correlationId`, `hospitalId`, and `userId`. The AI service should align with this where possible.

Suggested service-specific metrics/logging decisions:

- Model load duration.
- Health status transitions.
- Request count and latency for `/process-voice-note`.
- Transcription duration.
- LLM duration.
- LLM JSON parse failures.
- Prescription extraction count.
- 503 count while models are loading.

Avoid logging PHI-rich payloads. Prefer IDs, counts, timings, and error categories.

## Implementation Tasks Remaining

- Replace the current stub response in `app/routes/process_voice_note.py` with the full pipeline.
- In `mode=transcription_only`, run transcription only and skip SOAP/prescription extraction.
- Add administration-time calculation using `current_time`, `frequencyHours`, and `totalDoses`.
- Validate LLM output against Pydantic schemas before returning.
- Add robust handling for malformed or incomplete LLM JSON.
- Confirm `OLLAMA_HOST` is actually passed to the Ollama client, not just read from settings.
- Decide model defaults: the spec suggests `WHISPER_MODEL=base.en` and `LLM_MODEL=mistral:7b`, while the current `.env` and config may use shorter names.
- Fill in or defer the `vllm_provider.py` production path.
- Complete `Dockerfile`.
- Add request/response tests for the endpoint and unit tests for administration-time calculation.
- Add at least one test fixture for LLM JSON parsing and prescription mapping.

## Open Decisions

- What audio formats will the mobile and web clients actually send in MVP?
- How much patient context should `patient_id` unlock in prompts, given this service currently has no database access?
- Should raw transcription be returned for all roles, or only for doctor review flows?
