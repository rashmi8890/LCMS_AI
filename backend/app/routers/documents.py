import os
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Case, Chunk, Citation, Document, DocumentPage
from ..services.pdf_service import extract_pdf, split_text

router = APIRouter(tags=["documents"])


def _save_one(case_id, filename, data, db):
    folder = Path(settings.upload_dir) / str(case_id)
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    path = folder / f"{uuid.uuid4().hex}_{safe_name}"
    path.write_bytes(data)
    obj = Document(case_id=case_id, filename=safe_name, stored_path=str(path))
    db.add(obj)
    return obj


@router.post("/api/cases/{case_id}/documents")
async def upload_document(
    case_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    return await upload_documents(case_id, [file], db)


@router.post("/api/cases/{case_id}/documents/batch")
async def upload_documents(
    case_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    if not files:
        raise HTTPException(400, "Select at least one PDF.")
    if len(files) > 30:
        raise HTTPException(400, "Maximum 30 PDFs per upload batch.")

    created, errors = [], []
    for file in files:
        name = file.filename or "unnamed"
        if not name.lower().endswith(".pdf"):
            errors.append({"filename": name, "error": "Only PDF files are accepted."})
            continue

        data = await file.read()
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            errors.append({
                "filename": name,
                "error": f"Exceeds {settings.max_upload_mb} MB limit.",
            })
            continue
        if not data.startswith(b"%PDF"):
            errors.append({
                "filename": name,
                "error": "File does not have a valid PDF signature.",
            })
            continue

        try:
            obj = _save_one(case_id, name, data, db)
            db.flush()
            created.append({
                "id": obj.id,
                "filename": obj.filename,
                "is_processed": False,
            })
        except Exception as exc:
            errors.append({"filename": name, "error": str(exc)})

    db.commit()
    return {
        "uploaded": created,
        "errors": errors,
        "uploaded_count": len(created),
        "failed_count": len(errors),
    }


@router.get("/api/cases/{case_id}/documents")
def list_documents(case_id: int, db: Session = Depends(get_db)):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    docs = (
        db.query(Document)
        .filter_by(case_id=case_id)
        .order_by(Document.id.desc())
        .all()
    )
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "is_processed": d.is_processed,
            "page_count": len(d.pages),
            "chunk_count": len(d.chunks),
        }
        for d in docs
    ]


def _clear_extracted_content(document_id: int, db: Session):
    """Clear stale extraction rows before retrying a document with no chunks."""
    old_chunks = db.query(Chunk).filter_by(document_id=document_id).all()
    old_chunk_ids = [chunk.id for chunk in old_chunks]

    if old_chunk_ids:
        old_citations = (
            db.query(Citation)
            .filter(Citation.chunk_id.in_(old_chunk_ids))
            .all()
        )
        for citation in old_citations:
            db.delete(citation)

    for chunk in old_chunks:
        db.delete(chunk)
    for page in db.query(DocumentPage).filter_by(document_id=document_id).all():
        db.delete(page)
    db.flush()


def _extract_and_store(doc: Document, db: Session):
    if not os.path.exists(doc.stored_path):
        raise FileNotFoundError(f"Stored PDF not found: {doc.filename}")

    pages = extract_pdf(doc.stored_path)
    _clear_extracted_content(doc.id, db)

    chunk_count = 0
    extracted_chars = 0

    for page_data in pages:
        page_text = page_data.get("text") or ""
        extracted_chars += len(page_text.strip())

        page = DocumentPage(
            document_id=doc.id,
            page_number=page_data["page_number"],
            text=page_text,
            width=page_data["width"],
            height=page_data["height"],
        )
        db.add(page)
        db.flush()

        for index, chunk_text in enumerate(split_text(page_text)):
            db.add(
                Chunk(
                    document_id=doc.id,
                    page_id=page.id,
                    page_number=page_data["page_number"],
                    chunk_index=index,
                    text=chunk_text,
                )
            )
            chunk_count += 1

    # Do not mark a document as processed when no searchable text/chunks were extracted.
    doc.is_processed = chunk_count > 0
    db.flush()

    return {
        "pages": len(pages),
        "chunks": chunk_count,
        "characters": extracted_chars,
    }


@router.post("/api/documents/{document_id}/process")
def process_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    try:
        result = _extract_and_store(doc, db)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(422, f"PDF processing failed: {exc}") from exc

    status = "processed" if result["chunks"] > 0 else "no_text"
    return {
        "document_id": doc.id,
        **result,
        "status": status,
        "message": (
            "Text extracted successfully."
            if result["chunks"] > 0
            else "No selectable text was extracted. This PDF may be scanned and needs OCR."
        ),
    }


@router.post("/api/cases/{case_id}/documents/process-all")
def process_all(case_id: int, db: Session = Depends(get_db)):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")

    # Retry documents with zero chunks even if an older run incorrectly marked them processed.
    docs = (
        db.query(Document)
        .filter_by(case_id=case_id)
        .order_by(Document.id)
        .all()
    )

    results = []
    for doc in docs:
        existing_chunks = db.query(Chunk).filter_by(document_id=doc.id).count()
        if doc.is_processed and existing_chunks > 0:
            results.append({
                "document_id": doc.id,
                "filename": doc.filename,
                "status": "already_processed",
                "pages": len(doc.pages),
                "chunks": existing_chunks,
            })
            continue

        try:
            result = _extract_and_store(doc, db)
            db.commit()
            status = "processed" if result["chunks"] > 0 else "no_text"
            results.append({
                "document_id": doc.id,
                "filename": doc.filename,
                **result,
                "status": status,
                **({
                    "error": "No selectable text found; scanned PDFs require OCR."
                } if result["chunks"] == 0 else {}),
            })
        except Exception as exc:
            db.rollback()
            results.append({
                "document_id": doc.id,
                "filename": doc.filename,
                "status": "failed",
                "error": str(exc),
            })

    return {"results": results}


@router.get("/api/documents/{document_id}/pages/{page_number}/image")
def page_image(document_id: int, page_number: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    try:
        with fitz.open(doc.stored_path) as pdf:
            if page_number < 1 or page_number > len(pdf):
                raise HTTPException(404, "Page not found")
            pix = pdf[page_number - 1].get_pixmap(
                matrix=fitz.Matrix(1.5, 1.5),
                alpha=False,
            )
            data = pix.tobytes("png")
        return Response(content=data, media_type="image/png")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    path = doc.stored_path
    db.delete(doc)
    db.commit()

    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

    return {
        "message": "Document and dependent extracted data, citations and highlights deleted"
    }

@router.get("/api/cases/{case_id}/documents/{document_id}/file")
def get_original_pdf(
    case_id: int,
    document_id: int,
    db: Session = Depends(get_db),
):
    doc = db.get(Document, document_id)

    if not doc or doc.case_id != case_id:
        raise HTTPException(404, "Document not found")

    if not os.path.exists(doc.stored_path):
        raise HTTPException(404, "PDF file not found")

    return FileResponse(
        path=doc.stored_path,
        media_type="application/pdf",
        filename=doc.filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )
