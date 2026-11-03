import re
from ..models import Case, Chunk
from ..services.llm_service import ask_json


def retrieve_case_chunks(db, case_id: int, query: str = "", limit: int | None = None):
    terms = [t.lower().strip(".,:;()[]{}") for t in query.split() if len(t) > 2]
    rows = (
        db.query(Chunk)
        .join(Chunk.document)
        .filter(Chunk.document.has(case_id=case_id))
        .order_by(Chunk.document_id, Chunk.page_number, Chunk.chunk_index)
        .all()
    )
    if not terms:
        return rows[:limit] if limit else rows
    scored = []
    for row in rows:
        low = row.text.lower()
        score = sum(low.count(t) for t in terms)
        if score:
            scored.append((score, row))
    ordered = [r for _, r in sorted(scored, key=lambda x: x[0], reverse=True)]
    return ordered[:limit] if limit else ordered


def _normalise_for_dedupe(value: str) -> str:
    return re.sub(r"\W+", " ", (value or "").lower()).strip()


def _merge_duplicate_chronology_items(items):
    """
    Merge exact duplicate event descriptions emitted from overlapping/repeated
    document chunks. Evidence from both copies is retained for traceability.
    Similar-but-not-identical events are deliberately not merged.
    """
    merged = []
    chronology_index = {}

    for item in items:
        section = str(item.get("section", "Analysis")).strip()
        if section.casefold() not in {"chronology", "timeline"}:
            merged.append(item)
            continue

        event_date = str(item.get("event_date") or "").strip()
        content_key = _normalise_for_dedupe(str(item.get("content", "")))
        key = (event_date.casefold(), content_key)

        if not content_key:
            continue
        if key not in chronology_index:
            chronology_index[key] = len(merged)
            item["section"] = "Chronology"
            item["event_date"] = event_date or None
            item["evidence"] = list(item.get("evidence") or [])
            merged.append(item)
        else:
            existing = merged[chronology_index[key]]
            known = {
                (e.get("chunk_id"), _normalise_for_dedupe(e.get("quote", "")))
                for e in existing.get("evidence", [])
                if isinstance(e, dict)
            }
            for evidence in item.get("evidence") or []:
                if not isinstance(evidence, dict):
                    continue
                evidence_key = (
                    evidence.get("chunk_id"),
                    _normalise_for_dedupe(evidence.get("quote", "")),
                )
                if evidence_key not in known:
                    existing["evidence"].append(evidence)
                    known.add(evidence_key)

    return merged


def generate_analysis(db, case_id: int):
    case = db.get(Case, case_id)
    if not case:
        raise ValueError("Case not found")

    chunks = retrieve_case_chunks(db, case_id)
    if not chunks:
        raise ValueError("No processed text found. Upload and process at least one PDF.")
        
    system = (
        "You are a legal-administrative document analyst. Analyse only the "
        "supplied case metadata and document excerpts. Be comprehensive, "
        "neutral, precise and source-grounded. Never invent facts, dates, "
        "parties, legal provisions, citations or procedural events. "
        "Distinguish established records, allegations, claims, admissions, "
        "denials, contradictions and analytical inferences.\n\n"

        "OUTPUT: Return valid JSON only, using exactly this structure:\n"
        '{"items":[{"section":"Executive Summary","event_date":null,'
        '"content":"Detailed finding","evidence":[{"chunk_id":123,'
        '"quote":"Exact source quotation","note":"Relevance"}]}]}\n'
        "Every item must have all four fields. Use only these exact section "
        "names: Executive Summary, Case Background, Issues, " #Facts
        "Contradictions, Parties, Unresolved Facts, Evidence, Law, " #Missing Information,
        # "Analysis," 
        " Chronology, Dates. Never change the schema.\n\n"

        "CATEGORY RULES:\n"
        "Executive Summary: Concise case overview, parties, dispute, relief, "
        "key records, evidence. Do not draw "
        "complete-case conclusions from one batch.\n"
        "Case Background: Material factual and procedural history."
        "Separate material facts; attribute allegations to their makers.\n"
        # "Facts: Separate material facts; attribute allegations to their makers.\n"
        "Issues: Specific factual or legal questions requiring determination.\n"
        # "Parties: Each party's identity, role, claims, grounds, relief, "
        "Parties: For EACH party or material participant, create a separate finding."

        "Include:"
        "- Full name, designation and role in the case."
        "- What the party claims or alleges."
        "- What the party wants or what relief/action it seeks."
        "- Its grounds, reasons and justification."
        "- Its explanation or version of the events."
        "- Its response to the opposing party's allegations."
        "- Specific admissions, denials and objections."
        "- Orders, correspondence or documents relied upon."
        "- Its overall position and view regarding the dispute."

        "Clearly distinguish the applicant's position from the respondent's"
        "position. Explain what each party agrees with, disputes or contests."
        "If a party has not submitted a response in the supplied record,"
        "state that no response is present in the supplied excerpts."
        "Do not assume that silence means admission or denial."
        "Preserve important arguments, dates, order numbers and factual"
        "particulars. Attribute every statement to its actual source."
        "and Support each party's position with exact source quotations.\n"
        # "response, defence, admissions and denials where recorded.\n"

        "Evidence: Assess actual documents, their contents, relevance, "
        "support and limitations. A citation alone is not evidence.\n"
        "Contradictions: Identify both conflicting versions, their sources "
        "and the precise inconsistency. Do not confuse missing information "
        "with contradiction.\n"
       
        # "Missing Information: Identify specific material information, "
        # "documents, records, or explanations necessary to resolve those "
        # "questions but not available in the case record. Do not treat absence "
        # "from one batch or retrieved excerpt as proof that information is "
        # "missing. Check the available case material before identifying a gap. "
        # "Do not invent missing facts or assume that an allegation is true."

        "Chronology : Extract every event that has a specific or implied date/time marker."
        "Convert all dates to a unified, standard format (e.g., YYYY-MM-DD or DD Month YYYY). If a specific day is missing, use 'Month YYYY'."
        "Chronologically sort the events from the earliest date to the most recent date. Do not skip any intermediate procedural steps mentioned."
        "Provide a brief analysis of the progression (e.g., gaps in time, speed of resolution, or key turning points).\n"

        "Unresolved Facts: Identify material factual questions that remain "
        "unanswered, disputed, unverified, or unsupported by sufficient "
        "evidence in the available case record. Explain what is known, what "
        "remains uncertain, and which parties or documents are relevant. \n"

        "Law: Only explicitly identified Acts, rules, sections, regulations "
        "or legal principles. Never infer applicable law or classify facts "
        "as legal provisions.\n"
        # "Analysis: Explain relationships, evidentiary strengths, weaknesses "
        # "and implications. Label inferences clearly.\n"

        "CITATIONS: Every document-derived finding must contain at least "
        "one evidence entry. chunk_id must belong to the supplied excerpts. "
        "quote must be an exact, contiguous source quotation from that chunk. "
        "note must explain its relevance. For contradictions, cite both "
        "versions when available. Do not fabricate or mismatch citations.\n\n"

        "DETAIL: Extract all material particulars, including names, dates, "
        "amounts, order numbers, designations, reasons and document references. "
        "Split distinct findings into separate items. Avoid duplicates and "
        "unnecessary repetition. Do not omit supported material details.\n\n"

        "BATCH LIMIT: Analyse only the supplied excerpts. Preserve findings "
        "for later cross-document comparison. Do not claim complete-case "
        "contradictions, summaries or missing-information conclusions when "
        "the necessary documents are not present.\n\n"

        "Return one JSON object with an items array. Return an empty array "
        "only if no supported findings exist. No Markdown or additional prose."
    )


    all_items = []
    # Small batches preserve the existing protection against provider TPM limits.
    batch_size = 6
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset:offset + batch_size]
        context = "\n\n".join(
            f"[SOURCE chunk_id={c.id} document={c.document.filename!r} page={c.page_number}]\n{c.text}"
            for c in batch
        )
        user = (
            f"CASE METADATA\nCase number: {case.case_number}\nSubject: {case.subject}\n"
            f"Act: {case.act}\nSections: {case.sections}\nApplicant: {case.applicant}\n"
            f"Respondent: {case.respondent}\n\n"
            f"DOCUMENT EXCERPTS (batch {offset // batch_size + 1})\n{context}\n\n"
            "Extract every applicable analysis category supported by these excerpts. Return "
            "Executive Summary, Case Background, Issues, Parties, Evidence and Law " # fact , unresolved fact
            "when supported; also return Contradictions,  " #Missing Information,
            # "Analysis "
            "and explicitly dated Chronology events when supported. Do not "
            "fill a category with unrelated content. Every item must have at least one exact "
            "supporting quote from this batch. Return one JSON object."
        )
        result = ask_json(system, user)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise RuntimeError("LLM JSON must contain an items array.")
        for item in result["items"]:
            if isinstance(item, dict):
                # The API validates chunk ownership against this case before saving.
                section = str(item.get("section", "Analysis")).strip()
                if section.casefold() in {"chronology", "timeline"}:
                    item["section"] = "Chronology"
                    item["event_date"] = str(item.get("event_date") or "").strip() or None
                    # An undated item is not a chronology event.
                    if not item["event_date"]:
                        continue
                all_items.append(item)

    return _merge_duplicate_chronology_items(all_items)
