from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from typing import Annotated, List, Literal, Optional


# --- LLM raw output models (validated before mapping to response) ---

NonEmptyStrictStr = Annotated[StrictStr, Field(min_length=1)]

class LLMSoapNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjective: StrictStr
    objective: StrictStr
    assessment: StrictStr
    plan: StrictStr


class LLMPrescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drugName: NonEmptyStrictStr
    dose: NonEmptyStrictStr
    route: NonEmptyStrictStr
    frequencyString: Optional[StrictStr]
    frequencyHours: Optional[StrictInt]
    totalDoses: Optional[StrictInt]


class LLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    soap: LLMSoapNote
    prescriptions: List[LLMPrescription]


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
