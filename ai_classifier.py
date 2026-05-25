"""
ai_classifier.py — optional AI-powered intent classifier for LeadFlow.

The core pipeline (leadflow.py) ships with a fast, deterministic rules engine.
This module is an *optional* upgrade: when an OpenAI API key is available it
hands the fuzzier judgment calls to a language model, then falls back silently
to the rules engine if anything goes wrong (no key, network error, bad JSON).

It intentionally uses only the Python standard library (urllib) so there is
nothing to `pip install` — you only need an API key in the environment:

    export OPENAI_API_KEY="sk-..."
    python leadflow.py --input sample_leads.csv --outdir outputs --ai

Prompt-engineering notes (why the prompt is written the way it is):
  * The system message pins a single job and a closed set of labels so the
    model can't invent new categories that would break downstream routing.
  * It asks for STRICT JSON only — no prose, no markdown fences — so the
    output parses deterministically.
  * It includes the decision rubric for urgency so "priority" is consistent
    with the rules engine rather than the model's mood.
  * temperature=0 keeps classification stable and repeatable across runs.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Optional

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"  # cheap + fast; swap for any chat-completions model

SYSTEM_PROMPT = """You triage one inbound business lead and return structured data.

Return ONLY a JSON object (no markdown, no commentary) with exactly these keys:
  "category": one of ["New Booking","Sales Inquiry","Support","Billing","Spam"]
  "priority": one of ["High","Medium","Low"]
  "summary": one plain-English sentence a busy owner can skim

Rules for priority:
  - High: pain, emergencies, "asap"/"today", or any billing/payment dispute.
  - Medium: a booking or sales inquiry with no urgency.
  - Low: general questions; all spam.
Be decisive. If the message is promotional/scammy, category is "Spam"."""


def _build_payload(message: str) -> dict:
    return {
        "model": MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Lead message:\n\"\"\"\n{message}\n\"\"\""},
        ],
    }


def classify_with_ai(message: str, api_key: Optional[str] = None,
                     timeout: int = 20) -> Optional[dict]:
    """
    Classify a single lead message with OpenAI.

    Returns a dict like {"category","priority","summary"} on success, or None
    on any failure so the caller can fall back to the rules engine. This module
    never raises — automation should degrade gracefully, not crash a batch.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key or not message.strip():
        return None

    data = json.dumps(_build_payload(message)).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        # Validate the model stayed inside the allowed label set.
        if parsed.get("category") not in {
            "New Booking", "Sales Inquiry", "Support", "Billing", "Spam"
        }:
            return None
        return {
            "category": parsed["category"],
            "priority": parsed.get("priority", "Medium"),
            "summary": parsed.get("summary", ""),
        }
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None  # graceful fallback to the rules engine


if __name__ == "__main__":
    # Tiny manual smoke test (requires OPENAI_API_KEY to do anything real).
    demo = "My crown cracked and it's really painful, can someone see me today?"
    print(classify_with_ai(demo) or "No API key set — pipeline would use the rules engine.")
