import re
from ..models import Case, Chunk
from ..services.llm_service import ask_json
import math
import logging

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN_ESTIMATE = 2.5
MAX_REQUEST_TOKEN_BUDGET = 4000

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
LATIN_RE = re.compile(r"[A-Za-z]")


def detect_case_language(chunks):
    text = "\n".join(chunk.text or "" for chunk in chunks)

    devanagari_count = len(DEVANAGARI_RE.findall(text))
    latin_count = len(LATIN_RE.findall(text))

    total = devanagari_count + latin_count

    if total == 0:
        return "English"

    hindi_ratio = devanagari_count / total

    if hindi_ratio >= 0.60:
        return "Hindi"

    if hindi_ratio <= 0.20:
        return "English"

    return "Bilingual"

def estimate_tokens(text: str) -> int:
    """Estimate tokens conservatively without requiring a tokenizer."""
    if not text:
        return 0

    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)

# def build_analysis_batches(
#     chunks,
#     system_prompt: str,
#     metadata: str,
#     max_tokens: int = MAX_REQUEST_TOKEN_BUDGET,
# ):
#     """
#     Build deterministic batches based on estimated prompt token usage.
#     Each chunk remains intact and retains its original source information.
#     """
#     batches = []
#     current_batch = []

#     base_prompt = (
#         f"{metadata}\n\n"
#         "DOCUMENT EXCERPTS (batch 1)\n"
#     )

#     base_tokens = (
#         estimate_tokens(system_prompt)
#         + estimate_tokens(base_prompt)
#     )

#     available_tokens = max_tokens - base_tokens

#     if available_tokens <= 0:
#         raise ValueError(
#             "System prompt and case metadata exceed the batch token budget."
#         )

#     current_tokens = base_tokens

#     for chunk in chunks:
#         chunk_text = (
#             f"[SOURCE chunk_id={chunk.id} "
#             f"document={chunk.document.filename!r} "
#             f"page={chunk.page_number}]\n"
#             f"{chunk.text}"
#         )

#         chunk_tokens = estimate_tokens(chunk_text) + estimate_tokens("\n\n")

#         if chunk_tokens > available_tokens:
#             raise ValueError(
#                 f"Chunk {chunk.id} is too large for the configured "
#                 "analysis batch budget. Split this chunk before analysis."
#             )

#         if current_batch and current_tokens + chunk_tokens > max_tokens:
#             batches.append(current_batch)
#             current_batch = []
#             current_tokens = base_tokens

#         current_batch.append(chunk)
#         current_tokens += chunk_tokens

#     if current_batch:
#         batches.append(current_batch)

#     return batches

ANALYSIS_USER_INSTRUCTIONS = (
    "Extract every applicable category supported by these excerpts: "
    "Executive Summary, Case Background, Facts, Issues, Parties, Evidence, "
    "Contradictions, Unresolved Facts, Chronology, Law and Analysis. "
    "Return each category only when these excerpts provide relevant support. "
    "Every item must have at least one exact supporting quote from this batch."
)


def build_analysis_user_prompt(
    metadata: str,
    context: str,
    batch_number: int,
    total_batches: int,
) -> str:
    """Build the complete user prompt used for an analysis request."""
    return (
        f"{metadata}\n\n"
        f"DOCUMENT EXCERPTS (batch {batch_number}/{total_batches})\n"
        f"{context}\n\n"
        f"{ANALYSIS_USER_INSTRUCTIONS}"
    )


def _format_chunk_source(chunk) -> str:
    """Format a chunk with its original source information."""
    return (
        f"[SOURCE chunk_id={chunk.id} "
        f"document={chunk.document.filename!r} "
        f"page={chunk.page_number}]\n"
        f"{chunk.text}"
    )


def build_analysis_batches(
    chunks,
    system_prompt: str,
    metadata: str,
    max_tokens: int = MAX_REQUEST_TOKEN_BUDGET,
):
    """
    Build deterministic batches using the estimated size of the complete
    system and user prompts.

    Each chunk remains intact and retains its original source information.
    """
    batches = []
    current_batch = []

    # Use a deliberately long batch header while estimating. The actual
    # batch numbers will normally be shorter, so this adds a small margin.
    estimated_batch_number = 999999
    estimated_total_batches = 999999

    def estimate_batch_tokens(batch):
        context = "\n\n".join(
            _format_chunk_source(chunk) for chunk in batch
        )

        user_prompt = build_analysis_user_prompt(
            metadata=metadata,
            context=context,
            batch_number=estimated_batch_number,
            total_batches=estimated_total_batches,
        )

        return (
            estimate_tokens(system_prompt)
            + estimate_tokens(user_prompt)
        )

    for chunk in chunks:
        candidate_batch = current_batch + [chunk]
        candidate_tokens = estimate_batch_tokens(candidate_batch)

        if candidate_tokens <= max_tokens:
            current_batch = candidate_batch
            continue

        # If the current batch is empty, even this single chunk is too large.
        if not current_batch:
            raise ValueError(
                f"Chunk {chunk.id} is too large for the configured "
                f"analysis request budget of {max_tokens} estimated tokens. "
                "Split this chunk before analysis."
            )

        # Finalize the existing batch and start a new one.
        batches.append(current_batch)
        current_batch = [chunk]

        # Verify that the new batch can fit on its own.
        single_chunk_tokens = estimate_batch_tokens(current_batch)

        if single_chunk_tokens > max_tokens:
            raise ValueError(
                f"Chunk {chunk.id} is too large for the configured "
                f"analysis request budget of {max_tokens} estimated tokens. "
                "Split this chunk before analysis."
            )

    if current_batch:
        batches.append(current_batch)

    return batches

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
        "names: Executive Summary, Case Background, Facts, Issues, Parties, "
        "Evidence, Contradictions, Unresolved Facts, Chronology, Law, Analysis. "
        "Do not use other section names. Never change the schema.\n\n"

        "CATEGORY RULES:\n"
        "Executive Summary: Concise case overview, parties, dispute, relief, "
        "key records, evidence. Do not draw "
        "complete-case conclusions from one batch.\n"
        "Case Background: Briefly explain the nature of the case and its procedural context.\n"
        "Facts: Extract distinct material facts. Attribute allegations to their makers; "
        "distinguish allegations from facts established by records.\n"
        "Issues: Specific factual or legal questions requiring determination.\n"
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

        "Evidence: Analyse the SUBSTANTIVE CONTENT of records, not merely "
        "the names or types of documents submitted. Do not produce an inventory "
        "such as 'the bundle contains attendance registers, wage sheets and bank "
        "records' unless the inventory itself is materially relevant. For each "
        "record with readable substantive content, extract the actual entries, "
        "figures, dates, periods, calculations, names, transaction details, "
        "attendance marks, recorded hours, deductions, payment status, statements "
        "or other particulars that are explicitly visible in the supplied excerpt. "
        "Preserve exact amounts, dates, units and labels; do not calculate, infer, "
        "complete or normalize missing values unless the calculation is transparent "
        "and all inputs are explicitly present. Explain what the extracted content "
        "shows in relation to a specific disputed fact. Distinguish what the record "
        "directly records from what it may support and what it does NOT establish. "
        "State limitations only when grounded in the excerpt; do not assume a record "
        "is uncertified, incomplete, authentic or unauthentic unless the source says "
        "so. Compare records only when the supplied excerpts contain enough matching "
        "details to make a valid comparison, and identify any mismatch precisely. "
        "Do not repeat a generic relevance/verification paragraph for different "
        "records. If an excerpt contains only a cover page, index, annexure list or "
        "document title without substantive record entries, do not describe the "
        "underlying record as if its contents were examined; omit an Evidence item "
        "unless the index information itself proves a material fact. Every Evidence "
        "finding must include at least one exact quotation showing the actual record "
        "content being analysed. A citation or document name alone is not evidence.\n"
        "Contradictions: Identify both conflicting versions, their sources "
        "and the precise inconsistency. Do not confuse missing information "
        "with contradiction.\n"
       
        "Unresolved Facts: Identify material factual questions that remain unanswered, "
        "disputed, unverified, or unsupported in the available case record. Explain "
        "what is known and what remains uncertain. Do not treat absence from this batch "
        "alone as proof that information is missing; flag only gaps directly supported "
        "by the supplied excerpts and avoid complete-case conclusions.\n"

        "Chronology: Extract an event only when the supplied source explicitly states "
        "a date or time marker for that event. Do not infer or invent dates, and do not "
        "assign a more precise date than the source provides. Preserve the source date "
        "wording in event_date unless its conversion to ISO format is unambiguous. "
        "Each chronology item must describe one event and cite a quotation that supports "
        "both the event and its date. Do not add a general timeline analysis as an event.\n"

        "Law: Include ONLY legal provisions explicitly mentioned "
        "in the supplied documents, such as Acts, statutes, sections, "
        "rules, regulations, notifications or cited legal principles. "
        "Identify the exact provision and explain its stated relevance "
        "only when supported by the source. Every Law finding must "
        "contain an exact quotation identifying the legal provision. "
        "NEVER create a Law finding merely to state that no law, Act, "
        "section or statutory provision was mentioned. If no explicit "
        "legal provision is found, return NO Law item for that batch. "
        "Do not invent legal provisions or introduce external law."

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

    analysis_language = detect_case_language(chunks)
        
    language_instruction = {
        "Hindi": (
            "LANGUAGE REQUIREMENT: Write all finding content, summaries, "
            "explanations, legal analysis and relevance notes in Hindi "
            "using Devanagari script. Preserve names, dates, amounts and "
            "legal references accurately. Evidence quotations must remain "
            "exactly as written in the source document."
        ),
        "English": (
            "LANGUAGE REQUIREMENT: Write all finding content, summaries, "
            "explanations and legal analysis in English. Evidence quotations "
            "must remain exactly as written in the source document."
        ),
        "Bilingual": (
            "LANGUAGE REQUIREMENT: Write explanations primarily in Hindi. "
            "Preserve material English expressions and legal terms where "
            "appropriate. Evidence quotations must remain exactly as written "
            "in the source document."
        ),
    }[analysis_language]

    system = system + "\n\n" + language_instruction

    logger.info(
        "Analysis language selected | case_id=%s | language=%s",
        case_id,
        analysis_language,
    )

    # all_items = []
    # batch_size = 6
    # for offset in range(0, len(chunks), batch_size):
    #     batch = chunks[offset:offset + batch_size]
    #     context = "\n\n".join(
    #         f"[SOURCE chunk_id={c.id} document={c.document.filename!r} page={c.page_number}]\n{c.text}"
    #         for c in batch
    #     )
    #     user = (
    #         f"CASE METADATA\nCase number: {case.case_number}\nSubject: {case.subject}\n"
    #         f"Act: {case.act}\nSections: {case.sections}\nApplicant: {case.applicant}\n"
    #         f"Respondent: {case.respondent}\n\n"
    #         f"DOCUMENT EXCERPTS (batch {offset // batch_size + 1})\n{context}\n\n"
    #         "Extract every applicable category supported by these excerpts: Executive Summary, "
    #         "Case Background, Facts, Issues, Parties, Evidence, Contradictions, "
    #         "Unresolved Facts, Chronology, Law and Analysis. Return each category only "
    #         "when these excerpts provide relevant support. Do not omit a supported category "
    #         "merely because another category is easier to answer. Do not "
    #         "fill a category with unrelated content. Every item must have at least one exact "
    #         "supporting quote from this batch. Return one JSON object."
    #     )
    #     result = ask_json(system, user)
    all_items = []

    metadata = (
        f"CASE METADATA\n"
        f"Case number: {case.case_number}\n"
        f"Subject: {case.subject}\n"
        f"Act: {case.act}\n"
        f"Sections: {case.sections}\n"
        f"Applicant: {case.applicant}\n"
        f"Respondent: {case.respondent}"
    )

    batches = build_analysis_batches(
        chunks=chunks,
        system_prompt=system,
        metadata=metadata,
    )

    logger.info(
        "Case analysis batching | case_id=%s | chunks=%d | batches=%d",
        case_id,
        len(chunks),
        len(batches),
    )

    for batch_number, batch in enumerate(batches, start=1):

        # context = "\n\n".join(
        #     f"[SOURCE chunk_id={c.id} "
        #     f"document={c.document.filename!r} "
        #     f"page={c.page_number}]\n{c.text}"
        #     for c in batch
        # )

        # user = (
        #     f"{metadata}\n\n"
        #     f"DOCUMENT EXCERPTS (batch {batch_number}/{len(batches)})\n"
        #     f"{context}\n\n"
        #     "Extract every applicable category supported by these excerpts: "
        #     "Executive Summary, Case Background, Facts, Issues, Parties, Evidence, "
        #     "Contradictions, Unresolved Facts, Chronology, Law and Analysis. "
        #     "Return each category only when these excerpts provide relevant support. "
        #     "Every item must have at least one exact supporting quote from this batch."
        # )
        context = "\n\n".join(
            _format_chunk_source(chunk)
            for chunk in batch
        )

        user = build_analysis_user_prompt(
            metadata=metadata,
            context=context,
            batch_number=batch_number,
            total_batches=len(batches),
        )

        logger.info(
            "Processing analysis batch | case_id=%s | batch=%d/%d | "
            "chunks=%d | estimated_prompt_tokens=%d",
            case_id,
            batch_number,
            len(batches),
            len(batch),
            estimate_tokens(system) + estimate_tokens(user),
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
