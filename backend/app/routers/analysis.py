# from fastapi import APIRouter, Depends, HTTPException
# from sqlalchemy.orm import Session, joinedload
# from ..database import get_db
# from ..models import Case, AnalysisItem, Citation, HighlightSegment, Chunk
# from ..schemas import AnalysisItemCreate
# from ..services.analysis_service import generate_analysis
# from ..services.pdf_service import find_highlight_rects

# router = APIRouter(tags=["analysis"])

# def serialize_item(item):
#     out = {"id": item.id, "case_id": item.case_id, "section": item.section,
#            "item_order": item.item_order, "content": item.content, "citations": []}
#     for c in item.citations:
#         ch, doc = c.chunk, c.chunk.document
#         out["citations"].append({"id": c.id, "chunk_id": ch.id, "relevance_note": c.relevance_note,
#             "document_id": doc.id, "document_name": doc.filename, "page_number": ch.page_number,
#             "source_text": ch.text, "highlights": [{"id": h.id, "page_number": h.page_number,
#             "matched_text": h.matched_text, "x0": h.x0, "y0": h.y0, "x1": h.x1, "y1": h.y1,
#             "page_width": h.page_width, "page_height": h.page_height, "segment_order": h.segment_order}
#             for h in c.highlights]})
#     return out

# def _load_item(db, item_id):
#     return db.query(AnalysisItem).options(
#         joinedload(AnalysisItem.citations).joinedload(Citation.chunk).joinedload(Chunk.document),
#         joinedload(AnalysisItem.citations).joinedload(Citation.highlights)
#     ).filter_by(id=item_id).first()

# @router.get("/api/cases/{case_id}/analysis-status")
# def get_analysis_status(case_id: int, db: Session = Depends(get_db)):
#     case = db.get(Case, case_id)
#     if not case: raise HTTPException(404, "Case not found")
#     docs = list(case.documents)
#     return {"case_id": case_id, "document_count": len(docs),
#             "processed_document_count": sum(bool(d.is_processed) for d in docs),
#             "chunk_count": sum(len(d.chunks) for d in docs),
#             "ready": any(d.is_processed and d.chunks for d in docs)}

# @router.post("/api/cases/{case_id}/analyse")
# def analyse_case(case_id: int, db: Session = Depends(get_db)):
#     if not db.get(Case, case_id): raise HTTPException(404, "Case not found")
#     try: generated = generate_analysis(db, case_id)
#     except ValueError as exc: raise HTTPException(400, str(exc))
#     except Exception as exc: raise HTTPException(502, f"Analysis generation failed: {exc}")
#     # Replace the prior run atomically so stale analysis/citations cannot leak between cases.
#     for old_item in db.query(AnalysisItem).filter_by(case_id=case_id).all():
#         db.delete(old_item)
#     db.flush()
#     created_ids = []
#     try:
#         for order, raw in enumerate(generated):
#             content = str(raw.get("content", "")).strip()
#             if not content: continue
#             item = AnalysisItem(case_id=case_id, section=str(raw.get("section", "Analysis")),
#                                 item_order=order, content=content)
#             db.add(item); db.flush()
#             for evidence in raw.get("evidence", []):
#                 chunk = db.get(Chunk, int(evidence.get("chunk_id", 0)))
#                 if not chunk or chunk.document.case_id != case_id:
#                     continue
#                 quote = str(evidence.get("quote", "")).strip()
#                 if not quote: continue
#                 citation = Citation(analysis_item_id=item.id, chunk_id=chunk.id,
#                                     relevance_note=str(evidence.get("note", ""))[:2000])
#                 db.add(citation); db.flush()
#                 rects = find_highlight_rects(chunk.document.stored_path, chunk.page_number, quote)
#                 for idx, r in enumerate(rects):
#                     citation.highlights.append(HighlightSegment(
#                         page_number=chunk.page_number, matched_text=quote,
#                         x0=r["x0"], y0=r["y0"], x1=r["x1"], y1=r["y1"],
#                         page_width=r["page_width"], page_height=r["page_height"], segment_order=idx))
#             created_ids.append(item.id)
#         db.commit()
#     except Exception as exc:
#         db.rollback()
#         raise HTTPException(500, f"Could not save analysis and citations: {exc}")
#     return {"message": "Analysis generated", "item_count": len(created_ids),
#             "items": [serialize_item(_load_item(db, i)) for i in created_ids]}

# @router.post("/api/cases/{case_id}/analysis-items")
# def create_analysis_item(case_id: int, payload: AnalysisItemCreate, db: Session = Depends(get_db)):
#     if not db.get(Case, case_id): raise HTTPException(404, "Case not found")
#     item = AnalysisItem(case_id=case_id, section=payload.section, item_order=payload.item_order, content=payload.content)
#     db.add(item); db.flush()
#     for ci in payload.citations:
#         chunk = db.get(Chunk, ci.chunk_id)
#         if not chunk or chunk.document.case_id != case_id: raise HTTPException(400, "Citation source is outside this case")
#         citation = Citation(analysis_item_id=item.id, chunk_id=chunk.id, relevance_note=ci.relevance_note)
#         db.add(citation); db.flush()
#         for hi in ci.highlights: citation.highlights.append(HighlightSegment(**hi.model_dump()))
#     db.commit()
#     return serialize_item(_load_item(db, item.id))

# @router.get("/api/cases/{case_id}/analysis-items")
# def list_analysis_items(case_id: int, db: Session = Depends(get_db)):
#     if not db.get(Case, case_id): raise HTTPException(404, "Case not found")
#     rows = db.query(AnalysisItem).filter_by(case_id=case_id).order_by(AnalysisItem.item_order).all()
#     return [serialize_item(_load_item(db, x.id)) for x in rows]

# @router.get("/api/citations/{citation_id}")
# def get_citation(citation_id: int, db: Session = Depends(get_db)):
#     c = db.query(Citation).options(joinedload(Citation.chunk).joinedload(Chunk.document),
#                                    joinedload(Citation.highlights)).filter_by(id=citation_id).first()
#     if not c: raise HTTPException(404, "Citation not found")
#     return {"id": c.id, "document_id": c.chunk.document_id, "document_name": c.chunk.document.filename,
#             "page_number": c.chunk.page_number, "source_text": c.chunk.text,
#             "highlights": [{"id": h.id, "page_number": h.page_number, "matched_text": h.matched_text,
#             "x0": h.x0, "y0": h.y0, "x1": h.x1, "y1": h.y1, "page_width": h.page_width,
#             "page_height": h.page_height} for h in c.highlights]}


from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..models import Case, AnalysisItem, Citation, HighlightSegment, Chunk
from ..schemas import AnalysisItemCreate
from ..services.analysis_service import generate_analysis
from ..services.pdf_service import find_highlight_rects

router = APIRouter(tags=["analysis"])


def serialize_item(item):
    out = {
        "id": item.id,
        "case_id": item.case_id,
        "section": item.section,
        "item_order": item.item_order,
        "content": item.content,
        "event_date": getattr(item, "event_date", None),
        "citations": [],
    }
    for c in item.citations:
        ch, doc = c.chunk, c.chunk.document
        out["citations"].append({
            "id": c.id,
            "chunk_id": ch.id,
            "relevance_note": c.relevance_note,
            "document_id": doc.id,
            "document_name": doc.filename,
            "page_number": ch.page_number,
            "source_text": ch.text,
            "highlights": [{
                "id": h.id,
                "page_number": h.page_number,
                "matched_text": h.matched_text,
                "x0": h.x0, "y0": h.y0, "x1": h.x1, "y1": h.y1,
                "page_width": h.page_width,
                "page_height": h.page_height,
                "segment_order": h.segment_order,
            } for h in c.highlights],
        })
    return out


def _load_item(db, item_id):
    return db.query(AnalysisItem).options(
        joinedload(AnalysisItem.citations).joinedload(Citation.chunk).joinedload(Chunk.document),
        joinedload(AnalysisItem.citations).joinedload(Citation.highlights),
    ).filter_by(id=item_id).first()


@router.get("/api/cases/{case_id}/analysis-status")
def get_analysis_status(case_id: int, db: Session = Depends(get_db)):
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    docs = list(case.documents)
    return {
        "case_id": case_id,
        "document_count": len(docs),
        "processed_document_count": sum(bool(d.is_processed) for d in docs),
        "chunk_count": sum(len(d.chunks) for d in docs),
        "ready": any(d.is_processed and d.chunks for d in docs),
    }


@router.post("/api/cases/{case_id}/analyse")
def analyse_case(case_id: int, db: Session = Depends(get_db)):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    try:
        generated = generate_analysis(db, case_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Analysis generation failed: {exc}")

    # Replace only this case's previous analysis. Cascades remove its old citations
    # and highlight segments; other cases remain untouched.
    try:
        for old_item in db.query(AnalysisItem).filter_by(case_id=case_id).all():
            db.delete(old_item)
        db.flush()

        created_ids = []
        for order, raw in enumerate(generated):
            content = str(raw.get("content", "")).strip()
            if not content:
                continue

            section = str(raw.get("section", "Analysis")).strip() or "Analysis"
            event_date = str(raw.get("event_date") or "").strip() or None
            if section.casefold() in {"chronology", "timeline"}:
                section = "Chronology"
                if not event_date:
                    # Do not save undated entries as chronology.
                    continue
                event_date = event_date[:50]
            else:
                event_date = None

            item = AnalysisItem(
                case_id=case_id,
                section=section,
                item_order=order,
                content=content,
                event_date=event_date,
            )
            db.add(item)
            db.flush()

            for evidence in raw.get("evidence", []) or []:
                if not isinstance(evidence, dict):
                    continue
                try:
                    chunk_id = int(evidence.get("chunk_id", 0))
                except (TypeError, ValueError):
                    continue
                chunk = db.get(Chunk, chunk_id)
                if not chunk or chunk.document.case_id != case_id:
                    continue

                quote = str(evidence.get("quote", "")).strip()
                if not quote:
                    continue

                citation = Citation(
                    analysis_item_id=item.id,
                    chunk_id=chunk.id,
                    relevance_note=str(evidence.get("note", ""))[:2000],
                )
                db.add(citation)
                db.flush()

                try:
                    rects = find_highlight_rects(
                        chunk.document.stored_path, chunk.page_number, quote
                    )
                except Exception:
                    # Keep the valid citation even if a PDF highlight cannot be located.
                    rects = []

                for idx, rect in enumerate(rects):
                    citation.highlights.append(HighlightSegment(
                        page_number=chunk.page_number,
                        matched_text=quote,
                        x0=rect["x0"], y0=rect["y0"],
                        x1=rect["x1"], y1=rect["y1"],
                        page_width=rect["page_width"],
                        page_height=rect["page_height"],
                        segment_order=idx,
                    ))

            # Do not persist an unsupported item with no valid source citation.
            if not item.citations:
                db.delete(item)
                db.flush()
                continue

            created_ids.append(item.id)

        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"Could not save analysis and citations: {exc}")

    return {
        "message": "Analysis generated",
        "item_count": len(created_ids),
        "chronology_count": sum(
            1 for item_id in created_ids
            if (_load_item(db, item_id).section or "").casefold() == "chronology"
        ),
        "items": [serialize_item(_load_item(db, item_id)) for item_id in created_ids],
    }


@router.post("/api/cases/{case_id}/analysis-items")
def create_analysis_item(case_id: int, payload: AnalysisItemCreate, db: Session = Depends(get_db)):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    section = payload.section.strip() or "General"
    event_date = (payload.event_date or "").strip() or None
    if section.casefold() in {"chronology", "timeline"}:
        section = "Chronology"
        if not event_date:
            raise HTTPException(400, "Chronology items require an explicitly supported event_date.")
    else:
        event_date = None

    item = AnalysisItem(
        case_id=case_id,
        section=section,
        item_order=payload.item_order,
        content=payload.content,
        event_date=event_date[:50] if event_date else None,
    )
    db.add(item)
    db.flush()

    for ci in payload.citations:
        chunk = db.get(Chunk, ci.chunk_id)
        if not chunk or chunk.document.case_id != case_id:
            db.rollback()
            raise HTTPException(400, "Citation source is outside this case")
        citation = Citation(
            analysis_item_id=item.id,
            chunk_id=chunk.id,
            relevance_note=ci.relevance_note,
        )
        db.add(citation)
        db.flush()
        for hi in ci.highlights:
            citation.highlights.append(HighlightSegment(**hi.model_dump()))

    if section.casefold() == "chronology" and not payload.citations:
        db.rollback()
        raise HTTPException(400, "Chronology items require at least one source citation.")

    db.commit()
    return serialize_item(_load_item(db, item.id))


@router.get("/api/cases/{case_id}/analysis-items")
def list_analysis_items(case_id: int, db: Session = Depends(get_db)):
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    rows = (
        db.query(AnalysisItem)
        .filter_by(case_id=case_id)
        .order_by(AnalysisItem.item_order)
        .all()
    )
    return [serialize_item(_load_item(db, row.id)) for row in rows]


@router.get("/api/cases/{case_id}/chronology")
def get_case_chronology(case_id: int, db: Session = Depends(get_db)):
    """Return only this case's explicitly dated chronology events."""
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    rows = (
        db.query(AnalysisItem)
        .filter(
            AnalysisItem.case_id == case_id,
            AnalysisItem.section == "Chronology",
            AnalysisItem.event_date.isnot(None),
        )
        .order_by(AnalysisItem.item_order)
        .all()
    )
    events = [serialize_item(_load_item(db, row.id)) for row in rows]
    # Sort ISO dates chronologically where possible. Preserve source date text.
    # For non-ISO dates, retain their original analysis order rather than guessing.
    from datetime import date
    def sort_key(event):
        value = event.get("event_date") or ""
        try:
            return (0, date.fromisoformat(value).isoformat(), event["item_order"])
        except ValueError:
            return (1, "", event["item_order"])
    events.sort(key=sort_key)
    return {"case_id": case_id, "count": len(events), "events": events}


@router.get("/api/citations/{citation_id}")
def get_citation(citation_id: int, db: Session = Depends(get_db)):
    c = db.query(Citation).options(
        joinedload(Citation.chunk).joinedload(Chunk.document),
        joinedload(Citation.highlights),
    ).filter_by(id=citation_id).first()
    if not c:
        raise HTTPException(404, "Citation not found")
    return {
        "id": c.id,
        "document_id": c.chunk.document_id,
        "document_name": c.chunk.document.filename,
        "page_number": c.chunk.page_number,
        "source_text": c.chunk.text,
        "highlights": [{
            "id": h.id, "page_number": h.page_number,
            "matched_text": h.matched_text,
            "x0": h.x0, "y0": h.y0, "x1": h.x1, "y1": h.y1,
            "page_width": h.page_width, "page_height": h.page_height,
        } for h in c.highlights],
    }
