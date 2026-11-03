from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import Base, engine
from .config import settings
from .routers import cases, documents, analysis, notesheets

Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
Path(settings.export_dir).mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)  
app = FastAPI(title="LCMS API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(cases.router)
app.include_router(documents.router)
app.include_router(analysis.router)
app.include_router(notesheets.router)

@app.get("/")
def root(): return {"name":"LCMS API","version":"0.2.0","docs":"/docs"}
@app.get("/health")
def health(): return {"status":"ok"}
