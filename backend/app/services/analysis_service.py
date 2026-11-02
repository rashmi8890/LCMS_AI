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
        "You are a careful legal-administrative document analyst. Analyze only supplied "
        "case-document excerpts. Do not invent facts, dates, legal provisions, parties, "
        "page numbers or sources. Distinguish recorded facts, claims/allegations, "
        "contradictions, missing information and cautious inferences. This is decision "
        "support, not legal advice. Return valid JSON only with this schema: "
        '{"items":[{"section":"Executive Summary|Case Background|Facts|Issues|'
        'Contradictions|Missing Information|Parties|Evidence|Law|Analysis|'
        'Suggested Action|Chronology","event_date":"date exactly supported by source, or null",'
        '"content":"one self-contained factual or analytical paragraph",'
        '"evidence":[{"chunk_id":123,"quote":"verbatim contiguous quote from that chunk",'
        '"note":"why this quote supports the paragraph"}]}]}. '
        "Classify findings carefully and use the exact section names in the schema. "
        "Do not omit a category when the supplied excerpts contain supporting material. "
        "Executive Summary: concise synthesis of the claim, parties, relief sought, key records "
        "and evidentiary position, with supporting citations. Case Background: material factual "
        "and procedural context. Facts: individual facts, with allegations attributed to their "
        "maker. Issues: questions requiring determination, not general facts. Contradictions: "
        "identify both inconsistent statements or entries and explain the conflict. Missing "
        "Information: material gaps in the case record; do not infer absence merely because one "
        "batch lacks a detail. Parties: name, role, claims, relief, response, admissions/denials "
        "and position; if no response is present, say so. Evidence: assess actual documents and "
        "records, what they record and which issue they may support; a citation alone does not "
        "make a fact an Evidence item. Law: include only an Act, rule, regulation, section or "
        "legal principle explicitly identified in the supplied excerpts, with exact name/number "
        "and relevant text. Never classify factual narrative as Law or infer applicable law from "
        "the case subject or metadata. If no legal provision appears in this batch, return no "
        "Law item. Analysis: reasoned assessment not fitting a more specific category. Suggested "
        "Action: verification or procedural steps, not facts or law. "
        "Every document-derived factual paragraph and every Chronology event must have "
        "at least one evidence entry. For Chronology, create one item per distinct event "
        "that has an explicit date or date range in the supplied source text. Set section "
        "to exactly 'Chronology'. Put the date in event_date exactly as printed in the "
        "source where possible; normalize to YYYY-MM-DD only when the date is unambiguous. "
        "If only month/year or a date range is given, preserve that wording. Never infer "
        "a date from document order, filing context, or surrounding events. Do not create "
        "an event with no explicit date. Put a concise event description in content. "
        "The evidence quote must include the date and enough of the event wording to "
        "support it. Do not turn every date mentioned in a footer, reference list, or "
        "unrelated citation into an event. Split distinct factual points into separate "
        "items. Reuse a source chunk where appropriate, but cite each item independently. "
        "Quotes must be exact, copied from source excerpts, and concise enough to locate "
        "in the PDF. If this batch does not contain evidence for a point, do not assert it."
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
            "Executive Summary, Case Background, Facts, Issues, Parties, Evidence and Law "
            "when supported; also return Contradictions, Missing Information, Analysis, "
            "Suggested Action and explicitly dated Chronology events when supported. Do not "
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
