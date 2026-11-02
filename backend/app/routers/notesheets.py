from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from docx import Document as DocxDocument
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from ..database import get_db
from ..config import settings
from ..models import Case, AnalysisItem, NoteSheet, NoteSheetSection
from ..schemas import NoteSheetSave

router = APIRouter(prefix="/api/cases/{case_id}/notesheet", tags=["notesheet"])

def serialize(sheet):
    return {"id": sheet.id, "case_id": sheet.case_id, "title": sheet.title,
            "updated_at": sheet.updated_at.isoformat(),
            "sections": [{"id": s.id, "heading": s.heading, "content": s.content, "position": s.position}
                         for s in sheet.sections]}

@router.post("/generate")
def generate_notesheet(case_id: int, db: Session = Depends(get_db)):
    case = db.get(Case, case_id)
    if not case: raise HTTPException(404, "Case not found")
    items = db.query(AnalysisItem).filter_by(case_id=case_id).order_by(AnalysisItem.item_order).all()
    if not items: raise HTTPException(400, "Run case analysis before generating a notesheet.")
    grouped = {}
    for item in items: grouped.setdefault(item.section, []).append(item.content)
    sections = [
        ("Case Details", f"Case Number: {case.case_number}\nSubject: {case.subject or 'Not recorded'}\nCase Type: {case.case_type or 'Not recorded'}\nAct/Sections: {case.act or 'Not recorded'} / {case.sections or 'Not recorded'}\nApplicant: {case.applicant or 'Not recorded'}\nRespondent: {case.respondent or 'Not recorded'}"),
        ("Document-Based Analysis", "\n\n".join(f"[{heading}]\n" + "\n".join(values) for heading, values in grouped.items())),
        ("Review / Decision", "To be completed by the competent officer after verification of the original records.")
    ]
    sheet = db.query(NoteSheet).filter_by(case_id=case_id).first()
    if sheet:
        sheet.title = f"Note Sheet - {case.case_number}"
        sheet.sections.clear()
    else:
        sheet = NoteSheet(case_id=case_id, title=f"Note Sheet - {case.case_number}")
        db.add(sheet); db.flush()
    for pos, (heading, content) in enumerate(sections):
        sheet.sections.append(NoteSheetSection(heading=heading, content=content, position=pos))
    db.commit(); db.refresh(sheet)
    return serialize(sheet)

@router.get("")
def get_notesheet(case_id: int, db: Session = Depends(get_db)):
    sheet = db.query(NoteSheet).filter_by(case_id=case_id).first()
    if not sheet: raise HTTPException(404, "Notesheet not generated")
    return serialize(sheet)

@router.put("")
def save_notesheet(case_id: int, payload: NoteSheetSave, db: Session = Depends(get_db)):
    if not db.get(Case, case_id): raise HTTPException(404, "Case not found")
    sheet = db.query(NoteSheet).filter_by(case_id=case_id).first()
    if not sheet:
        sheet = NoteSheet(case_id=case_id, title=payload.title); db.add(sheet); db.flush()
    sheet.title = payload.title
    sheet.sections.clear()
    for s in payload.sections:
        sheet.sections.append(NoteSheetSection(heading=s.heading, content=s.content, position=s.position))
    db.commit(); db.refresh(sheet)
    return serialize(sheet)

@router.get("/export/docx")
def export_docx(case_id: int, db: Session = Depends(get_db)):
    sheet = db.query(NoteSheet).filter_by(case_id=case_id).first()
    if not sheet: raise HTTPException(404, "Generate or save notesheet first")
    outdir = Path(settings.export_dir); outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"notesheet_case_{case_id}.docx"
    doc = DocxDocument(); doc.add_heading(sheet.title, 0)
    for s in sheet.sections:
        doc.add_heading(s.heading, level=1)
        for para in s.content.split("\n"):
            if para.strip(): doc.add_paragraph(para)
    doc.save(path)
    return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

@router.get("/export/pdf")
def export_pdf(case_id: int, db: Session = Depends(get_db)):
    sheet = db.query(NoteSheet).filter_by(case_id=case_id).first()
    if not sheet: raise HTTPException(404, "Generate or save notesheet first")
    outdir = Path(settings.export_dir); outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"notesheet_case_{case_id}.pdf"
    styles = getSampleStyleSheet(); story = [Paragraph(sheet.title, styles["Title"]), Spacer(1, 16)]
    for s in sheet.sections:
        story.append(Paragraph(s.heading, styles["Heading2"]))
        for para in s.content.split("\n"):
            if para.strip(): story.append(Paragraph(para.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"), styles["BodyText"]))
        story.append(Spacer(1, 10))
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return FileResponse(path, filename=path.name, media_type="application/pdf")
