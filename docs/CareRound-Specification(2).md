# CareRound — Technical Specification & Implementation Plan

---

## Table of Contents

1. [What CareRound Is](#1-what-careround-is)
2. [The Problem It Solves](#2-the-problem-it-solves)
3. [Primary Features](#3-primary-features)
4. [Architecture Overview](#4-architecture-overview)
5. [Repository Structure](#5-repository-structure)
6. [Service Specifications](#6-service-specifications)
   - 6.1 careround-core
   - 6.2 careround-ai
   - 6.3 careround-notification
   - 6.4 careround-audit
7. [Data Models & Database Schema](#7-data-models--database-schema)
8. [Business Logic & Rules](#8-business-logic--rules)
9. [Kafka Event Catalogue](#9-kafka-event-catalogue)
10. [API Endpoints](#10-api-endpoints)
11. [Multi-Tenancy Design](#11-multi-tenancy-design)
12. [Authentication & Security](#12-authentication--security)
13. [AI Pipeline](#13-ai-pipeline)
14. [UI/UX Specification](#14-uiux-specification)
15. [Infrastructure & Deployment](#15-infrastructure--deployment)
16. [AWS Setup & Bootstrap](#16-aws-setup--bootstrap)
17. [CI/CD Pipeline](#17-cicd-pipeline)
18. [Observability](#18-observability)
19. [4-Day Implementation Plan](#19-4-day-implementation-plan)

---

## 1. What CareRound Is

CareRound is a multi-tenant ward management platform for hospitals. It solves the coordination and documentation gap that existing EMR (Electronic Medical Record) systems do not address. Where an EMR stores what happened to a patient over their lifetime, CareRound handles the real-time operational layer of a ward shift — ensuring clinical documentation is captured efficiently, medication tasks are generated automatically and tracked, and supervisors have live visibility of ward operations.

The platform is built around two core innovations:

1. **AI-powered voice documentation** — doctors dictate ward round notes verbally, and the system transcribes, structures, and extracts prescriptions automatically.
2. **Automated medication task generation** — once a prescription is confirmed, the complete medication schedule is created automatically and delivered as a to-do list to the assigned nurse.

---

## 2. The Problem It Solves

Ward coordination in hospitals runs on verbal instructions, paper notes, and manual checking. Specifically:

**For doctors:** Documentation during a ward round is time-consuming. Writing structured clinical notes for every patient consumes time that should go toward patient care. Most doctors spend 30–40% of their ward round time writing rather than examining.

**For nurses:** Medication charts are checked manually. A nurse must remember or manually scan a paper/screen chart to know what each patient needs and when. There is no automated reminder if a window is missed.

**For supervisors:** There is no real-time visibility of task completion across the ward. A charge nurse or ward manager has no systemic way to see that a medication task is overdue until something goes wrong.

**Documented evidence of the problem:**
- 60% of US hospital adverse events involve a communication or coordination failure (Joint Commission)
- 70% of deaths from medical error are linked to breakdown at shift handover (AHRQ)
- 15% of total hospital expenditure is a direct result of preventable adverse events (OECD)

---

## 3. Primary Features

### AI Voice Documentation (Doctor)
- Doctor records audio during a ward round consultation
- Single request to the AI service: transcribes audio, structures it as a SOAP-format clinical note, extracts any prescriptions with drug, dose, route, frequency, and administration times
- Doctor reviews and edits the AI output before confirming
- Long recordings with silent gaps supported — the full consultation is recorded once

### Medication Task Automation (Nurse)
- When a doctor confirms a prescription, a medication chart entry is created automatically
- One medication task per scheduled administration time is generated from the chart
- Nurse sees a sorted task list: overdue (red, at the top), due soon (amber), upcoming (by time)
- One-tap task completion
- Push notification reminder if a task is not completed within a configurable window after its scheduled time

### Live Supervisor Dashboard
- Task completion rate, overdue task count, patient acuity distribution
- Per-patient view showing active medications and today's task completion
- Polls every 10 seconds — no manual refresh needed
- Overdue tasks surfaced prominently with patient name, bed, drug, and minutes overdue

### Core EMR Functions
- Patient registration and admission
- Vitals recording with automatic acuity colour computation (GREEN / AMBER / RED)
- Full patient record: notes timeline, medication chart, vitals history
- Patient discharge
- Optional nurse handover notes per patient

### Multi-Hospital Platform
- Each hospital onboards independently with a short hospital code
- All data is fully isolated per hospital
- Hospital-specific configuration for acuity thresholds and task overdue windows

---

## 4. Architecture Overview

CareRound is a **hybrid architecture**: a modular monolith for the clinical core with two lightweight event-driven satellite services and one dedicated AI service.

```
Client (Web + Mobile)
        │ HTTPS
        ▼
careround-core  (Spring Boot 3, port 8080)
  ├── Auth Module
  ├── Hospital Module
  ├── Patient Module
  └── Scheduler Module (Quartz JDBC)
        │
        │ produces events via Transactional Outbox
        ▼
    Apache Kafka
        │
        ├──→ careround-notification  (Spring Boot, port 8081)
        │       Kafka consumer: medication-task-overdue
        │       Sends FCM push notifications to nurses
        │
        └──→ careround-audit  (Spring Boot, port 8082)
                Kafka consumer: all 6 topics
                Append-only audit log

careround-ai  (Python/FastAPI, port 8000)
  Called synchronously by careround-core via REST
  Runs Whisper (speech-to-text) + medical LLM (note structuring)
  Self-hosted on cloud GPU instance
```

**Why this shape:**
- careround-core is a monolith because clinical workflows (prescriptions, charts, tasks, notes) are deeply coupled and require transactional integrity across entities
- careround-ai is separate because it runs a different runtime (Python), different hardware requirements (GPU), and has no business logic coupling to the core
- careround-notification and careround-audit are separate because they are pure event consumers with different operational concerns (external API calls, append-only storage)

---

## 5. Repository Structure

### Monorepo (existing Spring Boot services)
```
careround/                         ← existing monorepo
├── pom.xml                        ← parent POM, dependency management
├── docker-compose.yml             ← local dev infrastructure only
├── docker-compose.full.yml        ← full stack including apps (demo day)
├── .env                           ← secrets (never committed)
├── .env.example                   ← template (committed)
├── infrastructure/                ← Terraform
│   ├── main.tf
│   ├── variables.tf
│   ├── vpc.tf
│   ├── security_groups.tf
│   ├── rds.tf
│   ├── elasticache.tf
│   ├── kafka.tf
│   ├── alb.tf
│   ├── asg.tf
│   ├── iam.tf
│   ├── monitoring.tf
│   └── scripts/
│       └── core-userdata.sh
├── load-tests/
│   └── careround-load-test.js
├── careround-core/
├── careround-notification/
└── careround-audit/
```

### Separate Repo (new AI service)
```
careround-ai/                      ← separate repository
├── Dockerfile
├── requirements.txt
├── main.py                        ← FastAPI entry point
├── .env
├── .env.example
└── app/
    ├── config.py                  ← AI_PROVIDER env switch
    ├── routes/
    │   └── process_voice_note.py  ← single endpoint
    ├── services/
    │   ├── whisper_service.py
    │   └── llm_service.py
    ├── providers/
    │   ├── base.py
    │   ├── ollama_provider.py     ← dev / demo
    │   └── vllm_provider.py       ← production GPU
    ├── models/
    │   └── schemas.py             ← Pydantic request/response models
    └── prompts/
        └── medical_system_prompt.txt
```

### careround-core Internal Structure
```
careround-core/src/main/java/com/careround/
├── CareRoundApplication.java
│
├── auth/
│   ├── entity/
│   │   ├── User.java
│   │   └── RefreshToken.java
│   ├── enums/UserRole.java        ← ADMIN, DOCTOR, NURSE, SUPERVISOR
│   ├── repository/
│   │   ├── UserRepository.java
│   │   └── RefreshTokenRepository.java
│   ├── service/
│   │   ├── AuthService.java
│   │   ├── UserService.java
│   │   └── JwtService.java
│   ├── controller/
│   │   ├── AuthController.java
│   │   └── UserController.java
│   └── dto/
│       ├── request/LoginRequest.java
│       ├── request/CreateUserRequest.java
│       └── response/JwtResponse.java, UserResponse.java
│
├── hospital/
│   ├── hospital/
│   │   ├── entity/Hospital.java
│   │   ├── entity/SystemConfiguration.java
│   │   ├── repository/HospitalRepository.java
│   │   ├── repository/SystemConfigurationRepository.java
│   │   ├── service/HospitalService.java
│   │   ├── service/SystemConfigurationService.java
│   │   ├── controller/HospitalController.java
│   │   └── dto/...
│   └── ward/
│       ├── entity/Ward.java
│       ├── repository/WardRepository.java
│       ├── service/WardService.java
│       ├── controller/WardController.java
│       └── dto/...
│
├── patient/
│   ├── patient/
│   │   ├── entity/Patient.java
│   │   ├── repository/PatientRepository.java
│   │   ├── service/PatientService.java
│   │   ├── controller/PatientController.java
│   │   └── dto/...
│   ├── vitals/
│   │   ├── entity/PatientVitals.java
│   │   ├── repository/PatientVitalsRepository.java
│   │   ├── service/PatientVitalsService.java
│   │   ├── controller/PatientVitalsController.java
│   │   └── dto/...
│   ├── clinicalnote/
│   │   ├── entity/ClinicalNote.java
│   │   ├── repository/ClinicalNoteRepository.java
│   │   ├── service/ClinicalNoteService.java
│   │   ├── controller/ClinicalNoteController.java
│   │   └── dto/...
│   ├── prescription/
│   │   ├── entity/Prescription.java
│   │   ├── repository/PrescriptionRepository.java
│   │   ├── service/PrescriptionService.java
│   │   ├── controller/PrescriptionController.java
│   │   └── dto/...
│   ├── medicationchart/
│   │   ├── entity/MedicationChart.java
│   │   ├── repository/MedicationChartRepository.java
│   │   ├── service/MedicationChartService.java
│   │   ├── controller/MedicationChartController.java
│   │   └── dto/...
│   ├── medicationtask/
│   │   ├── entity/MedicationTask.java
│   │   ├── repository/MedicationTaskRepository.java
│   │   ├── service/MedicationTaskService.java
│   │   ├── controller/MedicationTaskController.java
│   │   └── dto/...
│   └── handovernote/
│       ├── entity/HandoverNote.java
│       ├── repository/HandoverNoteRepository.java
│       ├── service/HandoverNoteService.java
│       ├── controller/HandoverNoteController.java
│       └── dto/...
│
├── ai/
│   ├── client/AiServiceClient.java    ← RestClient to careround-ai
│   └── dto/
│       ├── ProcessVoiceNoteRequest.java
│       └── ProcessVoiceNoteResponse.java
│
├── scheduler/
│   ├── config/QuartzConfig.java
│   └── jobs/
│       ├── OutboxPollerJob.java
│       ├── MedicationTaskOverdueJob.java
│       ├── OutboxCleanupJob.java
│       └── RefreshTokenCleanupJob.java
│
├── kafka/
│   ├── consumer/
│   │   ├── PrescriptionConfirmedConsumer.java
│   │   └── MedicationChartCreatedConsumer.java
│   └── config/KafkaConsumerConfig.java
│
└── shared/
    ├── config/
    │   ├── SecurityConfig.java
    │   ├── KafkaProducerConfig.java
    │   ├── KafkaTopicConfig.java
    │   └── RedisConfig.java
    ├── entity/BaseEntity.java
    ├── event/
    │   ├── OutboxEvent.java
    │   ├── OutboxEventRepository.java
    │   └── events/          ← all 6 Kafka event POJOs
    ├── exception/
    │   ├── GlobalExceptionHandler.java
    │   ├── ResourceNotFoundException.java
    │   ├── AccessDeniedException.java
    │   └── BusinessRuleException.java
    ├── security/
    │   ├── JwtAuthFilter.java
    │   └── HospitalContextHolder.java
    ├── service/OutboxService.java
    ├── filter/CorrelationIdFilter.java
    └── dto/ApiResponse.java
```

---

## 6. Service Specifications

### 6.1 careround-core

**Runtime:** Java 21, Spring Boot 3.3.5  
**Database:** MySQL 8.0, schema `careround_core`  
**Port:** 8080  
**Scales:** Horizontally via AWS Auto Scaling Group (2–5 instances)

**Responsibilities:**
- All clinical and administrative domain logic
- JWT issuance and validation
- REST API for web and mobile clients
- AI proxy endpoints (forwards audio to careround-ai, returns structured results)
- Transactional Outbox pattern (writes domain events to `outbox_event` table)
- Internal Kafka consumers for the prescription → chart → task async chain
- Quartz scheduler (4 jobs, JDBC clustered mode)

**Key dependencies:**
```xml
spring-boot-starter-web
spring-boot-starter-data-jpa
spring-boot-starter-security
spring-boot-starter-data-redis
spring-boot-starter-actuator
spring-boot-starter-validation
spring-boot-starter-quartz
spring-kafka
flyway-core + flyway-mysql
mysql-connector-j
jjwt-api:0.12.5
micrometer-registry-prometheus
logstash-logback-encoder:7.4
lombok
<!-- Jackson ObjectMapper is included transitively via spring-boot-starter-web -->
<!-- No MapStruct — DTO mapping is done manually in service layer mapper methods -->
```

**DTO Mapping Approach:** Jackson's `ObjectMapper` (already on the classpath via Spring Web) handles JSON serialisation for Kafka payloads and API responses. Entity-to-DTO mapping is done with explicit manual mapper methods inside each service class or a dedicated `*Mapper` helper class per domain package. No annotation processor setup required. Example:

```java
private PatientResponse toResponse(Patient patient) {
    return new PatientResponse(
        patient.getId(),
        patient.getHospitalId(),
        patient.getFirstName() + " " + patient.getLastName(),
        patient.getBedNumber(),
        patient.getAcuityColor(),
        patient.getStatus(),
        patient.getPrimaryDiagnosis(),
        patient.getAdmissionDate()
    );
}
```

**Quartz Jobs:**

| Job | Schedule | Purpose |
|-----|----------|---------|
| OutboxPollerJob | Every 2 seconds | Reads pending outbox_event rows, publishes to Kafka |
| MedicationTaskReminderJob | Every 1 minute | Fires pre-task reminders (5 min before) and overdue alerts (5 min after) |
| OutboxCleanupJob | Daily 3am | Deletes published outbox events older than 7 days |
| RefreshTokenCleanupJob | Daily 2am | Deletes revoked/expired refresh tokens older than 7 days |

**MedicationTaskReminderJob — Two-Pass Logic:**

The job runs every minute and performs two independent passes:

**Pass 1 — Pre-task reminder (5 minutes before scheduled time):**
```
Find tasks where:
  status = PENDING
  AND scheduled_time BETWEEN now() + 4min AND now() + 6min
  AND pre_reminder_sent_at IS NULL
→ Publish medication-task-reminder event
→ Set pre_reminder_sent_at = now()
```
Notification to nurse: *"[Drug] [dose] for [patient name] — Bed [N] is due in 5 minutes."*

**Pass 2 — Overdue alert (5 minutes after scheduled time):**
```
Find tasks where:
  status = PENDING
  AND scheduled_time < now() - 5min
  AND overdue_alert_sent_at IS NULL
→ Mark status = OVERDUE
→ Publish medication-task-overdue event
→ Set overdue_alert_sent_at = now()
```
Notification to nurse: *"[Drug] [dose] for [patient name] — Bed [N] is now 5 minutes overdue."*

The 5-minute windows for both passes are fixed in application logic. The `system_configuration` table retains `task_overdue_reminder_minutes` only as a legacy field and is unused — both windows are hardcoded at 5 minutes. This removes configuration complexity with no clinical trade-off for the MVP.

**Quartz Configuration (application.yml):**
```yaml
spring:
  quartz:
    job-store-type: jdbc
    jdbc:
      initialize-schema: never     # Flyway handles QRTZ_* tables
    properties:
      org.quartz.jobStore.isClustered: true
      org.quartz.jobStore.clusterCheckinInterval: 10000
      org.quartz.jobStore.driverDelegateClass: >
        org.quartz.impl.jdbcjobstore.StdJDBCDelegate
      org.quartz.threadPool.threadCount: 5
```

### 6.2 careround-ai

**Runtime:** Python 3.11, FastAPI  
**Port:** 8000  
**Scales:** Single instance (GPU EC2 in production, CPU EC2 for demo)  
**Repository:** Separate repo (`careround-ai`)

**Responsibilities:**
- Speech-to-text transcription via Whisper
- SOAP note structuring and prescription extraction via medical LLM
- Administration time calculation (pure Python, not AI)
- Returns structured data for doctor review before anything is saved

**AI Models:**

| Component | Demo (CPU) | Production (GPU) |
|-----------|-----------|-----------------|
| Speech-to-text | faster-whisper (base.en) via Ollama | faster-whisper (large-v3) |
| LLM | Ollama (mistral:7b or llama3.2:3b) | vLLM serving BioMistral-7B |
| Provider switch | `AI_PROVIDER=ollama` | `AI_PROVIDER=vllm` |

**Why self-hosted (not Groq/OpenAI):** Clinical notes and prescriptions are protected health information. Sending audio or text to a third-party API violates HIPAA, GDPR, and NDPR. Self-hosting keeps all patient data within the hospital's infrastructure. No audio file, no transcription, and no prescription text ever leaves the cloud instance.

**Single Endpoint:**
```
POST /process-voice-note
Content-Type: multipart/form-data

Fields:
  audio          (file)     — audio recording in any format Whisper supports
  patient_id     (string)   — used for context in the prompt
  current_time   (string)   — ISO datetime, used to calculate administration times
```

**Response:**
```json
{
  "rawTranscription": "Patient is a 54 year old male...",
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

**Health Endpoint:**
```
GET /health
Response: { "status": "loading" } | { "status": "ready" }
```
Returns "ready" only after both models have successfully completed a warmup inference. careround-core checks this before routing AI requests and returns 503 if the AI service is still initialising.

**LLM System Prompt (abbreviated):**
```
You are a clinical documentation assistant.
You will receive a doctor's dictated ward round note.
Extract the SOAP note and any prescriptions.
Return ONLY valid JSON. No explanation. No markdown. No code fences.
Schema: { "soap": { "subjective": "", "objective": "", "assessment": "", "plan": "" },
          "prescriptions": [{ "drugName": "", "dose": "", "route": "",
                              "frequencyString": "", "frequencyHours": 0, "totalDoses": 0 }] }
```

Temperature: 0.1 (low, for consistent clinical output)

**Cloud Instance Setup:**
- EC2 instance provisioned via Terraform as part of initial infrastructure setup
- Instance type: `g4dn.xlarge` for demo (T4 GPU, 16GB VRAM, ~$0.526/hr spot)
- Model weights stored in a Docker volume — survive container restarts
- First startup downloads models; subsequent starts load from volume

### 6.3 careround-notification

**Runtime:** Java 21, Spring Boot 3.3.5  
**Database:** MySQL 8.0, schema `careround_notification`  
**Port:** 8081  
**Scales:** Single instance (load is very low — one push per overdue task)

**Responsibilities:**
- Kafka consumer on `medication-task-reminder` topic — sends 5-minute pre-task push notification to nurse
- Kafka consumer on `medication-task-overdue` topic — sends 5-minute post-task overdue push notification to nurse
- Persists failed notifications to `failed_notifications` table (DLT handling)

**Kafka Consumer Groups:**
- `careround-notification` — primary consumer on both topics
- Dead letter topics: `medication-task-reminder.DLT`, `medication-task-overdue.DLT`

**Two consumers, two notification types:**

`MedicationTaskReminderConsumer` — listens to `medication-task-reminder`:
- Notification title: "Medication Due Soon"
- Body: "[Drug] [dose] for [patient name] — Bed [N] is due in 5 minutes."

`MedicationTaskOverdueConsumer` — listens to `medication-task-overdue`:
- Notification title: "Medication Overdue"
- Body: "[Drug] [dose] for [patient name] — Bed [N] is 5 minutes overdue."

**Error handling:** `DefaultErrorHandler` with `ExponentialBackOffWithMaxRetries(3)` at 1s, 2s, 4s backoff. After 3 failures, `DeadLetterPublishingRecoverer` routes to DLT. `NotificationDltConsumer` writes failed messages to `failed_notifications`.

**FCM Integration:**
```java
// FirebaseMessaging.getInstance().send(
//   Message.builder()
//     .setToken(deviceToken)
//     .setNotification(Notification.builder()
//       .setTitle("Medication Overdue")
//       .setBody("Amoxicillin 500mg for Bed 4 — 22 min overdue")
//       .build())
//     .putData("taskId", taskId)
//     .putData("patientId", patientId)
//     .build()
// );
```

### 6.4 careround-audit

**Runtime:** Java 21, Spring Boot 3.3.5  
**Database:** MySQL 8.0, schema `careround_audit`  
**Port:** 8082  
**Scales:** Single instance

**Responsibilities:**
- Kafka consumer on all 11 topics
- Writes every event as an immutable row to `audit_log`
- No updates or deletes ever occur in this schema

**Why it exists:** Healthcare regulations require a verifiable, tamper-proof record of all clinical actions. The append-only schema, separated from the core database, ensures it is architecturally impossible for the core application to modify audit records.

**Idempotency:** Before writing, checks `existsByEventId(event.getId())` to prevent duplicate entries on Kafka redelivery.

---

## 7. Data Models & Database Schema

### careround_core Schema

#### `hospital`
```sql
CREATE TABLE hospital (
    id            VARCHAR(36)  NOT NULL PRIMARY KEY,
    name          VARCHAR(255) NOT NULL,
    code          VARCHAR(20)  NOT NULL UNIQUE,  -- e.g. "STMARYS", "LGH"
    address       TEXT,
    contact_email VARCHAR(255) NOT NULL UNIQUE,
    contact_phone VARCHAR(50),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    DATETIME     NOT NULL,
    updated_at    DATETIME     NOT NULL
);
CREATE UNIQUE INDEX idx_hospital_code ON hospital(code);
```

#### `system_configuration`
```sql
CREATE TABLE system_configuration (
    id                           VARCHAR(36) NOT NULL PRIMARY KEY,
    hospital_id                  VARCHAR(36) NOT NULL UNIQUE,
    task_overdue_reminder_minutes INT        NOT NULL DEFAULT 10,
    task_escalation_minutes      INT         NOT NULL DEFAULT 20,
    push_notifications_enabled   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at                   DATETIME    NOT NULL,
    updated_at                   DATETIME    NOT NULL
);
```
Note: Acuity thresholds are not configurable. The Vitals Health Index uses fixed clinical scoring thresholds (see Section 8). Only task overdue windows and notification preferences are hospital-configurable.

#### `users`
```sql
CREATE TABLE users (
    id            VARCHAR(36)  NOT NULL PRIMARY KEY,
    hospital_id   VARCHAR(36)  NOT NULL,
    first_name    VARCHAR(100) NOT NULL,
    last_name     VARCHAR(100) NOT NULL,
    email         VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('ADMIN','DOCTOR','NURSE','SUPERVISOR') NOT NULL,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    fcm_token     VARCHAR(512),             -- stored here for notification targeting
    created_at    DATETIME     NOT NULL,
    updated_at    DATETIME     NOT NULL,
    UNIQUE KEY uk_users_hospital_email (hospital_id, email)
);
CREATE INDEX idx_users_hospital_id ON users(hospital_id);
```

#### `refresh_tokens`
```sql
CREATE TABLE refresh_tokens (
    id          VARCHAR(36)  NOT NULL PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL,
    hospital_id VARCHAR(36)  NOT NULL,
    token       VARCHAR(512) NOT NULL UNIQUE,
    expires_at  DATETIME     NOT NULL,
    revoked     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  DATETIME     NOT NULL,
    updated_at  DATETIME     NOT NULL
);
CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens(user_id);
```

#### `ward`
```sql
CREATE TABLE ward (
    id          VARCHAR(36)  NOT NULL PRIMARY KEY,
    hospital_id VARCHAR(36)  NOT NULL,
    name        VARCHAR(255) NOT NULL,
    specialty   VARCHAR(100),
    total_beds  INT          NOT NULL DEFAULT 0,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  DATETIME     NOT NULL,
    updated_at  DATETIME     NOT NULL
);
CREATE INDEX idx_ward_hospital_id ON ward(hospital_id);
```

#### `patient`
```sql
CREATE TABLE patient (
    id                      VARCHAR(36)  NOT NULL PRIMARY KEY,
    hospital_id             VARCHAR(36)  NOT NULL,
    ward_id                 VARCHAR(36)  NOT NULL,
    bed_number              VARCHAR(20),
    first_name              VARCHAR(100) NOT NULL,
    last_name               VARCHAR(100) NOT NULL,
    date_of_birth           DATE         NOT NULL,
    gender                  ENUM('MALE','FEMALE','OTHER') NOT NULL,
    hospital_number         VARCHAR(50)  NOT NULL UNIQUE,
    phone_number            VARCHAR(50),
    address                 TEXT,
    previous_conditions     TEXT,        -- free text: e.g. "Hypertension, Type 2 Diabetes"
    current_medications     TEXT,        -- pre-admission medications, free text
    allergies               TEXT,        -- free text
    emergency_contact_name  VARCHAR(255),
    emergency_contact_phone VARCHAR(50),
    admission_date          DATETIME     NOT NULL,
    admission_type          ENUM('EMERGENCY','ELECTIVE','TRANSFER') NOT NULL,
    primary_diagnosis       TEXT,
    acuity_color            ENUM('GREEN','AMBER','RED') NOT NULL DEFAULT 'GREEN',
    status                  ENUM('ADMITTED','DISCHARGED') NOT NULL DEFAULT 'ADMITTED',
    estimated_discharge_date DATE,
    registered_by_id        VARCHAR(36)  NOT NULL,   -- always an ADMIN user
    created_at              DATETIME     NOT NULL,
    updated_at              DATETIME     NOT NULL
);
CREATE INDEX idx_patient_hospital_ward ON patient(hospital_id, ward_id);
CREATE INDEX idx_patient_hospital_acuity ON patient(hospital_id, ward_id, acuity_color);
```

#### `patient_vitals`
```sql
CREATE TABLE patient_vitals (
    id                VARCHAR(36)  NOT NULL PRIMARY KEY,
    patient_id        VARCHAR(36)  NOT NULL,
    hospital_id       VARCHAR(36)  NOT NULL,
    recorded_by_id    VARCHAR(36)  NOT NULL,     -- always a Nurse
    pulse             INT,                        -- bpm
    systolic_bp       INT,                        -- mmHg (used in VHI scoring)
    diastolic_bp      INT,                        -- mmHg (stored, not scored)
    respiratory_rate  INT,                        -- breaths/min
    temperature       DECIMAL(4,1),               -- °C
    spo2              DECIMAL(5,2),               -- % oxygen saturation
    vhi_score         INT          NOT NULL,      -- Vitals Health Index (0–15)
    vhi_status        ENUM('STABLE','WATCH','CRITICAL') NOT NULL,
    recorded_at       DATETIME     NOT NULL,
    created_at        DATETIME     NOT NULL,
    updated_at        DATETIME     NOT NULL
);
CREATE INDEX idx_vitals_patient_time ON patient_vitals(patient_id, recorded_at DESC);
```

**Vitals Health Index (VHI) Scoring Logic:**

Computed server-side in `PatientVitalsService` when vitals are saved. Five of the six inputs are scored — diastolic BP is stored in the database for clinical reference but is not included in the acute deterioration score.

| Input | 0 points (Normal) | 1 point (Mild) | 2 points (Moderate) | 3 points (Severe) |
|-------|-------------------|----------------|---------------------|-------------------|
| **Pulse** | 61–100 bpm | 51–60 or 101–110 | 41–50 or 111–129 | ≤ 40 or ≥ 130 |
| **Systolic BP** | 101–159 mmHg | 91–100 or 160–199 | 81–90 or ≥ 200 | ≤ 80 |
| **Respiratory Rate** | 9–14 breaths/min | 15–20 | 21–29 | ≤ 8 or ≥ 30 |
| **Temperature** | 36.1–37.4 °C | 35.1–36.0 or 37.5–38.4 | 38.5–38.9 | ≤ 35.0 or ≥ 39.0 |
| **SpO2** | 96–100% | 94–95% | 92–93% | ≤ 91% |

**VHI Status thresholds:**
- Score 0–2 → `STABLE` → **GREEN** — "Routine monitoring"
- Score 3–4 → `WATCH` → **AMBER** — "Inform the floor doctor or re-check vitals in 2 hours"
- Score 5+ → `CRITICAL` → **RED** — "Urgent medical attention required immediately"

`Patient.acuityColor` is updated to match the latest VHI status every time vitals are recorded.

#### `clinical_note`
```sql
CREATE TABLE clinical_note (
    id                     VARCHAR(36)  NOT NULL PRIMARY KEY,
    patient_id             VARCHAR(36)  NOT NULL,
    hospital_id            VARCHAR(36)  NOT NULL,
    author_id              VARCHAR(36)  NOT NULL,
    note_type              ENUM('WARD_ROUND_NOTE','PROGRESS_NOTE','ADMISSION_NOTE',
                                'DISCHARGE_NOTE','HANDOVER_NOTE','NURSING_REPORT') NOT NULL,
    content                LONGTEXT     NOT NULL,    -- full note or SOAP as JSON
    raw_transcription      LONGTEXT,                 -- Whisper output, nullable
    is_ai_generated        BOOLEAN      NOT NULL DEFAULT FALSE,
    confirmed_by_doctor_at DATETIME,
    ai_model_used          VARCHAR(100),
    created_at             DATETIME     NOT NULL,
    updated_at             DATETIME     NOT NULL
);
CREATE INDEX idx_clinical_note_patient ON clinical_note(patient_id, created_at);
CREATE INDEX idx_clinical_note_hospital_patient ON clinical_note(hospital_id, patient_id, created_at);
```

#### `prescription`
```sql
CREATE TABLE prescription (
    id                    VARCHAR(36)  NOT NULL PRIMARY KEY,
    patient_id            VARCHAR(36)  NOT NULL,
    hospital_id           VARCHAR(36)  NOT NULL,
    clinical_note_id      VARCHAR(36),
    drug_name             VARCHAR(255) NOT NULL,
    dose                  VARCHAR(50)  NOT NULL,
    route                 VARCHAR(50)  NOT NULL,
    frequency_string      VARCHAR(100) NOT NULL,   -- "every 6 hours"
    frequency_hours       INT          NOT NULL,
    total_doses           INT          NOT NULL,
    start_time            DATETIME     NOT NULL,
    administration_times  JSON         NOT NULL,   -- ["2025-05-19T10:00:00", ...]
    confirmed_by_id       VARCHAR(36)  NOT NULL,
    confirmed_at          DATETIME     NOT NULL,
    status                ENUM('ACTIVE','DISCONTINUED','COMPLETED') NOT NULL DEFAULT 'ACTIVE',
    created_at            DATETIME     NOT NULL,
    updated_at            DATETIME     NOT NULL
);
CREATE INDEX idx_prescription_patient ON prescription(patient_id);
CREATE INDEX idx_prescription_hospital_patient ON prescription(hospital_id, patient_id);
```

#### `medication_chart`
```sql
CREATE TABLE medication_chart (
    id              VARCHAR(36)  NOT NULL PRIMARY KEY,
    patient_id      VARCHAR(36)  NOT NULL,
    hospital_id     VARCHAR(36)  NOT NULL,
    prescription_id VARCHAR(36)  NOT NULL,
    status          ENUM('ACTIVE','COMPLETED','DISCONTINUED') NOT NULL DEFAULT 'ACTIVE',
    nurse_notes     TEXT,
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL
);
CREATE INDEX idx_chart_patient ON medication_chart(patient_id, status);
```

#### `medication_task`
```sql
CREATE TABLE medication_task (
    id                    VARCHAR(36)  NOT NULL PRIMARY KEY,
    medication_chart_id   VARCHAR(36)  NOT NULL,
    patient_id            VARCHAR(36)  NOT NULL,
    hospital_id           VARCHAR(36)  NOT NULL,
    ward_id               VARCHAR(36)  NOT NULL,
    assigned_nurse_id     VARCHAR(36),
    scheduled_time        DATETIME     NOT NULL,
    status                ENUM('PENDING','COMPLETED','OVERDUE') NOT NULL DEFAULT 'PENDING',
    completed_at          DATETIME,
    completed_by_id       VARCHAR(36),
    actual_dose_given     VARCHAR(50),             -- may differ if nurse adjusts; defaults to prescribed dose
    pre_reminder_sent_at  DATETIME,                -- set when 5-min pre-task notification fires
    overdue_alert_sent_at DATETIME,                -- set when 5-min overdue notification fires
    created_at            DATETIME     NOT NULL,
    updated_at            DATETIME     NOT NULL
);
CREATE INDEX idx_task_hospital_status_time ON medication_task(hospital_id, status, scheduled_time);
CREATE INDEX idx_task_ward_status ON medication_task(ward_id, status, scheduled_time);
CREATE INDEX idx_task_nurse_status ON medication_task(assigned_nurse_id, status, scheduled_time);
CREATE INDEX idx_task_reminder_window ON medication_task(status, scheduled_time, pre_reminder_sent_at);
```
```

#### `outbox_event`
```sql
CREATE TABLE outbox_event (
    id             VARCHAR(36)  NOT NULL PRIMARY KEY,
    hospital_id    VARCHAR(36)  NOT NULL,
    event_type     VARCHAR(100) NOT NULL,
    payload        LONGTEXT     NOT NULL,
    published      BOOLEAN      NOT NULL DEFAULT FALSE,
    published_at   DATETIME,
    correlation_id VARCHAR(36),
    created_at     DATETIME     NOT NULL,
    updated_at     DATETIME     NOT NULL
);
CREATE INDEX idx_outbox_unpublished ON outbox_event(published, created_at);
```

#### `processed_event`
```sql
CREATE TABLE processed_event (
    event_id    VARCHAR(36)  NOT NULL PRIMARY KEY,  -- outbox event ID
    processed_at DATETIME    NOT NULL
);
-- Used by internal Kafka consumers for idempotency
```

#### QRTZ_* Tables
The standard Quartz 2.3 JDBC tables for MySQL (`QRTZ_JOB_DETAILS`, `QRTZ_TRIGGERS`, `QRTZ_LOCKS`, etc.) are included in V12 migration from the official Quartz distribution.

### Flyway Migration Strategy

**Starting from a clean slate.** All previous migrations are deleted. The `flyway_schema_history` table is dropped along with all domain tables. Every schema is rebuilt from V1.

**Why a clean slate over incremental migrations:**
The overhaul removes roughly 15 entities and fundamentally changes 5 others. Writing V15–V25 drop-and-alter migrations on top of the old schema is harder to read, harder to debug, and carries hidden risk from foreign key constraints on the old tables. Starting from V1 produces a schema that reads exactly as the system is today — no archaeology required.

**How to execute the wipe before Day 1:**
```sql
-- Run against careround_core schema before starting
DROP DATABASE careround_core;
CREATE DATABASE careround_core CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

DROP DATABASE careround_notification;
CREATE DATABASE careround_notification CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

DROP DATABASE careround_audit;
CREATE DATABASE careround_audit CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON careround_core.* TO 'careround'@'%';
GRANT ALL PRIVILEGES ON careround_notification.* TO 'careround'@'%';
GRANT ALL PRIVILEGES ON careround_audit.* TO 'careround'@'%';
FLUSH PRIVILEGES;
```

**Migration files for careround-core (V1 onwards):**

| File | Contents |
|------|----------|
| V1__create_hospital_users.sql | hospital, system_configuration, users, refresh_tokens |
| V2__create_ward_patient.sql | ward, patient |
| V3__create_vitals_notes.sql | patient_vitals, clinical_note |
| V4__create_prescription_chain.sql | prescription, medication_chart, medication_task |
| V5__create_handover_outbox.sql | handover_note, outbox_event, processed_event |
| V6__create_quartz_tables.sql | All QRTZ_* tables (copy from Quartz distribution) |
| V7__create_indexes.sql | All composite performance indexes |

**Migration files for careround-notification:**

| File | Contents |
|------|----------|
| V1__create_failed_notifications.sql | failed_notifications |

**Migration files for careround-audit:**

| File | Contents |
|------|----------|
| V1__create_audit_log.sql | audit_log |

**Delete from the codebase before starting:**
- All Java source files under the old entity packages: `medicalteam/`, `oncall/`, `shift/`, `handover/`, `round/`, `caretask/`, `escalation/`, `nextofkin/`
- All old migration SQL files in `careround-core/src/main/resources/db/migration/`
- Replace with the fresh V1–V7 set

### careround_notification Schema

#### `failed_notifications`
```sql
CREATE TABLE failed_notifications (
    id             VARCHAR(36)  NOT NULL PRIMARY KEY,
    event_type     VARCHAR(100) NOT NULL,
    payload        LONGTEXT     NOT NULL,
    error_message  TEXT,
    failed_at      DATETIME     NOT NULL,
    retry_count    INT          NOT NULL DEFAULT 0,
    resolved       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at     DATETIME     NOT NULL,
    updated_at     DATETIME     NOT NULL
);
```

### careround_audit Schema

#### `audit_log`
```sql
CREATE TABLE audit_log (
    id             VARCHAR(36)  NOT NULL PRIMARY KEY,
    event_id       VARCHAR(36)  NOT NULL UNIQUE,   -- idempotency key
    event_type     VARCHAR(100) NOT NULL,
    hospital_id    VARCHAR(36)  NOT NULL,
    correlation_id VARCHAR(36),
    payload        LONGTEXT     NOT NULL,
    received_at    DATETIME     NOT NULL,
    created_at     DATETIME     NOT NULL,
    updated_at     DATETIME     NOT NULL
);
CREATE INDEX idx_audit_hospital_event ON audit_log(hospital_id, event_type, received_at);
CREATE INDEX idx_audit_event_id ON audit_log(event_id);
```

---

## 8. Business Logic & Rules

### Hospital Registration
1. `code` must be unique across all hospitals. Auto-generated from name if not provided (uppercase, alphanumeric, max 8 chars).
2. Creating a hospital also creates its `SystemConfiguration` record with defaults in the same transaction.
3. The first Admin user is created in the same transaction.

### Multi-Tenancy Rules
1. `hospitalId` is NEVER accepted from request body, query params, or path variables. It is ALWAYS read from `HospitalContextHolder` which is populated by `JwtAuthFilter`.
2. Every repository query uses `findByIdAndHospitalId` — never bare `findById` in business logic.
3. Kafka consumers read `hospitalId` from the event payload — `HospitalContextHolder` is not set in consumer threads.

### Patient Acuity Computation (Vitals Health Index)
1. VHI score is computed in `PatientVitalsService` from five inputs: Pulse, Systolic BP, Respiratory Rate, Temperature, SpO2. Diastolic BP is stored but not scored.
2. Each input is individually scored 0–3 points using the thresholds in Section 7 (patient_vitals schema).
3. Total VHI score = sum of all five individual scores (range 0–15).
4. VHI status mapping: 0–2 → STABLE (GREEN), 3–4 → WATCH (AMBER), 5+ → CRITICAL (RED).
5. `PatientVitals.vhi_score` and `PatientVitals.vhi_status` are persisted with each recording.
6. `Patient.acuityColor` is updated to reflect the latest VHI status immediately after vitals are saved.
7. VHI thresholds (0–2 / 3–4 / 5+) are fixed in application logic — they are not configurable per hospital. The `acuityAmberThreshold` and `acuityRedThreshold` fields in SystemConfiguration are therefore removed. They were a legacy of the NEWS2 design.

### Patient List Ordering
Patients are always sorted: RED first, then AMBER, then GREEN. Within each colour group, the patient with the oldest clinical note timestamp appears first (longest since last seen).

### AI Voice Note Flow
1. Doctor records audio.
2. `POST /api/v1/ai/process-voice-note` — careround-core forwards audio to careround-ai.
3. careround-ai returns structured note and prescriptions.
4. careround-core returns the AI result to the doctor for review.
5. Doctor edits if needed and taps confirm.
6. `POST /api/v1/clinical-notes/confirm` — saves ClinicalNote + all Prescriptions + outbox events in ONE transaction.
7. Doctor's action returns immediately. Chart and task creation are asynchronous.

### Prescription → Chart → Task Chain (Async)
1. `prescription-confirmed` event → `PrescriptionConfirmedConsumer` → creates `MedicationChart` → writes `medication-chart-created` outbox event.
2. `medication-chart-created` event → `MedicationChartCreatedConsumer` → reads `Prescription.administrationTimes[]` → creates one `MedicationTask` per time.
3. Both consumers check `processedEventRepository.existsByEventId()` before acting (idempotency).
4. Both run in their own `@Transactional` context.

### Medication Task Notifications (Two-Pass)

`MedicationTaskReminderJob` runs every minute and performs two passes:

**Pass 1 — Pre-task reminder:**
1. Find PENDING tasks where `scheduled_time` is 4–6 minutes from now AND `pre_reminder_sent_at IS NULL`.
2. Write `medication-task-reminder` outbox event.
3. Set `pre_reminder_sent_at = now()`.
4. Notification sent to nurse: *"[Drug] [dose] for [Patient] — Bed [N] is due in 5 minutes."*

**Pass 2 — Overdue alert:**
1. Find PENDING tasks where `scheduled_time < now() - 5 minutes` AND `overdue_alert_sent_at IS NULL`.
2. Mark `status = OVERDUE`.
3. Write `medication-task-overdue` outbox event.
4. Set `overdue_alert_sent_at = now()`.
5. Notification sent to nurse: *"[Drug] [dose] for [Patient] — Bed [N] is 5 minutes overdue."*

Both passes are independent. A task will receive a pre-reminder, and then later an overdue alert — two separate notifications across the task's lifecycle.

### Medication Task Completion
1. Nurse calls `PUT /medication-tasks/:id/complete` with optional `actualDoseGiven` in the body.
2. `MedicationTaskService.complete()` sets `status = COMPLETED`, `completedAt`, `completedById`, `actualDoseGiven` (defaults to prescribed dose if not provided).
3. The corresponding time chip on the medication chart is derived from the task record — the chart display reads `completedAt`, `actualDoseGiven`, and `completedById` (resolved to nurse's name) directly from the task. No separate chart update call is needed.
4. Writes `medication-task-completed` outbox event for audit trail.

### Nurse Task List Ordering
1. OVERDUE tasks first (sorted by overdue duration descending — longest overdue first).
2. Tasks due within next 30 minutes (AMBER, sorted by `scheduled_time` ascending).
3. All remaining PENDING tasks grouped by hour, sorted by `scheduled_time`.
4. **COMPLETED tasks at the bottom** — a collapsible "Completed today" section showing all tasks completed during the current shift, sorted by `completedAt` descending (most recently completed first).
5. Only tasks for the nurse's ward are shown. If `assigned_nurse_id` is set, show only their tasks.

### Role-Based Access
| Action | ADMIN | DOCTOR | NURSE | SUPERVISOR |
|--------|-------|--------|-------|------------|
| Register/configure hospital | ✓ | | | |
| Create/manage wards | ✓ | | | |
| Create/manage users | ✓ | | | |
| **Register patient** (demographic intake) | ✓ | | | |
| Assign patient to ward/bed | ✓ | | | |
| Update patient clinical details | | ✓ | | |
| Discharge patient | | ✓ | | |
| Record AI voice note | | ✓ | | |
| Confirm AI note & prescription | | ✓ | | |
| Record vitals | | | ✓ | |
| Complete medication tasks | | | ✓ | |
| Edit medication chart | | | ✓ | |
| Add handover note | | | ✓ | |
| View patient records | | ✓ | ✓ | ✓ (read) |
| View supervisor dashboard | | | | ✓ |

---

## 9. Kafka Event Catalogue

All events include: `eventId`, `hospitalId`, `correlationId`, `timestamp`.

### Are 6 Events Enough for a Useful Audit Record?

No. The original 6 topics covered the prescription-to-task chain and patient admission/discharge but missed several significant clinical actions: vitals recordings, task completions, prescription discontinuations, and patient record updates. These are all actions that hospital regulators and legal teams will ask about in an incident investigation. The catalogue is expanded to 11 topics.

| Topic | Produced By | Consumed By | Trigger |
|-------|-------------|-------------|---------|
| `prescription-confirmed` | careround-core | careround-core (internal), careround-audit | Doctor confirms prescription |
| `prescription-discontinued` | careround-core | careround-audit | Doctor or nurse discontinues a prescription |
| `medication-chart-created` | careround-core | careround-core (internal), careround-audit | Internal consumer creates chart from prescription |
| `medication-task-reminder` | careround-core (Quartz) | careround-notification | Task is 5 minutes from scheduled time |
| `medication-task-overdue` | careround-core (Quartz) | careround-notification, careround-audit | Task is 5 minutes past scheduled time |
| `medication-task-completed` | careround-core | careround-audit | Nurse marks task complete |
| `vitals-recorded` | careround-core | careround-audit | Nurse records patient vitals |
| `clinical-note-saved` | careround-core | careround-audit | Doctor or nurse confirms/saves a note |
| `patient-admitted` | careround-core | careround-audit | Admin registers and admits patient |
| `patient-updated` | careround-core | careround-audit | Patient record updated (clinical details, bed change) |
| `patient-discharged` | careround-core | careround-audit | Doctor discharges patient |

**Consumer Groups:**
- `careround-core-internal` — prescription-confirmed, medication-chart-created (internal chain only)
- `careround-notification` — medication-task-reminder, medication-task-overdue
- `careround-audit` — all 11 topics

**What the expanded catalogue covers for an auditor:**
- Full medication lifecycle: prescribed → chart created → reminder sent → completed or overdue → discontinued if applicable
- Every vitals recording — the clinical basis for acuity decisions
- Every clinical note by every role
- Every patient state change from admission to discharge
- Task completions with nurse identity and timestamp

**Payload Examples:**

`medication-task-reminder`:
```json
{
  "eventId": "uuid",
  "taskId": "uuid",
  "patientId": "uuid",
  "wardId": "uuid",
  "hospitalId": "uuid",
  "assignedNurseId": "uuid",
  "drugName": "Amoxicillin",
  "dose": "500mg",
  "scheduledTime": "2025-05-19T10:00:00",
  "minutesUntilDue": 5,
  "correlationId": "uuid",
  "timestamp": "2025-05-19T09:55:00"
}
```

`medication-task-overdue`:
```json
{
  "eventId": "uuid",
  "taskId": "uuid",
  "patientId": "uuid",
  "wardId": "uuid",
  "hospitalId": "uuid",
  "assignedNurseId": "uuid",
  "drugName": "Amoxicillin",
  "dose": "500mg",
  "scheduledTime": "2025-05-19T10:00:00",
  "minutesOverdue": 5,
  "correlationId": "uuid",
  "timestamp": "2025-05-19T10:05:00"
}
```

`medication-task-completed`:
```json
{
  "eventId": "uuid",
  "taskId": "uuid",
  "patientId": "uuid",
  "hospitalId": "uuid",
  "completedByNurseId": "uuid",
  "completedByNurseName": "Sarah O.",
  "drugName": "Amoxicillin",
  "prescribedDose": "500mg",
  "actualDoseGiven": "500mg",
  "scheduledTime": "2025-05-19T10:00:00",
  "completedAt": "2025-05-19T10:08:00",
  "correlationId": "uuid",
  "timestamp": "2025-05-19T10:08:00"
}
```

`vitals-recorded`:
```json
{
  "eventId": "uuid",
  "vitalsId": "uuid",
  "patientId": "uuid",
  "hospitalId": "uuid",
  "recordedByNurseId": "uuid",
  "vhiScore": 3,
  "vhiStatus": "WATCH",
  "previousVhiStatus": "STABLE",
  "recordedAt": "2025-05-19T08:30:00",
  "correlationId": "uuid",
  "timestamp": "2025-05-19T08:30:00"
}
```

`prescription-confirmed`:
```json
{
  "eventId": "uuid",
  "prescriptionId": "uuid",
  "patientId": "uuid",
  "hospitalId": "uuid",
  "correlationId": "uuid",
  "timestamp": "2025-05-19T10:00:00"
}
```

---

## 10. API Endpoints

All endpoints prefixed `/api/v1`. All authenticated routes require `Authorization: Bearer <token>`.

### Authentication
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/auth/login` | Public | Login with hospitalCode, email, password |
| POST | `/auth/refresh` | Public | Rotate refresh token |
| POST | `/auth/logout` | Auth | Revoke refresh token |
| POST | `/auth/change-password` | Auth | Change own password |

### Hospital Management
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/hospitals/register` | Public* | Register new hospital + create admin |
| GET | `/hospitals/me` | Admin | Get own hospital details |
| PUT | `/hospitals/me` | Admin | Update hospital details |
| GET | `/hospitals/me/config` | Admin | Get system configuration |
| PUT | `/hospitals/me/config` | Admin | Update acuity thresholds, overdue windows |

*Protected by a platform registration key in production

### Ward Management
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/wards` | Admin | Create ward |
| GET | `/wards` | Admin, Supervisor | List all wards |
| GET | `/wards/:id` | Auth | Get ward details |
| PUT | `/wards/:id` | Admin | Update ward |

### User Management
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/users` | Admin | Create user |
| GET | `/users` | Admin | List all users |
| GET | `/users/me` | Auth | Get own profile |
| GET | `/users/:id` | Admin | Get user by ID |
| PUT | `/users/:id` | Admin | Update user |
| PUT | `/users/:id/deactivate` | Admin | Deactivate user |
| PUT | `/users/me/device-token` | Nurse | Register FCM device token |

### Patient Management
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/patients` | **Admin only** | Register patient (demographic intake: name, DOB, gender, previous conditions, allergies, emergency contact, ward assignment) |
| GET | `/patients` | Auth | List patients (filtered by wardId, status) |
| GET | `/patients/:id` | Auth | Get patient details |
| PUT | `/patients/:id/clinical` | Doctor | Update clinical details (primary diagnosis, notes) |
| PUT | `/patients/:id/assign-bed` | Admin | Assign or move patient to bed |
| POST | `/patients/:id/discharge` | Doctor | Discharge patient |

### Vitals
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/patients/:id/vitals` | Nurse | Record vitals |
| GET | `/patients/:id/vitals` | Auth | List vitals history (paginated) |

### Clinical Notes
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/clinical-notes/confirm` | Doctor | Save AI-generated or manual note + prescriptions |
| GET | `/patients/:id/notes` | Auth | Get all notes (all types, chronological) |

### AI Proxy
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/ai/process-voice-note` | Doctor, Nurse | Forward audio to careround-ai, return structured result |

### Prescriptions
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/patients/:id/prescriptions` | Auth | List prescriptions |
| PUT | `/prescriptions/:id/discontinue` | Doctor | Discontinue prescription |

### Medication Chart
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/patients/:id/medication-chart` | Auth | Get full medication chart |
| PUT | `/medication-charts/:id` | Nurse | Update chart entry (nurse notes) |
| POST | `/medication-charts/:patientId/manual` | Nurse | Add medication manually |
| PUT | `/medication-charts/:id/discontinue` | Nurse | Discontinue chart entry |

### Medication Tasks
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/medication-tasks` | Nurse | Get nurse's task list (wardId, date, status filters; includes COMPLETED section) |
| PUT | `/medication-tasks/:id/complete` | Nurse | Mark task complete; body: `{ actualDoseGiven?: string }` — updates chart chip with time, dose, nurse name |

### Handover Notes
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/patients/:id/handover-notes` | Nurse | Add handover note |
| GET | `/patients/:id/handover-notes` | Auth | List handover notes |

### Supervisor Dashboard
| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/supervisor/dashboard?wardId=` | Supervisor | Live ward overview (polled every 10s) |

---

## 11. Multi-Tenancy Design

**Login Flow:**
1. User visits the app and enters hospital code (e.g. `STMARYS`), email, and password.
2. `AuthService.login()` looks up `Hospital` by code → finds `User` by `hospitalId + email` → verifies password.
3. JWT issued containing `{ userId, hospitalId, role }`.
4. All subsequent requests carry this JWT.

**Request Flow:**
1. `JwtAuthFilter` validates JWT, extracts `hospitalId, userId, role`.
2. `HospitalContextHolder.set(hospitalId, userId, role)` — thread-local storage.
3. Every service method reads `HospitalContextHolder.getHospitalId()`.
4. Every repository call includes `hospitalId` in the WHERE clause.

**Data Isolation Rules:**
- `findById()` is NEVER used in business logic — always `findByIdAndHospitalId()`
- Redis keys are always namespaced: `sysconfig:{hospitalId}`, `ratelimit:{hospitalId}:{userId}`
- Kafka event payloads always include `hospitalId`
- Kafka consumers use `event.getHospitalId()` — not `HospitalContextHolder`

**Cross-tenant attack result:** Request for resource belonging to Hospital B using Hospital A JWT returns 404 (not 403 — never confirm the resource exists).

---

## 12. Authentication & Security

**JWT:**
- Library: `jjwt 0.12.5`
- Algorithm: HS256 with a 256-bit secret key
- Access token expiry: 15 minutes
- Refresh token expiry: 7 days (rotated on every refresh, stored in DB)
- Claims: `{ sub: userId, hospitalId, role, email }`

**Password:** BCrypt (cost factor 10)

**Refresh Token Rotation:**
1. Client sends refresh token.
2. Server validates (not revoked, not expired).
3. Server revokes the old token.
4. Server issues new access token + new refresh token.
5. All existing refresh tokens are revoked on password change.

**`RefreshTokenCleanupJob`** runs nightly and deletes tokens where `revoked = true OR expires_at < now()` AND `updated_at < now() - 7 days`.

**Rate Limiting:** Redis-backed sliding window per `hospitalId:userId:endpoint`. Configured in `RateLimitingFilter`.

**CORS:** Allow all origins in dev; restrict to known domains in prod.

---

## 13. AI Pipeline

### Complete Flow (single request)

```
Doctor taps "Stop and Save" on recording screen
        │
        ▼
Audio file sent to:
POST /api/v1/ai/process-voice-note (careround-core)
        │
        │ careround-core proxies to careround-ai
        ▼
POST /process-voice-note (careround-ai)
        │
        ├── STEP 1: faster-whisper transcribes audio → raw text
        │
        ├── STEP 2: LLM structures raw text → SOAP note + prescriptions (JSON)
        │           temperature=0.1, json_object response format enforced
        │
        ├── STEP 3: Python calculates administration times from
        │           (start_time, frequencyHours, totalDoses)
        │           — no AI used here, pure datetime arithmetic
        │
        └── Returns structured response to careround-core
                │
                ▼
        careround-core returns to doctor
        Doctor reviews on screen, edits if needed, confirms
                │
                ▼
        POST /api/v1/clinical-notes/confirm
        (saves ClinicalNote + Prescriptions + outbox events)
                │ (async from here)
                ▼
        prescription-confirmed → MedicationChart created
                                → medication-chart-created → MedicationTasks created
```

### Why AI is NOT used for time calculation
LLMs hallucinate on datetime arithmetic. Python's `datetime + timedelta` is exact and deterministic. The boundary is: AI handles language understanding, Python handles computation.

### Handling Long Recordings with Silent Gaps
faster-whisper handles this natively — it processes the full audio file, recognises speech segments and silence, and produces one complete transcription. No special handling needed. The doctor records the entire consultation in one take, including silence during examination.

---

## 14. UI/UX Specification

### Platform Split
- **Web (React + Vite + Tailwind + shadcn/ui):** All 4 roles
- **Mobile (React Native + Expo):** Doctor and Nurse only

### Admin Web (Desktop-first)

**Layout:** Sidebar navigation — Dashboard, Wards, Users, Settings

**Dashboard:** 4 stat cards (total wards, doctors, nurses, patients admitted). Recent user account activity table.

**Wards:** Table with name, beds, active status. Add Ward button opens a **modal** (not slide-over) with 3 fields: name, specialty, total beds.

**Users:** Tab bar filters by role. Table shows name, email, role, status. Add User button opens modal. Deactivate requires confirmation modal.

**Settings:** Two sections: (1) Acuity thresholds with live colour preview, (2) Task overdue windows. Save button at bottom.

### Doctor (Web + Mobile)

**Login → Patient List.** Bottom tab navigation: Patients, Profile.

**Patient List:**
- Cards sorted: RED → AMBER → GREEN, then oldest-seen-first within each colour
- Left colour strip on each card
- Card shows: name (large), bed number, diagnosis, last vitals time, active medication count, "seen today" indicator
- Filter row for colour, search bar secondary

**Patient Detail — Fixed header:** name, age, bed, acuity badge. Floating Record button (mic icon) in bottom-right on all tabs.

**Tab 1 — Overview:**
- Admission summary
- Latest vitals (5 stat cards in a row, always expanded)
- Most recent clinical note (always expanded, full SOAP content)
- Active medications compact list

**Tab 2 — Notes:**
- Scroll to newest on open
- Chronological timeline (oldest at top)
- Each note is a bubble card: author name, role, timestamp, type badge (colour-coded), full content
- Doctor notes show labelled SOAP sections; nurse notes show plain text
- **Add Note** button (top-right): note type selector + single text area (free text, no imposed SOAP structure)
- **Record** button (floating): 3-step flow for doctors

**Doctor Recording Flow:**
1. Full-screen recorder: waveform animation (flat during silence, active during speech), elapsed timer, Pause + Stop & Save buttons, Cancel link
2. Processing screen: 3-step progress track (Transcribing → Structuring → Extracting prescriptions), message managing wait time expectation
3. Review screen: patient context header, collapsible raw transcription, SOAP note as 4 editable text areas, prescription cards with time chips, Add Prescription button. **Confirm** opens a **confirmation modal** (summary of what will be saved) before executing.

**Tab 3 — Medications:**
- One card per active prescription
- Card header: drug name (bold, large), dose + route, frequency, prescribing doctor, date
- Administration time chips: grey (pending), amber (due soon), green (completed with nurse name popover), red (overdue)
- Discontinued prescriptions remain visible, greyed out

**Tab 4 — Vitals:**
- Multi-line chart, last 48 hours default, GREEN/AMBER/RED background bands
- Reverse-chronological table: Time, HR, RR, SpO2, BP, Temp, Acuity badge, Recorded By
- Abnormal values shown in matching acuity colour

### Nurse (Web + Mobile)

**Login → Task List.** Bottom tabs: Tasks (badge shows overdue count), Patients, Profile.

**Task List:**
- Summary bar: total / completed / overdue
- OVERDUE section (red header, always expanded): minutes overdue in red, patient name + bed, drug + dose, large Mark Complete button
- DUE SOON section (amber): tasks due within 30 minutes
- UPCOMING sections grouped by hour
- Mark Complete: intentional gesture (press-hold or swipe) to prevent accidents

**Nurse Recording Flow:**
1. Same recording screen as doctor
2. Processing: single step — "Transcribing your note"
3. Review: note type selector (Handover Note / Nursing Report) + single editable text area
4. Confirmation modal before save

**Patient Detail (Nurse):**
- Same 4 tabs as doctor
- Notes tab: Add Note button visible, Record button visible
- Medications tab: edit mode — discontinue entries, add manual medication
- Handover Notes tab: Add Note button

### Supervisor (Web-only)

**Single screen — Ward Dashboard.** No navigation needed.

**Header:** Ward name, date/time, "Updated N seconds ago" indicator (pulses on refresh).

**4 stat cards:** Total Patients, Tasks Completed (fraction), Overdue Tasks (red if > 0), Completion Rate %.

**Overdue Alert Panel** (if overdue > 0, red background): List of overdue tasks — patient name, bed, drug + dose, minutes overdue, nurse assigned.

**Patient Grid:** Each patient as a card with colour left border. Name + bed, active medication list, task status summary (e.g. "4 of 5 done" in green or "1 overdue" in red), last vitals timestamp.

**Hourly bar chart:** Task completion rate by hour across the current shift.

**Polling:** Every 10 seconds. Silent refresh. Spinner only if refresh takes > 2s.

---

## 15. Infrastructure & Deployment

### AWS Architecture

```
Internet → Route 53 → ACM (SSL)
        → Application Load Balancer (public subnet)
              → Auto Scaling Group: careround-core × 2–3 (private subnet, t3.medium)

Private subnet (not internet-facing):
  careround-ai            EC2 g4dn.xlarge (GPU for demo) or t3.large (CPU)
  careround-notification  EC2 t3.small
  careround-audit         EC2 t3.micro
  Kafka                   EC2 t3.small (Docker, KRaft mode)

Managed services:
  RDS MySQL 8.0           db.t3.medium, Multi-AZ
  ElastiCache Redis       cache.t3.micro

Observability:
  EC2 t3.micro            Prometheus (scrapes all services)
  Grafana Cloud           Free tier (remote_write from Prometheus)
  CloudWatch Logs         Structured JSON from all EC2 instances
```

### Docker Compose (Local Dev — Infrastructure Only)

```yaml
services:
  mysql:      port 3306   # all 3 schemas created via init.sql
  redis:      port 6379
  kafka:      port 9094   # KRaft mode, bitnami/kafka:3.7
  kafka-ui:   port 8090
  ollama:     port 11434  # for careround-ai in dev
  prometheus: port 9090
  grafana:    port 3001
```

Spring Boot apps run on the host (IntelliJ), not in Docker, during development. This enables hot reload and debugging.

### careround-ai EC2 Provisioning

Provisioned by Terraform as part of initial setup. Not re-deployed on every push like the Spring Boot services — only when the AI service code changes.

**Startup sequence:**
1. Docker starts Ollama container
2. Models pulled to volume (once only)
3. Warmup inference runs on both models
4. `/health` returns `ready`
5. careround-core's `AiServiceClient` polls `/health` before making requests

### Deployment Modes

| Service | Method | Zero Downtime |
|---------|--------|---------------|
| careround-core | ASG instance refresh (rolling, min 50% healthy) | Yes |
| careround-ai | SSH deploy + health check wait | Near-zero (30s gap) |
| careround-notification | SSH deploy (stop/start) | Brief gap |
| careround-audit | SSH deploy (stop/start) | Brief gap |

### Environment Variables (all services)

**careround-core (prod):**
```
SPRING_PROFILES_ACTIVE=prod
MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD
REDIS_HOST
KAFKA_BOOTSTRAP_SERVERS
JWT_SECRET
AI_SERVICE_URL=http://<careround-ai-private-ip>:8000
```

**careround-ai:**
```
AI_PROVIDER=ollama   # or vllm
OLLAMA_HOST=http://localhost:11434
WHISPER_MODEL=base.en
LLM_MODEL=mistral:7b
```

**careround-notification:**
```
SPRING_PROFILES_ACTIVE=prod
MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD
KAFKA_BOOTSTRAP_SERVERS
FCM_CREDENTIALS_JSON=<firebase-service-account-json>
```

**careround-audit:**
```
SPRING_PROFILES_ACTIVE=prod
MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD
KAFKA_BOOTSTRAP_SERVERS
```

---

## 16. AWS Setup & Bootstrap

This section covers everything that must be done once before Terraform can run and before the CI/CD pipeline can deploy. Work through it in order — each step is a dependency for the next.

### Step 1 — Verify Existing Account State

Before creating anything, check what already exists to avoid duplicates and conflicts.

```bash
# Configure AWS CLI if not already done
aws configure
# Enter: Access Key ID, Secret Access Key, Region (us-east-1), output format (json)

# Check existing VPCs
aws ec2 describe-vpcs --query 'Vpcs[*].[VpcId,CidrBlock,Tags]' --output table

# Check existing RDS instances
aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceStatus,Endpoint.Address]' --output table

# Check existing ECR repositories
aws ecr describe-repositories --query 'repositories[*].[repositoryName,repositoryUri]' --output table

# Check existing S3 buckets (for Terraform state)
aws s3 ls

# Check existing key pairs
aws ec2 describe-key-pairs --query 'KeyPairs[*].KeyName' --output table
```

Review the output. If a VPC, RDS instance, or ECR repos from the previous deployment exist, decide whether to reuse or delete them. For a clean start, delete previous resources before running Terraform. The database schemas are being wiped regardless, but old EC2 instances and RDS endpoints can conflict with Terraform.

### Step 2 — Create EC2 Key Pair

If a key pair from the previous deployment exists and you still have the `.pem` file, reuse it and skip this step. If not:

```bash
# Create new key pair and save the private key
aws ec2 create-key-pair \
  --key-name careround-keypair \
  --query 'KeyMaterial' \
  --output text > careround-keypair.pem

chmod 400 careround-keypair.pem

# Store the private key contents as a GitHub secret (EC2_SSH_KEY)
# You will need this exact content — copy the full .pem file
```

### Step 3 — Create S3 Bucket for Terraform State

Terraform needs a remote state backend. The bucket name must be globally unique.

```bash
# Create the bucket (replace ACCOUNT_ID with your AWS account ID)
aws s3api create-bucket \
  --bucket careround-terraform-state-ACCOUNT_ID \
  --region us-east-1

# Enable versioning (protects state history)
aws s3api put-bucket-versioning \
  --bucket careround-terraform-state-ACCOUNT_ID \
  --versioning-configuration Status=Enabled

# Block all public access
aws s3api put-public-access-block \
  --bucket careround-terraform-state-ACCOUNT_ID \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Update `infrastructure/main.tf` with your bucket name:
```hcl
backend "s3" {
  bucket = "careround-terraform-state-ACCOUNT_ID"
  key    = "prod/terraform.tfstate"
  region = "us-east-1"
}
```

### Step 4 — Create IAM User for CI/CD

This user's credentials are stored as GitHub Actions secrets. It needs only the permissions the pipeline requires — not admin access.

```bash
# Create the CI/CD user
aws iam create-user --user-name careround-cicd

# Create and attach an inline policy
aws iam put-user-policy \
  --user-name careround-cicd \
  --policy-name CareRoundCICDPolicy \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage"
        ],
        "Resource": "*"
      },
      {
        "Effect": "Allow",
        "Action": [
          "autoscaling:StartInstanceRefresh",
          "autoscaling:DescribeInstanceRefreshes",
          "autoscaling:DescribeAutoScalingGroups"
        ],
        "Resource": "*"
      },
      {
        "Effect": "Allow",
        "Action": [
          "ec2:DescribeInstances"
        ],
        "Resource": "*"
      }
    ]
  }'

# Create access keys for this user
aws iam create-access-key --user-name careround-cicd
# Save the AccessKeyId and SecretAccessKey — you cannot retrieve the secret again
```

### Step 5 — Create ECR Repositories

```bash
# Create one repository per service
for SERVICE in careround-core careround-notification careround-audit careround-ai; do
  aws ecr create-repository \
    --repository-name $SERVICE \
    --region us-east-1 \
    --image-scanning-configuration scanOnPush=true
  echo "Created: $SERVICE"
done

# Get your account ID for use in subsequent steps
aws sts get-caller-identity --query Account --output text
```

Note the repository URIs — they follow the format:
`<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/<service-name>`

### Step 6 — Create Firebase Project for FCM

1. Go to [console.firebase.google.com](https://console.firebase.google.com)
2. Create a new project named `careround`
3. Navigate to Project Settings → Service Accounts
4. Click "Generate new private key" — this downloads a JSON file
5. Keep this JSON file — its contents become the `FCM_CREDENTIALS_JSON` environment variable
6. Navigate to Project Settings → General → Your apps
7. Add an Android app with package name `com.careround.app`
8. Download the `google-services.json` file — this goes in the React Native project

### Step 7 — Generate JWT Secret

```bash
# Generate a cryptographically secure 256-bit secret
openssl rand -base64 32
# Example output: 3f8a2b1c9d4e7f0a5b6c2d8e1f4a7b3c9d2e5f8...
# Save this — it becomes the JWT_SECRET environment variable
```

### Step 8 — Run Terraform

```bash
cd infrastructure

# Initialise (downloads providers, connects to S3 backend)
terraform init

# Preview what will be created
terraform plan \
  -var="db_password=YourSecureDBPassword123!" \
  -var="jwt_secret=$(openssl rand -base64 32)" \
  -var="mysql_user=careround" \
  -var="key_pair_name=careround-keypair" \
  -var="aws_region=us-east-1" \
  -out=tfplan

# Review the plan — verify no unintended changes to existing resources
# Then apply
terraform apply tfplan
```

Terraform creates in order: VPC → Subnets → Security Groups → RDS → ElastiCache → Kafka EC2 → careround-ai EC2 → ALB → ASG (careround-core) → careround-notification EC2 → careround-audit EC2 → Prometheus EC2.

This takes approximately 15–20 minutes for RDS to become available.

### Step 9 — Provision careround-ai EC2

After Terraform runs, the careround-ai instance exists but the models are not yet loaded. SSH in and run the one-time setup:

```bash
# Get the instance IP from Terraform output
terraform output careround_ai_private_ip

# SSH into the instance
ssh -i careround-keypair.pem ec2-user@<ai-instance-ip>

# Install Docker and NVIDIA drivers (if GPU instance)
sudo yum update -y
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# For GPU instance (g4dn.xlarge): install NVIDIA container toolkit
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
sudo yum install -y nvidia-container-toolkit

# Pull and start Ollama
docker run -d \
  --name ollama \
  --restart unless-stopped \
  -p 11434:11434 \
  -v ollama_data:/root/.ollama \
  ollama/ollama

# Pull models (this takes 5–15 minutes per model)
docker exec ollama ollama pull mistral:7b
# Optionally also pull a smaller model as fallback
docker exec ollama ollama pull llama3.2:3b

# Verify Ollama is working
curl http://localhost:11434/api/tags
# Should list the pulled models

# Exit and note the instance's private IP
# This becomes the AI_SERVICE_URL in careround-core's environment
```

### Step 10 — Set Up GitHub Actions Secrets

In the careround monorepo (GitHub → Settings → Secrets and Variables → Actions):

| Secret Name | Value |
|-------------|-------|
| `AWS_ACCESS_KEY_ID` | From Step 4 IAM user |
| `AWS_SECRET_ACCESS_KEY` | From Step 4 IAM user |
| `AWS_ACCOUNT_ID` | From `aws sts get-caller-identity` |
| `EC2_SSH_KEY` | Full contents of careround-keypair.pem |
| `NOTIFICATION_HOST` | Private IP of careround-notification EC2 |
| `AUDIT_HOST` | Private IP of careround-audit EC2 |
| `AI_HOST` | Private IP of careround-ai EC2 |

In the careround-ai separate repo (same Settings → Secrets path):

| Secret Name | Value |
|-------------|-------|
| `AWS_ACCESS_KEY_ID` | Same as above |
| `AWS_SECRET_ACCESS_KEY` | Same as above |
| `AWS_ACCOUNT_ID` | Same as above |
| `EC2_SSH_KEY` | Same as above |
| `AI_HOST` | Private IP of careround-ai EC2 |

### Step 11 — Verify End-to-End Connectivity

Before writing a line of application code, confirm the infrastructure is correctly wired:

```bash
# From your local machine — verify ALB is reachable
curl https://<alb-dns-name>/actuator/health
# Expected: connection refused (no app deployed yet) or 503 (healthy ALB, no targets)

# From careround-core EC2 — verify it can reach RDS
mysql -h <rds-endpoint> -u careround -p careround_core
# Should connect successfully

# From careround-core EC2 — verify it can reach Redis
redis-cli -h <elasticache-endpoint> ping
# Should return: PONG

# From careround-core EC2 — verify it can reach Kafka
# (after Kafka EC2 is up)
nc -zv <kafka-private-ip> 9092
# Should return: Connection to <ip> 9092 port [tcp] succeeded

# From careround-core EC2 — verify it can reach careround-ai
curl http://<ai-private-ip>:8000/health
# Should return: {"status": "ready"} or {"status": "loading"}
```

If any check fails, the most common causes are security group rules (check inbound rules on the target), route table configuration (private subnets must have NAT gateway route), or the target service not yet running.

### Step 12 — Push Initial Docker Images

Build skeleton images (just the entry point, no business logic needed yet) to verify the full ECR + deploy pipeline works before Day 1 development begins:

```bash
# From the careround repo root
mvn clean package -DskipTests

ECR=<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR

for SERVICE in careround-core careround-notification careround-audit; do
  docker build -t $ECR/$SERVICE:latest $SERVICE/
  docker push $ECR/$SERVICE:latest
done
```

Then push a commit to `main` and verify the GitHub Actions workflow runs cleanly. The first deploy will likely fail health checks (no database schema yet) — that is expected and acceptable at this stage. What you are verifying is that the pipeline itself reaches the deploy step without errors.

---

## 17. CI/CD Pipeline

GitHub Actions triggers on push to `main`.

**Job 1 — test:**
```
mvn test --no-transfer-progress
```

**Job 2 — build-and-push (matrix: core, notification, audit):**
```
mvn package -pl <service> -am -DskipTests
docker build + push to ECR (tagged :sha + :latest)
```

**Job 2b — build-and-push careround-ai (separate repo trigger):**
```
docker build + push to ECR
```

**Job 3 — deploy:**
```
careround-core:
  aws autoscaling start-instance-refresh
  --preferences MinHealthyPercentage=50, InstanceWarmup=60

careround-ai:
  SSH: pull latest image, stop old, start new
  Wait up to 5 minutes for /health = ready
  Rollback to previous image if timeout

careround-notification, careround-audit:
  SSH: pull latest, stop old, start new, verify /actuator/health
```

**Secrets required:**
```
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_ACCOUNT_ID
EC2_SSH_KEY
NOTIFICATION_HOST
AUDIT_HOST
AI_HOST
```

---

## 18. Observability

### Structured Logging
All services use Logback with `logstash-logback-encoder`. Every log line includes:
```json
{
  "timestamp": "...",
  "level": "INFO",
  "service": "careround-core",
  "correlationId": "from MDC",
  "hospitalId": "from MDC",
  "userId": "from MDC",
  "logger": "...",
  "message": "..."
}
```

`CorrelationIdFilter` generates a UUID per request and stores it in MDC. `JwtAuthFilter` adds `hospitalId` and `userId` to MDC. All internal Kafka consumers propagate `correlationId` from event payload into MDC.

### Custom Micrometer Metrics
```
careround.vitals.acuity.breach{color, hospitalId}
careround.clinical.notes.saved{hospitalId}
careround.tasks.overdue{hospitalId}
careround.auth.logins{hospitalId}
careround.prescriptions.confirmed{hospitalId}
```

### Grafana Dashboards
- JVM Heap, CPU, thread count (Spring Boot default)
- HikariCP active connections
- Kafka consumer lag (careround-notification, careround-audit)
- Business metrics: tasks completed per hour, overdue rate, prescriptions confirmed
- HTTP p50/p95/p99 latency by endpoint

### Health Checks
All Spring Boot services expose `/actuator/health` — checks DB, Redis (core), Kafka connectivity. ALB uses this to gate traffic.

---

## 19. 4-Day Implementation Plan

### Developer Assignments

- **Dev A — David:** AWS Bootstrap (Pre-Day 1) → AI Service (Days 1–2) → Web Frontend (Days 2–4) → Backend support after AI is done
- **Dev B — Teslim:** Backend overhaul (Days 1–2) → Mobile (Days 1–3) → Integration testing (Day 4)

The handoff point: once David finishes the AI service and deploys it (end of Day 2 morning), he switches fully to web frontend. If the backend has remaining work that needs two people, David jumps in before starting web.

### Pre-Day 1 — AWS Bootstrap (David, before development begins)

Work through Section 16 (AWS Setup & Bootstrap) completely before Day 1. This is a prerequisite for everything else. Estimated time: 3–4 hours. Do it the evening before or first thing on Day 1 morning before Teslim needs the infrastructure.

Checklist before calling it done:
- [ ] AWS CLI configured and verified against existing account
- [ ] S3 bucket for Terraform state created
- [ ] IAM CI/CD user created with access keys stored safely
- [ ] EC2 key pair created or verified from previous deployment, `.pem` file stored securely
- [ ] ECR repositories created for all 4 services
- [ ] Firebase project created, service account JSON downloaded, `google-services.json` downloaded
- [ ] JWT secret generated and saved
- [ ] Terraform `init` and `apply` completed successfully
- [ ] careround-ai EC2 provisioned with Docker + Ollama + models pulled (this takes 15–20 min — start it first and let it run while doing other steps)
- [ ] GitHub Actions secrets set in both repos
- [ ] Connectivity verified: core → RDS, core → Redis, core → Kafka, core → AI
- [ ] Initial skeleton Docker images pushed, CI/CD pipeline runs without errors

### Day 1 — Backend Overhaul + AI Service Foundation

**David — AI Service**

Morning:
- Confirm careround-ai EC2: SSH in, verify Ollama is running, models are loaded (`docker exec ollama ollama list`)
- Scaffold careround-ai repo: `fastapi`, `python-multipart`, `faster-whisper`, `ollama`, `pydantic` in `requirements.txt`, Dockerfile
- `whisper_service.py` — faster-whisper wrapper, accept any audio format Whisper supports, return raw text string
- `llm_service.py` — Ollama client wrapper, medical system prompt, `format: json` enforced, temperature 0.1

Afternoon:
- `process_voice_note.py` route — sequential pipeline: transcribe → structure + extract prescriptions → calculate administration times (pure Python) → return complete response
- `/health` endpoint — returns `{"status": "loading"}` until warmup inference succeeds, then `{"status": "ready"}`
- Write and iterate on the medical system prompt using real clinical audio samples until SOAP note quality and prescription extraction are reliable
- Push to careround-ai repo, verify GitHub Actions deploys to EC2 and `/health` returns `ready`

**Teslim — Backend Overhaul**

Morning:
- Run the database wipe SQL (Section 7, Flyway Migration Strategy) against the deployed RDS instance
- Delete all Java source files for removed features from the codebase: `medicalteam/`, `oncall/`, `shift/`, `handover/`, `round/`, `caretask/`, `escalation/`, `nextofkin/` packages entirely
- Delete all existing Flyway migration SQL files from `careround-core/src/main/resources/db/migration/`
- Update `UserRole` enum to 4 values: ADMIN, DOCTOR, NURSE, SUPERVISOR

Afternoon:
- Write all entities from scratch matching the schema in Section 7: Hospital (with `code`), SystemConfiguration (updated fields), User (4 roles), Ward, Patient (`acuityColor`, no team fields), PatientVitals (`acuityColor`), ClinicalNote (AI fields), RefreshToken
- Write new entities: Prescription (with `administration_times` JSON column + `LocalDateTimeListConverter`), MedicationChart, MedicationTask, HandoverNote, OutboxEvent, ProcessedEvent
- Write all fresh Flyway migrations V1–V7
- Write all repositories with scoped query methods — no bare `findById` anywhere
- Verify: `mvn clean package -DskipTests` passes, application starts, Flyway runs V1–V7 cleanly, push to `main`
- Begin React Native + Expo project: initialise, Expo Router navigation, install Zustand, Axios, `expo-av`, `expo-secure-store`, `@react-native-async-storage/async-storage`
- Login screen: hospital code, email, password fields; Zustand auth store; JWT persisted in SecureStore

### Day 2 — Core Business Logic + AI Deployed + Mobile Doctor

**David — AI Service Complete + Web Frontend Begins**

Morning:
- Final AI service tuning — test with varied clinical audio (different accents, medical terminology, multi-drug prescriptions)
- Deploy final version to EC2, confirm careround-core can call it end-to-end once Teslim has the `AiServiceClient` wired
- **After AI is deployed and verified:** switch to web frontend
- Web frontend scaffold: Vite + React + TypeScript + Tailwind + shadcn/ui
- Zustand auth store, Axios instance with JWT interceptor and silent refresh on 401
- Login page (hospital code, email, password)

Afternoon:
- Admin layout: sidebar navigation (Dashboard, Wards, Users, Settings)
- Admin Dashboard page: 4 stat cards (total wards, doctors, nurses, patients admitted), recent user activity table
- Admin Wards page: table with Add Ward modal (name, specialty, total beds)
- Admin Users page: role tab filter, table, Add User modal, deactivate confirmation modal
- Admin Settings page: acuity thresholds with live colour preview, overdue window inputs, Save button

**Teslim — Core Business Logic + Mobile Doctor**

Morning:
- `HospitalService.register()` — Hospital + SystemConfiguration + Admin in one `@Transactional` method
- `PatientVitalsService.record()` — save vitals, compute acuity colour from SystemConfiguration thresholds, update `Patient.acuityColor`
- `AiServiceClient` — Spring `RestClient` to careround-ai, polls `/health` before requests, returns 503 if AI not ready
- `POST /api/v1/ai/process-voice-note` — controller proxies multipart audio to `AiServiceClient`, returns AI response to frontend
- `ClinicalNoteService.confirm()` — saves ClinicalNote + Prescriptions + outbox events in one `@Transactional`

Afternoon (backend):
- Internal Kafka consumer config (`careround-core-internal` group)
- `PrescriptionConfirmedConsumer` — idempotency check, creates MedicationChart, writes outbox event
- `MedicationChartCreatedConsumer` — reads `Prescription.administrationTimes`, creates one MedicationTask per time
- `MedicationTaskService.getTaskList()` — sorted: OVERDUE first, then due within 30 mins, then upcoming
- `MedicationTaskService.complete()` — marks complete, sets `completedAt`, `completedById`
- All controllers and DTOs for the above

Afternoon (mobile — after backend core is wired):
- Doctor: Patient list screen — acuity-coloured cards, RED→AMBER→GREEN sort, colour strip, "seen today" indicator
- Doctor: Patient detail screen — fixed header with acuity badge, 4-tab container
- Doctor: Overview tab — admission summary, latest vitals stat row, most recent note expanded, active medications list

### Day 3 — Backend Finishes + Web Doctor + Mobile Nurse + Design Diagrams

**David — Web Doctor and Supervisor**

Morning:
- Doctor: Patient list page — cards with acuity colour strips, VHI badge, sort order, filter row
- Doctor: Patient detail — fixed header, 4-tab structure
- Doctor: Overview tab and Notes tab (chronological timeline, auto-scroll to newest, note bubble cards, Add Note modal with type selector + free text, Record button)

Afternoon:
- Doctor: Recording flow on web — Web Audio API for recording, full-screen recorder UI, waveform animation, elapsed timer, Pause + Stop & Save
- Doctor: Processing screen — 3-step progress (Transcribing → Structuring → Extracting)
- Doctor: Review screen — editable SOAP text areas, prescription chips with time badges, Add/Remove prescription, confirmation modal
- Doctor: Medications tab (time chips, completed chip showing dose + time + nurse name) and Vitals tab (multi-line chart + reverse-chronological table with VHI column)

**End of Day 3 — Design Diagrams (both developers, ~1 hour together):**
- **System architecture diagram** — all 4 services, Kafka, MySQL, Redis, AWS ALB/ASG topology. Tool: Excalidraw or draw.io.
- **ERD (Entity Relationship Diagram)** — all core entities and their relationships. Tool: dbdiagram.io using the DBML schema from Section 7.
- **AI pipeline sequence diagram** — doctor records → careround-core proxies → careround-ai (Whisper → LLM → Python) → returns structured result → doctor confirms → outbox → prescription chain.
- **Prescription → task async flow** — sequence diagram showing outbox → Kafka → PrescriptionConfirmedConsumer → MedicationChartCreatedConsumer → MedicationTask rows.
- These diagrams go into a `/docs/diagrams/` folder in the monorepo and are referenced in the README. They are also the architecture slides for the demo presentation.

**Teslim — Backend Completion + Mobile Nurse**

Morning (backend):
- `MedicationTaskReminderJob` (Quartz, replaces old overdue-only job) — two-pass logic: pre-reminder at T-5min and overdue alert at T+5min, writing respective outbox events
- `RefreshTokenCleanupJob` and `OutboxCleanupJob`
- `supervisor/dashboard` endpoint — aggregation query: patient list with VHI, per-patient task counts, hourly chart data
- careround-notification: FCM setup, `MedicationTaskReminderConsumer` + `MedicationTaskOverdueConsumer`, DLT config for both topics, `NotificationDltConsumer`
- careround-audit: `AuditEventConsumer` on all 11 topics, idempotency check

Morning (mobile):
- Doctor: Notes tab — chronological scroll, note bubble cards, Add Note modal, Record button
- Doctor: Recording flow — full-screen recorder with `expo-av`, waveform animation, Pause + Stop & Save, Cancel
- Doctor: Processing screen and Review screen — SOAP editing, prescription time chips, confirmation modal

Afternoon (mobile):
- Nurse: Login → Task list as landing screen
- Nurse: Task list — summary bar, OVERDUE section (red, always expanded), DUE SOON (amber), UPCOMING grouped by hour, COMPLETED section at the bottom (collapsible, shows drug + time administered + nurse name), Mark Complete with press-hold gesture
- Nurse: Patient detail — 4-tab structure, Medications tab (time chips updating to show dose + time + nurse on completion, add/discontinue), Vitals recording form with live VHI preview
- Nurse: Handover Notes tab with Add Note modal, simplified recording flow (transcribe only → single text area → confirm)
- FCM token registration: `PUT /users/me/device-token` called on nurse login, test both pre-reminder and overdue notifications on physical device

### Day 4 — Integration, Testing, Demo Prep

**David — Web Supervisor + End-to-End Testing**

Morning:
- Supervisor: Ward dashboard — 4 stat cards, Overdue Alert Panel (red background, per-task rows), Patient Grid (colour cards, task summary), hourly bar chart (Recharts), 10-second polling
- Fix any integration bugs found while connecting web frontend to deployed backend

Afternoon:
- End-to-end test on web: admit patient → record voice note → verify AI output → confirm → verify tasks generated → nurse completes tasks → supervisor dashboard updates
- Run k6 load test against deployed AWS environment
- Verify Grafana shows live metrics during load test
- Seed demo data: 2 hospitals, 3 wards each, 10–15 patients with varying acuity, several tasks in each state

**Teslim — Mobile Testing + Demo**

Morning:
- End-to-end test on physical Android device: doctor records voice note → AI processes → reviews and confirms → nurse sees tasks → completes → push notification fires for overdue task
- Fix mobile-specific bugs (audio format handling, permission prompts, SecureStore behaviour, FCM token registration)

Afternoon:
- Full demo flow rehearsal on both web and mobile — at least twice from start to finish
- Fix any critical UX friction found during rehearsal
- Final system check: all 4 services healthy on AWS, Kafka topics populated, Grafana live, seed data loaded
- Prepare demo environment: Grafana dashboard visible on a second screen showing p95 latency, task completion rate, and active DB connections during the live demo

---

## Appendix: Key Design Decisions

**Modular monolith over microservices for the core:** Clinical workflows (prescription → chart → task) are deeply coupled and require transactional integrity. Splitting them would require Saga pattern, compensating transactions, and distributed failure handling — complexity with no scaling benefit at this domain's scale.

**careround-ai as a separate service:** Different runtime (Python), different hardware requirements (GPU), different scaling profile. Keeping it separate allows the Spring Boot services to deploy independently of AI model changes.

**Transactional Outbox over direct Kafka publish:** If Kafka is unavailable when a prescription is confirmed, the data is still saved and the event will be delivered when Kafka recovers. Direct Kafka publish would either fail the doctor's action or lose the event.

**Internal Kafka consumers over synchronous chain:** The doctor's confirmation should never fail because chart creation failed. Async decoupling via Kafka ensures the doctor moves on immediately while the downstream chain completes in the background.

**Self-hosted AI models:** Protected health information cannot be sent to third-party APIs. Self-hosting ensures no patient data leaves the hospital's cloud infrastructure.

**Hospital code on login (not subdomain):** Simpler to implement for MVP, works on both web and mobile without DNS configuration. Revisit subdomain routing post-MVP.

**Polling for supervisor dashboard (not WebSockets):** Simpler infrastructure, no persistent connection management, acceptable for clinical use case where 10-second staleness is fine.