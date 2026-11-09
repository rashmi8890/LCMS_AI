import json
import logging
from openai import OpenAI
from ..config import settings

logger = logging.getLogger(__name__)

def client_or_none():
    if not settings.llm_api_key or not settings.llm_model:
        return None
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=90,
        max_retries=1,
    )

def _response_details(response) -> str:
    """Return safe response metadata for diagnosing empty model output."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return "no choices returned"

    choice = choices[0]
    message = getattr(choice, "message", None)
    usage = getattr(response, "usage", None)
    finish_reason = getattr(choice, "finish_reason", None)
    content = getattr(message, "content", None) if message else None
    reasoning = getattr(message, "reasoning", None) if message else None
    refusal = getattr(message, "refusal", None) if message else None

    return (
        f"finish_reason={finish_reason!r}, "
        f"content_length={len(content) if isinstance(content, str) else 0}, "
        f"reasoning_length={len(reasoning) if isinstance(reasoning, str) else 0}, "
        f"refusal={bool(refusal)}, "
        f"completion_tokens={getattr(usage, 'completion_tokens', None)}, "
        f"prompt_tokens={getattr(usage, 'prompt_tokens', None)}, "
        f"total_tokens={getattr(usage, 'total_tokens', None)}"
    )

def _parse_json_object(content: str) -> dict:
    if not content or not content.strip():
        raise ValueError("The LLM returned an empty response.")

    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("The LLM response must be a JSON object.")
    return parsed

def _create_completion(client, messages, *, json_mode: bool):
    request = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.1,
        "max_completion_tokens": 1800,
        "reasoning_effort": "low",
    }
    if json_mode:
        request["response_format"] = {"type": "json_object"}
    return client.chat.completions.create(**request)


def ask_json(system_prompt: str, user_prompt: str):
    client = client_or_none()

    if client is None:
        raise RuntimeError(
            "LLM is not configured. Set LLM_API_KEY and LLM_MODEL in .env."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = _create_completion(
            client,
            messages,
            json_mode=True,
        )

        choices = getattr(response, "choices", None) or []

        if not choices:
            raise RuntimeError("LLM returned no completion choices.")

        choice = choices[0]
        content = choice.message.content or ""
        finish_reason = getattr(choice, "finish_reason", None)

        logger.info(
            "LLM response | finish_reason=%s | content_length=%s",
            finish_reason,
            len(content),
        )

        if not content.strip():
            raise ValueError(
                f"LLM returned empty content. "
                f"finish_reason={finish_reason}"
            )

        try:
            return _parse_json_object(content)

        except json.JSONDecodeError as exc:
            # Log a small diagnostic excerpt, not the entire legal document.
            start = max(0, exc.pos - 100)
            end = min(len(content), exc.pos + 100)
            excerpt = content[start:end]

            logger.error(
                "Invalid LLM JSON | line=%s | column=%s | "
                "position=%s | finish_reason=%s | excerpt=%r",
                exc.lineno,
                exc.colno,
                exc.pos,
                finish_reason,
                excerpt,
            )

            raise RuntimeError(
                "LLM returned malformed JSON. "
                f"Line={exc.lineno}, column={exc.colno}, "
                f"finish_reason={finish_reason}. "
                "See backend logs for the response excerpt."
            ) from exc

    except RuntimeError:
        raise

    except Exception as exc:
        logger.exception("LLM JSON generation request failed.")
        raise RuntimeError(
            f"LLM request failed ({type(exc).__name__}): {exc}"
        ) from exc
