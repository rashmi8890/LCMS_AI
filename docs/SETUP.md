# Setup, API and engineering notes

## Docker
```bash
copy .env.example .env
# Set LLM_API_KEY in .env
docker compose up --build
```
Windows PowerShell equivalent for copying:
```powershell
Copy-Item .env.example .env
```

## Local backend
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
For local SQLite testing set `DATABASE_URL=sqlite:///./lcms.db`.

## Frontend
```bash
cd frontend
npm install
npm run dev
```

## Main workflow
1. Create or select a case.
2. Select multiple PDFs in the upload picker. Endpoint: `POST /api/cases/{case_id}/documents/batch` with repeated `files` form fields.
3. Click Process all pending PDFs. Endpoint: `POST /api/cases/{case_id}/documents/process-all`.
4. Click Analyse case. Endpoint: `POST /api/cases/{case_id}/analyse`.
5. Each analysis paragraph is stored as an `AnalysisItem`; each supporting source is a `Citation`; each located rectangle is a `HighlightSegment`.
6. Click a citation chip. The PDF page image is displayed with all corresponding highlight rectangles.
7. Generate/edit/save notesheet and export DOCX/PDF.

## Citation/highlight data design
- One analysis paragraph can have N citation records.
- One source chunk can be cited by many analysis paragraphs.
- One citation can own N highlight segments.
- Quote text and PDF coordinates are persisted separately for every highlight.
- The LLM returns only chunk IDs and verbatim quote text. Backend checks case ownership and computes coordinates with PyMuPDF `Page.search_for`; it never trusts model-supplied coordinates.

## API routes
- `POST/GET /api/cases`, `GET/PATCH/DELETE /api/cases/{id}`
- `POST /api/cases/{id}/documents/batch`
- `GET /api/cases/{id}/documents`
- `POST /api/documents/{id}/process`
- `POST /api/cases/{id}/documents/process-all`
- `GET /api/documents/{id}/pages/{page}/image`
- `DELETE /api/documents/{id}`
- `POST /api/cases/{id}/analyse`
- `GET /api/cases/{id}/analysis-items`
- `POST /api/cases/{id}/analysis-items`
- `POST/GET/PUT /api/cases/{id}/notesheet[/generate]`
- `GET /api/cases/{id}/notesheet/export/docx|pdf`

## Important implementation constraints
- `Base.metadata.create_all()` is only a development bootstrap. Existing database schemas need Alembic migrations before deployment.
- The model context is capped by `MAX_CONTEXT_CHUNKS`; current retrieval is a simple lexical baseline, not semantic vector search.
- OCR is not bundled in this build. Scanned pages need OCR text plus word/line bounding boxes before dependable highlight rendering.
- Do not use AI-generated legal conclusions without review. Preserve source references and have an authorized officer verify the original PDF.
