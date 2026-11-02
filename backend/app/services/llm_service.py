import json
from openai import OpenAI
from ..config import settings

def client_or_none():
    if not settings.llm_api_key or not settings.llm_model:
        return None
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=90, max_retries=1)

def ask_json(system_prompt: str, user_prompt: str):
    client = client_or_none()
    if client is None:
        raise RuntimeError("LLM is not configured. Set LLM_API_KEY and LLM_MODEL in .env.")
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}],
        temperature=0.1,
        response_format={"type":"json_object"},
        max_tokens=3000,
    )
    content = response.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc
