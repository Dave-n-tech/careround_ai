from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class PrescriptionExtracted(BaseModel):
    drugName: str
    dose: str
    route: str
    frequencyString: Optional[str] = None
    frequencyHours: Optional[int] = None
    totalDoses: Optional[int] = None
    administrationTimes: List[str] = Field(default_factory=list)   # ISO datetime strings

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
    status: str                      # "loading" | "ready"
    whisperLoaded: bool
    llmLoaded: bool
