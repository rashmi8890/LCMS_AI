from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, Boolean, DateTime, ForeignKey, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    case_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    act: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sections: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicant: Mapped[str | None] = mapped_column(String(300), nullable=True)
    respondent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    filing_date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="Open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    documents: Mapped[list["Document"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    analysis_items: Mapped[list["AnalysisItem"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    notesheet: Mapped["NoteSheet | None"] = relationship(back_populates="case", cascade="all, delete-orphan", uselist=False)

class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    case: Mapped["Case"] = relationship(back_populates="documents")
    pages: Mapped[list["DocumentPage"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")

class DocumentPage(Base):
    __tablename__ = "document_pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    width: Mapped[float] = mapped_column(Float, default=0)
    height: Mapped[float] = mapped_column(Float, default=0)
    document: Mapped["Document"] = relationship(back_populates="pages")
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("document_pages.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    document: Mapped["Document"] = relationship(back_populates="chunks")
    page: Mapped["DocumentPage"] = relationship()

class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), unique=True, index=True
    )
    model_name: Mapped[str] = mapped_column(String(200), index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(JSON)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    chunk: Mapped["Chunk"] = relationship()

class AnalysisItem(Base):
    __tablename__ = "analysis_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(150), default="General")
    item_order: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    # Populated only for Chronology items. Keep as source-faithful text so
    # partial dates and dates with uncertain formats are not silently altered.
    event_date: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    case: Mapped["Case"] = relationship(back_populates="analysis_items")
    citations: Mapped[list["Citation"]] = relationship(back_populates="analysis_item", cascade="all, delete-orphan")

class Citation(Base):
    __tablename__ = "citations"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_item_id: Mapped[int] = mapped_column(ForeignKey("analysis_items.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    relevance_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_item: Mapped["AnalysisItem"] = relationship(back_populates="citations")
    chunk: Mapped["Chunk"] = relationship()
    highlights: Mapped[list["HighlightSegment"]] = relationship(back_populates="citation", cascade="all, delete-orphan")

class HighlightSegment(Base):
    __tablename__ = "highlight_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    citation_id: Mapped[int] = mapped_column(ForeignKey("citations.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    matched_text: Mapped[str] = mapped_column(Text)
    x0: Mapped[float] = mapped_column(Float)
    y0: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    page_width: Mapped[float] = mapped_column(Float)
    page_height: Mapped[float] = mapped_column(Float)
    segment_order: Mapped[int] = mapped_column(Integer, default=0)
    citation: Mapped["Citation"] = relationship(back_populates="highlights")

class NoteSheet(Base):
    __tablename__ = "notesheets"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), default="Case Note Sheet")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    case: Mapped["Case"] = relationship(back_populates="notesheet")
    sections: Mapped[list["NoteSheetSection"]] = relationship(back_populates="notesheet", cascade="all, delete-orphan", order_by="NoteSheetSection.position")

class NoteSheetSection(Base):
    __tablename__ = "notesheet_sections"
    id: Mapped[int] = mapped_column(primary_key=True)
    notesheet_id: Mapped[int] = mapped_column(ForeignKey("notesheets.id", ondelete="CASCADE"), index=True)
    heading: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    notesheet: Mapped["NoteSheet"] = relationship(back_populates="sections")
