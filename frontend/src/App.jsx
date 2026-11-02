import { useEffect, useMemo, useState } from "react";
import "./style.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  if (!response.ok) {
    let message = await response.text();
    try {
      const parsed = JSON.parse(message);
      message = parsed.detail || message;
    } catch {}
    throw new Error(message || `Request failed (${response.status})`);
  }
  if (response.status === 204) return null;
  return response.json();
}

const TAB_CONFIG = [
  { id: "overview", label: "Overview" },
  { id: "issues", label: "Issues" },
  { id: "parties", label: "Parties" },
  { id: "evidence", label: "Evidence" },
  { id: "chronology", label: "Chronology" },
  { id: "law", label: "Law" },
  { id: "sources", label: "Sources" },
];

function matchesSection(item, terms) {
  const section = String(item.section || "").toLowerCase();
  return terms.some((term) => section.includes(term));
}

function CitationChips({ citations = [], onOpen, activeCitation }) {
  if (!citations.length) return <span className="no-citation">No source linked</span>;
  return (
    <div className="citation-chips">
      {citations.map((citation, index) => (
        <button
          type="button"
          className={`citation-chip ${String(activeCitation?.id) === String(citation.id) ? "active" : ""}`}
          key={citation.id || `${citation.document_id}-${index}`}
          onClick={() => onOpen(citation)}
        >
          <strong>[{index + 1}] {citation.document_name || "Source document"}</strong>
          <span>Page {citation.page_number ?? "—"} · {(citation.highlights || []).length} highlight(s)</span>
        </button>
      ))}
    </div>
  );
}

function FindingCard({ item, onCitation, activeCitation, caseId }) {
  return (
    <article className="finding-card">
      <div className="finding-meta">
        <span className="eyebrow">{item.section || "Analysis"}</span>
        <span className="muted">Paragraph #{(item.item_order ?? 0) + 1}</span>
      </div>
      <p className="finding-content">{item.content}</p>
      <CitationChips
        citations={item.citations || []}
        onOpen={onCitation}
        activeCitation={activeCitation}
        caseId={caseId}
      />
    </article>
  );
}

function Metric({ label, value, hint }) {
  return (
    <div className="metric-card">
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </div>
  );
}

export default function App() {
  const [cases, setCases] = useState([]);
  const [selected, setSelected] = useState(null);
  const [docs, setDocs] = useState([]);
  const [items, setItems] = useState([]);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState("info");
  const [busy, setBusy] = useState(false);
  const [newCase, setNewCase] = useState({ case_number: "", subject: "" });
  const [editOpen, setEditOpen] = useState(false);
  const [caseForm, setCaseForm] = useState({});
  const [activeTab, setActiveTab] = useState("overview");
  const [citation, setCitation] = useState(null);
  const [sheet, setSheet] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const facts = useMemo(() => items.filter((x) => matchesSection(x, ["fact", "background"])), [items]);
  const issues = useMemo(() => items.filter((x) => matchesSection(x, ["issue", "dispute"])), [items]);
  const contradictions = useMemo(() => items.filter((x) => matchesSection(x, ["contradiction", "conflict", "discrepancy"])), [items]);
  const chronology = useMemo(() => items.filter((x) => matchesSection(x, ["chronology", "timeline"])), [items]);
  const evidence = useMemo(() => items.filter((x) => matchesSection(x, ["evidence", "documentary"])), [items]);
  const parties = useMemo(() => items.filter((x) => matchesSection(x, ["parties", "applicant", "respondent"])), [items]);
  const law = useMemo(() => items.filter((x) => matchesSection(x, ["law", "legal provision", "act", "section"])), [items]);
  const allCitations = useMemo(
    () => items.flatMap((item) => (item.citations || []).map((c, index) => ({
      ...c, paragraph: item.content, paragraphSection: item.section, citationIndex: index + 1,
    }))),
    [items]
  );
  const uniqueSourceDocuments = useMemo(() => {
    const grouped = new Map();
    allCitations.forEach((source) => {
      const key = String(source.document_id);
      if (!grouped.has(key)) grouped.set(key, { ...source, relatedCitations: [] });
      grouped.get(key).relatedCitations.push(source);
    });
    return Array.from(grouped.values());
  }, [allCitations]);
  const readyDocs = docs.filter((doc) => doc.is_processed && Number(doc.chunk_count || 0) > 0).length;

  function notify(text, type = "info") {
    setMessage(text);
    setMessageType(type);
  }

  async function refreshCases() {
    try {
      const result = await api("/api/cases");
      setCases(Array.isArray(result) ? result : []);
    } catch (error) {
      notify(error.message, "error");
    }
  }

  async function loadCase(caseRecord) {
    setSelected(caseRecord);
    setCitation(null);
    setSheet(null);
    setActiveTab("overview");
    try {
      const [documents, analysis] = await Promise.all([
        api(`/api/cases/${caseRecord.id}/documents`),
        api(`/api/cases/${caseRecord.id}/analysis-items`),
      ]);
      setDocs(Array.isArray(documents) ? documents : []);
      setItems(Array.isArray(analysis) ? analysis : []);
    } catch (error) {
      notify(error.message, "error");
    }
  }

  useEffect(() => { refreshCases(); }, []);

  async function createCase(event) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await api("/api/cases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newCase),
      });
      setNewCase({ case_number: "", subject: "" });
      await refreshCases();
      await loadCase(created);
      notify("Case created successfully.", "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function uploadFiles(event) {
    const files = [...(event.target.files || [])];
    if (!files.length || !selected) return;
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    setBusy(true);
    try {
      const result = await api(`/api/cases/${selected.id}/documents/batch`, { method: "POST", body: form });
      await loadCase(selected);
      notify(`${result.uploaded_count ?? files.length} PDF(s) uploaded${result.failed_count ? `; ${result.failed_count} failed` : ""}.`, result.failed_count ? "warning" : "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function processAndAnalyse() {
    if (!selected || !docs.length) return;
    setBusy(true);
    try {
      const needProcessing = docs.some((doc) => !doc.is_processed || Number(doc.chunk_count || 0) === 0);
      if (needProcessing) {
        notify("Extracting text from case PDFs…");
        const processResult = await api(`/api/cases/${selected.id}/documents/process-all`, { method: "POST" });
        const updatedDocs = await api(`/api/cases/${selected.id}/documents`);
        setDocs(updatedDocs);
        const usable = updatedDocs.some((doc) => doc.is_processed && Number(doc.chunk_count || 0) > 0);
        if (!usable) {
          const errors = (processResult.results || []).map((x) => `${x.filename}: ${x.error || x.status}`).join(" | ");
          throw new Error(errors || "No selectable text was extracted. Scanned PDFs may require OCR.");
        }
      }
      notify("Generating case analysis and source citations…");
      const result = await api(`/api/cases/${selected.id}/analyse`, { method: "POST" });
      setItems(result.items || await api(`/api/cases/${selected.id}/analysis-items`));
      setCitation(null);
      setSheet(null);
      setActiveTab("overview");
      const count = result.item_count ?? result.items?.length ?? 0;
      notify(`Analysis completed. ${count} paragraphs generated.`, "success");
      const updatedDocs = await api(`/api/cases/${selected.id}/documents`);
      setDocs(updatedDocs);
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function removeDocument(documentId) {
    if (!window.confirm("Remove this PDF and its extracted text, citations and highlights?")) return;
    setBusy(true);
    try {
      await api(`/api/documents/${documentId}`, { method: "DELETE" });
      await loadCase(selected);
      notify("Document removed.", "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function saveCase() {
    setBusy(true);
    try {
      const updated = await api(`/api/cases/${selected.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(caseForm),
      });
      setEditOpen(false);
      await refreshCases();
      await loadCase(updated);
      notify("Case details updated.", "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function generateSheet() {
    setBusy(true);
    try {
      const generated = await api(`/api/cases/${selected.id}/notesheet/generate`, { method: "POST" });
      setSheet(generated);
      notify("Notesheet generated. Review and edit it before export.", "success");
      setActiveTab("notesheet");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function loadSheet() {
    try {
      setSheet(await api(`/api/cases/${selected.id}/notesheet`));
    } catch (error) {
      notify(error.message, "error");
    }
  }

  async function saveSheet() {
    setBusy(true);
    try {
      const saved = await api(`/api/cases/${selected.id}/notesheet`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: sheet.title,
          sections: sheet.sections.map((section, index) => ({
            heading: section.heading, content: section.content, position: index,
          })),
        }),
      });
      setSheet(saved);
      notify("Notesheet saved.", "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function updateSection(index, key, value) {
    setSheet((current) => ({
      ...current,
      sections: current.sections.map((section, i) => i === index ? { ...section, [key]: value } : section),
    }));
  }

  function moveSection(index, direction) {
    setSheet((current) => {
      const sections = [...current.sections];
      const target = index + direction;
      if (target < 0 || target >= sections.length) return current;
      [sections[index], sections[target]] = [sections[target], sections[index]];
      return { ...current, sections };
    });
  }

  function exportNotesheet(format) {
    window.open(`${API}/api/cases/${selected.id}/notesheet/export/${format}`, "_blank", "noopener,noreferrer");
  }

  function openCitation(source) {
    setCitation(source);
  }

  function renderFindings(list, emptyText) {
    return list.length
      ? <div className="finding-list">{list.map((item) => (
          <FindingCard
            key={item.id}
            item={item}
            onCitation={openCitation}
            activeCitation={citation}
            caseId={selected?.id}
          />
        ))}</div>
      : <div className="empty-panel"><strong>Nothing to display yet</strong><p>{emptyText}</p></div>;
  }

  return (
    <div className="lcms-app">
      <header className="topbar">
        <button className="mobile-menu" onClick={() => setSidebarOpen((value) => !value)} aria-label="Toggle cases panel">☰</button>
        <div className="brand">
          <div className="brand-name">LCMS AI</div>
          <div className="brand-subtitle">Labour Case Analysis &amp; Note-Sheet Assistant</div>
        </div>
        <div className="workspace-status"><span className="status-dot" /> Local workspace</div>
      </header>

      {message && (
        <div className={`toast toast-${messageType}`} role="status">
          <span>{message}</span>
          <button onClick={() => setMessage("")} aria-label="Dismiss notification">×</button>
        </div>
      )}

      <div className="app-shell">
        <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : "sidebar-closed"}`}>
          <div className="sidebar-heading">
            <div><h2>Cases <span>{cases.length}</span></h2><p>Manage your case files</p></div>
          </div>
          <form className="create-case-form" onSubmit={createCase}>
            <input required placeholder="New case number" value={newCase.case_number} onChange={(e) => setNewCase({ ...newCase, case_number: e.target.value })} />
            <input placeholder="Case subject (optional)" value={newCase.subject} onChange={(e) => setNewCase({ ...newCase, subject: e.target.value })} />
            <button className="button button-primary button-full" disabled={busy}>+ Create Case</button>
          </form>
          <div className="case-list">
            {cases.map((record) => (
              <button key={record.id} className={`case-list-item ${selected?.id === record.id ? "selected" : ""}`} onClick={() => loadCase(record)}>
                <strong>{record.case_number}</strong>
                <span>{record.case_type || "Labour case"}</span>
                <small>{record.subject || "No subject provided"}</small>
              </button>
            ))}
            {!cases.length && <div className="sidebar-empty">No cases created yet.</div>}
          </div>
          <div className="sidebar-footer">LCMS AI · Local workspace</div>
        </aside>

        <main className="main-content">
          {!selected ? (
            <section className="welcome-panel">
              <span className="eyebrow">CASE WORKSPACE</span>
              <h1>Welcome to LCMS AI</h1>
              <p>Create a case or select one from the left panel to upload documents, analyse evidence and prepare a notesheet.</p>
            </section>
          ) : (
            <>
              <section className="case-hero">
                <div className="case-identity">
                  <span className="eyebrow">CASE {selected.id}</span>
                  <h1>{selected.case_number}</h1>
                  <p>{selected.applicant || "Applicant"} <span className="versus">vs</span> {selected.respondent || selected.subject || "Respondent / Organisation"}</p>
                  <div className="case-tags">
                    {selected.case_type && <span className="tag">{selected.case_type}</span>}
                    <span className="tag tag-status">{selected.status || "Pending"}</span>
                  </div>
                </div>
                <div className="hero-actions">
                  <button className="button button-soft" onClick={() => { setCaseForm({ ...selected }); setEditOpen((value) => !value); }}>✎ Edit Case</button>
                  <label className={`button button-soft upload-button ${busy ? "disabled" : ""}`}>＋ Upload PDFs<input type="file" accept=".pdf,application/pdf" multiple hidden disabled={busy} onChange={uploadFiles} /></label>
                  <button className="button button-primary" disabled={busy || !docs.length} onClick={processAndAnalyse}>{busy ? "Working…" : "Analyse Case"}</button>
                </div>
              </section>

              {editOpen && (
                <section className="edit-panel">
                  <div className="section-title-row"><div><h2>Edit case details</h2><p>Update the case metadata.</p></div><button className="icon-button" onClick={() => setEditOpen(false)}>×</button></div>
                  <div className="edit-grid">
                    {["case_number", "case_type", "subject", "act", "sections", "applicant", "respondent", "filing_date", "status"].map((field) => (
                      <label key={field}>{field.replaceAll("_", " ")}<input value={caseForm[field] ?? ""} onChange={(e) => setCaseForm({ ...caseForm, [field]: e.target.value })} /></label>
                    ))}
                  </div>
                  <div className="button-row"><button className="button button-primary" disabled={busy} onClick={saveCase}>Save changes</button><button className="button button-outline" onClick={() => setEditOpen(false)}>Cancel</button></div>
                </section>
              )}

              <section className="documents-panel">
                <div className="section-title-row">
                  <div><h2>Documents <span className="count-muted">{docs.length} uploaded</span></h2><p>Case files and text extraction status</p></div>
                  <div className="document-progress">{readyDocs}/{docs.length || 0} processed</div>
                </div>
                {docs.length ? (
                  <div className="document-grid">
                    {docs.map((doc) => {
                      const ready = doc.is_processed && Number(doc.chunk_count || 0) > 0;
                      return (
                        <div className="document-tile" key={doc.id}>
                          <span className="pdf-icon">PDF</span>
                          <div className="document-info"><strong title={doc.filename}>{doc.filename}</strong><small>{doc.category || "Case document"}</small></div>
                          <span className={`document-state ${ready ? "ready" : "pending"}`}>{ready ? "Ready" : "Pending"}</span>
                          <button className="remove-document" title="Remove PDF" disabled={busy} onClick={() => removeDocument(doc.id)}>×</button>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="empty-documents"><span className="empty-icon">▤</span><div><strong>No documents uploaded</strong><p>Upload one or more PDFs to begin the case analysis.</p></div></div>
                )}
                {docs.length > 0 && readyDocs < docs.length && <p className="document-hint">Select Analyse Case to process pending PDFs and analyse the case in one operation.</p>}
              </section>

              <section className="analysis-panel">
                <div className="analysis-heading">
                  <div><span className="eyebrow">AI ANALYSIS</span><h2>Case Analysis</h2><p>Click a source reference to see the exact document, page and retrieved passage.</p></div>
                  <button className="button button-primary notesheet-preview-button" onClick={() => { if (sheet) setActiveTab("notesheet"); else generateSheet(); }} disabled={!items.length || busy}>Note-Sheet Preview</button>
                </div>
                <nav className="analysis-tabs" aria-label="Analysis sections">
                  {TAB_CONFIG.map((tab) => {
                    const count = {
                      issues: issues.length, parties: parties.length, evidence: evidence.length,
                      chronology: chronology.length, law: law.length, sources: allCitations.length,
                    }[tab.id];
                    return <button key={tab.id} className={`analysis-tab ${activeTab === tab.id ? "active" : ""}`} onClick={() => setActiveTab(tab.id)}>
                      {tab.label}{tab.id !== "overview" && <span>{count ?? ""}</span>}
                    </button>;
                  })}
                </nav>

                <div className={`analysis-body ${citation && activeTab !== "sources" ? "analysis-body-with-preview" : ""}`}>
                  <div className="analysis-content-pane">
                  {activeTab === "overview" && (
                    <>
                      <div className="overview-metrics">
                        <Metric label="FINDINGS" value={items.length} hint="Analysis paragraphs" />
                        <Metric label="ISSUES" value={issues.length} hint="Questions for consideration" />
                        <Metric label="CONTRADICTIONS" value={contradictions.length} hint="Conflicting records" />
                        <Metric label="SOURCE LINKS" value={allCitations.length} hint="Traceable citations" />
                      </div>
                      <div className="overview-block"><h3>Executive Summary</h3>{renderFindings(items.filter((x) => matchesSection(x, ["summary", "executive"])).slice(0, 2), "The analysis has not returned a separate executive summary.")}</div>
                      <div className="overview-block"><h3>Case Background</h3>{renderFindings(facts.slice(0, 3), "No separately classified background facts were returned.")}</div>
                      <div className="overview-block"><h3>Unresolved Facts</h3>{renderFindings(items.filter((x) => matchesSection(x, ["missing", "unresolved"])), "No unresolved facts were identified in the returned analysis. This does not mean every fact has been established.")}</div>
                      {/* {contradictions.length > 0 && <div className="overview-block"><h3>Key Contradictions</h3>{renderFindings(contradictions.slice(0, 3), "No contradictions found.")}</div>} */}
                    </>
                  )}
                  {activeTab === "issues" && <><div className="tab-intro"><h3>Issues for consideration</h3><p>Questions and disputed points identified from the case record.</p></div>{renderFindings(issues, "No separately classified issues were returned by the analysis.")}{contradictions.length > 0 && <div className="overview-block"><h3>Related contradictions</h3>{renderFindings(contradictions, "No contradictions identified.")}</div>}</>}
                  {activeTab === "parties" && <><div className="tab-intro"><h3>Parties and participants</h3><p>Parties named in the case metadata and supporting analysis.</p></div><div className="party-grid"><div className="party-card"><span>APPLICANT</span><strong>{selected.applicant || "Not specified"}</strong></div><div className="party-card"><span>RESPONDENT</span><strong>{selected.respondent || "Not specified"}</strong></div></div>{renderFindings(parties, "No additional party-related findings were returned.")}</>}
                  {activeTab === "evidence" && <><div className="tab-intro"><h3>Evidence assessment</h3><p>Documentary evidence and its relevance to the disputed facts.</p></div>{renderFindings(evidence.length ? evidence : items.filter((x) => x.citations?.length), "No evidence assessment was returned. Review the Sources tab for all available citations.")}</>}
                  {activeTab === "chronology" && <><div className="tab-intro"><h3>Case chronology</h3><p>Date-ordered events extracted from the documents. Each event should have source support.</p></div>{chronology.length ? <div className="timeline">{[...chronology].sort((a, b) => String(a.event_date || "").localeCompare(String(b.event_date || ""))).map((item, index) => <article className="timeline-item" key={item.id}><div className="timeline-point">{index + 1}</div><div className="timeline-content"><time>{item.event_date || "Date not specified"}</time><p>{item.content}</p><CitationChips citations={item.citations || []} onOpen={openCitation} activeCitation={citation} caseId={selected?.id} /></div></article>)}</div> : <div className="empty-panel"><strong>No chronology events available</strong><p>Run Analyse Case after processing the PDFs. Only explicitly dated, source-supported events are displayed here.</p></div>}</>}
                  {activeTab === "law" && <><div className="tab-intro"><h3>Legal provisions</h3><p>Acts, sections and legal references mentioned in the case documents.</p></div><div className="case-law-meta"><span><b>Act:</b> {selected.act || "Not specified"}</span><span><b>Sections:</b> {selected.sections || "Not specified"}</span></div>{renderFindings(law, "No separately classified legal provisions were returned by the analysis.")}</>}
                  {activeTab === "sources" && (
                    <>
                      <div className="tab-intro"><h3>Source traceability</h3><p>Each PDF is listed once. Select a document, then choose one of its related citation pages.</p></div>
                      <div className={`source-workspace ${citation ? "has-selection" : ""}`}>
                        <div className="source-list-pane">
                          <div className="source-list-heading">
                            <h3>Source documents</h3>
                            <span>{uniqueSourceDocuments.length} unique PDFs · {allCitations.length} citations</span>
                          </div>
                          <div className="source-table">
                            {uniqueSourceDocuments.map((document, index) => <div className="source-document-group" key={document.document_id}>
                              <button
                                className={`source-row ${String(citation?.document_id) === String(document.document_id) ? "selected" : ""}`}
                                onClick={() => setCitation(document.relatedCitations[0])}
                              >
                                <span className="source-index">[{index + 1}]</span>
                                <span className="source-main">
                                  <strong>{document.document_name}</strong>
                                  <small>{document.relatedCitations.length} related citation(s) · Pages {[...new Set(document.relatedCitations.map((c) => c.page_number))].sort((a,b) => a-b).join(", ")}</small>
                                </span>
                                <span className="source-action">Open PDF →</span>
                              </button>
                              {String(citation?.document_id) === String(document.document_id) && (
                                <div className="document-citation-pages">
                                  {document.relatedCitations.map((related, pageIndex) => (
                                    <button
                                      type="button"
                                      key={related.id || `${related.page_number}-${pageIndex}`}
                                      className={`document-page-link ${String(citation?.id) === String(related.id) ? "active" : ""}`}
                                      onClick={() => setCitation(related)}
                                    >
                                      Page {related.page_number} · {related.paragraphSection || "Citation"} #{pageIndex + 1}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>)}
                            {!uniqueSourceDocuments.length && <div className="empty-panel"><strong>No source citations yet</strong><p>Run the case analysis to generate source-linked findings.</p></div>}
                          </div>
                        </div>
                        <div className="source-preview-pane">
                          {citation ? (
                            <div className="source-preview">
                              <div className="section-title-row">
                                <div><h3>{citation.document_name} · Page {citation.page_number}</h3><p>{citation.relevance_note || "Supporting passage"}</p></div>
                                <button className="button button-outline button-small" onClick={() => setCitation(null)}>Close</button>
                              </div>
                              <div className="pdf-viewer">
                                <iframe
                                  key={`${selected.id}-${citation.document_id}-${citation.page_number}`}
                                  src={`${API}/api/cases/${selected.id}/documents/${citation.document_id}/file#page=${citation.page_number}&zoom=page-width`}
                                  title={`Original PDF: ${citation.document_name}`}
                                  loading="lazy"
                                />
                              </div>
                              {(citation.highlights || []).map((highlight) => <blockquote className="quoted-highlight" key={highlight.id}>{highlight.matched_text}</blockquote>)}
                              {!(citation.highlights || []).length && <p className="inline-warning">The original PDF is open. No saved highlight segments were returned for this citation.</p>}
                            </div>
                          ) : (
                            <div className="source-preview-empty">
                              <div className="source-empty-icon">PDF</div>
                              <strong>Select a source to preview its PDF</strong>
                              <p>The original document will open here at the cited page.</p>
                            </div>
                          )}
                        </div>
                      </div>
                    </>
                  )}
                  </div>
                  {citation && activeTab !== "sources" && (
                    <aside className="analysis-pdf-pane">
                      <div className="analysis-pdf-sticky">
                        <div className="analysis-pdf-header">
                          <div>
                            <strong>{citation.document_name || "Source PDF"}</strong>
                            <small>Page {citation.page_number ?? "—"} · Supporting source</small>
                          </div>
                          <button type="button" className="preview-close" onClick={() => setCitation(null)} aria-label="Close PDF preview">×</button>
                        </div>
                        <iframe
                          key={`${selected.id}-${citation.document_id}-${citation.page_number}`}
                          src={`${API}/api/cases/${selected.id}/documents/${citation.document_id}/file#page=${citation.page_number}&zoom=page-width`}
                          title={`Original PDF: ${citation.document_name || "source"}`}
                          loading="lazy"
                        />
                        {(citation.highlights || []).map((highlight) => (
                          <blockquote className="quoted-highlight" key={highlight.id}>{highlight.matched_text}</blockquote>
                        ))}
                        {!(citation.highlights || []).length && (
                          <p className="inline-warning">No saved highlight segments were returned for this citation.</p>
                        )}
                      </div>
                    </aside>
                  )}
                </div>
              </section>

              {activeTab === "notesheet" && (
                <section className="notesheet-panel">
                  <div className="section-title-row"><div><span className="eyebrow">DRAFT DOCUMENT</span><h2>Editable Note-Sheet</h2><p>Edit sections, change their order, save and export.</p></div><div className="button-row"><button className="button button-soft" onClick={loadSheet}>Load saved</button><button className="button button-primary" onClick={generateSheet} disabled={!items.length || busy}>Generate</button></div></div>
                  {sheet ? <div className="notesheet-editor">
                    <input className="notesheet-title" value={sheet.title || ""} onChange={(e) => setSheet({ ...sheet, title: e.target.value })} />
                    {sheet.sections.map((section, index) => <div className="notesheet-section" key={section.id || index}>
                      <div className="notesheet-section-head"><input value={section.heading || ""} onChange={(e) => updateSection(index, "heading", e.target.value)} /><div className="button-row"><button className="button button-outline button-small" onClick={() => moveSection(index, -1)}>↑</button><button className="button button-outline button-small" onClick={() => moveSection(index, 1)}>↓</button><button className="button button-danger button-small" onClick={() => setSheet({ ...sheet, sections: sheet.sections.filter((_, i) => i !== index) })}>Remove</button></div></div>
                      <textarea rows="6" value={section.content || ""} onChange={(e) => updateSection(index, "content", e.target.value)} />
                    </div>)}
                    <div className="button-row notesheet-actions"><button className="button button-outline" onClick={() => setSheet({ ...sheet, sections: [...sheet.sections, { heading: "New Section", content: "" }] })}>+ Add section</button><button className="button button-primary" disabled={busy} onClick={saveSheet}>Save notesheet</button><button className="button button-soft" onClick={() => exportNotesheet("docx")}>Export DOCX</button><button className="button button-soft" onClick={() => exportNotesheet("pdf")}>Export PDF</button></div>
                  </div> : <div className="empty-panel"><strong>No notesheet loaded</strong><p>Generate a notesheet from the case analysis or load a previously saved version.</p><button className="button button-primary" disabled={!items.length || busy} onClick={generateSheet}>Generate Note-Sheet</button></div>}
                </section>
              )}
            </>
          )}
        </main>
      </div>
      <footer className="app-footer">LCMS AI · Review AI-generated content and verify cited passages against the original record before official use.</footer>
    </div>
  );
}
