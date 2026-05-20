from pydantic import BaseModel, Field
from typing import List, Literal, Optional


# --- LLM raw output models (validated before mapping to response) ---

class LLMSoapNote(BaseModel):
    subjective: str = ""
    objective: str = ""
    assessment: str = ""
    plan: str = ""


class LLMPrescription(BaseModel):
    drugName: str
    dose: str
    route: str
    frequencyString: Optional[str] = None
    frequencyHours: Optional[int] = None
    totalDoses: Optional[int] = None


class LLMOutput(BaseModel):
    soap: LLMSoapNote
    prescriptions: List[LLMPrescription] = Field(default_factory=list)


# --- API response models ---

class PrescriptionExtracted(BaseModel):
    drugName: str
    dose: str
    route: str
    frequencyString: Optional[str] = None
    frequencyHours: Optional[int] = None
    totalDoses: Optional[int] = None
    administrationTimes: List[str] = Field(default_factory=list)


class ClinicalNote(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str


class ProcessVoiceNoteResponse(BaseModel):
    rawTranscription: str
    mode: Literal["ward_round", "transcription_only"]
    clinicalNote: Optional[ClinicalNote] = None
    prescriptions: List[PrescriptionExtracted]


class HealthResponse(BaseModel):
    status: str  # "loading" | "ready"
    whisperLoaded: bool
    llmLoaded: bool
