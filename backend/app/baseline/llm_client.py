"""
LLM-only baseline: direct OpenRouter call with no retrieval, no RAG.
Used to benchmark the multi-step pipeline against single-LLM approaches.

The prompt is adapted from Dom's llm_client.py (originally Ollama/local Phi-4).
We call OpenRouter so we can compare multiple LLMs (microsoft/phi-4,
openai/gpt-4o-mini, etc.) without managing separate SDKs or local models.
"""

import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "microsoft/phi-4"
TIMEOUT = 60.0

SYSTEM_PROMPT = (
    "You are a clinical coding assistant.\n\n"
    "TASK: Return SNOMED CT and ICD-10 codes for a condition.\n\n"
    "OUTPUT RULES:\n"
    "1. Output ONLY valid JSON — a JSON array of code objects.\n"
    "2. Each element MUST be an object with EXACTLY these fields:\n"
    '   "code": string,\n'
    '   "term": string,\n'
    '   "vocabulary": "SNOMED CT" or "ICD-10",\n'
    '   "decision": "include",\n'
    '   "confidence": 0.8,\n'
    '   "rationale": "baseline LLM (no retrieval)",\n'
    '   "sources": ["<model-name>"]\n'
    "3. Do NOT group by vocabulary.\n"
    "4. Do NOT use nested objects.\n"
    "5. Do NOT include any text outside the JSON array.\n"
    "6. Return 10-30 codes covering the condition and its common variants.\n\n"
    "FORMAT EXAMPLE:\n"
    '[{"code":"E11.9","term":"Type 2 diabetes mellitus without complications",'
    '"vocabulary":"ICD-10","decision":"include","confidence":0.9,'
    '"rationale":"baseline LLM (no retrieval)","sources":["LLM"]}]'
)


def _extract_json_array(text: str) -> list[dict]:
    """LLMs sometimes wrap JSON in prose or code fences — strip and parse."""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in LLM response: {text[:200]}")
    return json.loads(text[start : end + 1])


def run_baseline(condition: str, model: str = DEFAULT_MODEL) -> list[dict]:
    """
    Call an LLM via OpenRouter with no retrieval, no merging, no scoring —
    just the model's prior knowledge of clinical codes.

    `model` is any OpenRouter model id, e.g. 'microsoft/phi-4',
    'openai/gpt-4o-mini', 'anthropic/claude-3.5-haiku'.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Condition: {condition}"},
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://clinicalcodes.uk",
        "X-OpenRouter-Title": "NICE Clinical Codes Baseline",
    }

    logger.info("Baseline call: model=%s condition=%s", model, condition)
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(OPENROUTER_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    codes = _extract_json_array(content)

    # tag with the model's display name (drop the provider prefix) so the
    # sources column reads "phi-4" / "gpt-4o-mini" instead of the full path
    source_tag = model.split("/")[-1] if "/" in model else model

    normalised = []
    for c in codes:
        try:
            conf = float(c.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
        normalised.append({
            "code": str(c.get("code", "")).strip(),
            "term": c.get("term", ""),
            "vocabulary": c.get("vocabulary", ""),
            "decision": c.get("decision", "include"),
            "confidence": conf,
            "rationale": c.get("rationale", "baseline LLM (no retrieval)"),
            "sources": [source_tag],
            "usage_frequency": None,
        })

    logger.info("Baseline model=%s returned %d codes", model, len(normalised))
    return normalised
