from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

class CaseCreate(BaseModel):
    case_number: str
    case_type: Optional[str] = None
    subject: Optional[str] = None
    act: Optional[str] = None
    sections: Optional[str] = None
    applicant: Optional[str] = None
    respondent: Optional[str] = None
    filing_date: Optional[str] = None
    status: str = "Open"
class CaseUpdate(BaseModel):
    case_number: Optional[str] = None
    case_type: Optional[str] = None
    subject: Optional[str] = None
    act: Optional[str] = None
    sections: Optional[str] = None
    applicant: Optional[str] = None
    respondent: Optional[str] = None
    filing_date: Optional[str] = None
    status: Optional[str] = None
class CaseOut(CaseCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)
class HighlightIn(BaseModel):
    page_number: int = Field(ge=1)
    matched_text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float
    segment_order: int = 0
class CitationIn(BaseModel):
    chunk_id: int
    relevance_note: Optional[str] = None
    highlights: list[HighlightIn] = []
class AnalysisItemCreate(BaseModel):
    section: str = "General"
    item_order: int = 0
    content: str
    event_date: Optional[str] = None
    citations: list[CitationIn] = []
class NoteSectionIn(BaseModel):
    heading: str
    content: str = ""
    position: int = 0
class NoteSheetSave(BaseModel):
    title: str = "Case Note Sheet"
    sections: list[NoteSectionIn]
