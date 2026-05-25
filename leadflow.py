#!/usr/bin/env python3
"""
LeadFlow — automated inbound-lead triage pipeline.

Takes a messy export of inbound leads (web form, DMs, email, phone notes) and
turns it into clean, deduplicated, prioritized, and routed records that are
ready to drop into a CRM, a Google Sheet, or a downstream webhook.

Core design goals:
  * Zero third-party dependencies — runs on a plain Python install anywhere.
  * Deterministic, rules-based triage that a client can read and trust.
  * Pluggable AI classifier (see ai_classifier.py) for fuzzier judgment calls,
    with automatic fallback to the rules engine when no API key is present.

Usage:
    python leadflow.py --input sample_leads.csv --outdir outputs
    python leadflow.py --input sample_leads.csv --outdir outputs --ai   # use OpenAI if OPENAI_API_KEY is set

Author: Jack — AI Automation Specialist
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Configuration — edit these tables to retune the pipeline for any business.
# --------------------------------------------------------------------------- #

# Intent categories and the signal words that point to them. Weighted so that
# strong, unambiguous signals (e.g. "refund") outrank generic ones.
INTENT_RULES: dict[str, dict[str, int]] = {
    "Billing": {"invoice": 3, "charged": 3, "charge": 2, "refund": 3, "billing": 2, "double": 1},
    "Sales Inquiry": {"pricing": 3, "price": 2, "cost": 2, "quote": 2, "bulk": 3, "units": 2,
                      "order": 2, "how much": 3, "$": 1, "lead time": 2, "ship": 1},
    "New Booking": {"book": 3, "appointment": 3, "appt": 3, "schedule": 2, "cleaning": 2,
                    "checkup": 2, "check-up": 2, "consultation": 2, "extraction": 2,
                    "see me": 2, "get in": 1, "come in": 2, "first cleaning": 2,
                    "tooth": 1, "crown": 1, "cracked": 1, "filling": 1},
    "Support": {"hours": 2, "open": 2, "do you accept": 2, "accept": 1, "question": 1,
                "process": 1, "saturday": 1, "do you do": 1},
    "Spam": {"crypto": 4, "btc": 3, "bonus": 3, "guaranteed": 3, "click now": 4,
             "congratulations": 3, "selected": 2, "%": 1},
}

# Words that mark a lead as time-critical regardless of category.
URGENCY_WORDS = ["pain", "painful", "emergency", "asap", "urgent", "today",
                 "getting worse", "worse", "right now", "bleeding"]

# Where each category should be routed inside the business.
ROUTING = {
    "New Booking": "Front Desk",
    "Sales Inquiry": "Sales — Dana",
    "Support": "Support Team",
    "Billing": "Billing",
    "Spam": "Auto-archive",
    "Unclassified": "Front Desk",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Lead:
    name: str = ""
    email: str = ""
    phone: str = ""
    source: str = ""
    message: str = ""
    # Enriched by the pipeline:
    category: str = "Unclassified"
    priority: str = "Low"
    owner: str = ""
    completeness: int = 0
    email_valid: bool = False
    touchpoints: int = 1
    flags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Step 1 — normalization / cleaning
# --------------------------------------------------------------------------- #

def clean_name(raw: str) -> str:
    """Trim, collapse whitespace, fix 'last, first' ordering, title-case."""
    n = re.sub(r"\s+", " ", (raw or "").strip())
    if "," in n:  # e.g. "nguyen, kim" -> "kim nguyen"
        last, first = [p.strip() for p in n.split(",", 1)]
        n = f"{first} {last}"
    return n.title()


def clean_email(raw: str) -> tuple[str, bool]:
    """Lowercase + trim. Returns (email, is_valid)."""
    e = (raw or "").strip().lower()
    return e, bool(EMAIL_RE.match(e))


def clean_phone(raw: str) -> str:
    """Normalize US phone numbers to '+1 (XXX) XXX-XXXX'. Leaves odd ones as digits."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1 ({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    return digits  # incomplete/foreign — keep raw digits rather than guess


def core_digits(phone: str) -> str:
    """Return the 10-digit core of a US number, dropping a leading country code."""
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def is_blank(lead: Lead) -> bool:
    """A row with no usable contact info and no message is noise."""
    return not (lead.name or lead.email or lead.phone) and not lead.message.strip()


# --------------------------------------------------------------------------- #
# Step 2 — classification, urgency, scoring, routing
# --------------------------------------------------------------------------- #

def classify_intent(text: str) -> str:
    """Score the message against each category's signal words; highest wins."""
    t = text.lower()
    scores = {cat: sum(w for kw, w in words.items() if kw in t)
              for cat, words in INTENT_RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Unclassified"


def assess_priority(category: str, text: str, completeness: int) -> str:
    t = text.lower()
    if category == "Spam":
        return "Low"
    if any(w in t for w in URGENCY_WORDS):
        return "High"
    if category == "Billing":
        return "High"           # money problems escalate fast
    if category in ("New Booking", "Sales Inquiry"):
        return "Medium"
    return "Low"


def score_completeness(lead: Lead) -> int:
    pts = 0
    pts += 30 if lead.name else 0
    pts += 30 if lead.email_valid else 0
    pts += 30 if len(core_digits(lead.phone)) == 10 else 0
    pts += 10 if len(lead.message.strip()) >= 15 else 0
    return pts


# --------------------------------------------------------------------------- #
# Step 3 — deduplication
# --------------------------------------------------------------------------- #

def dedupe(leads: list[Lead]) -> list[Lead]:
    """
    Merge leads that share a valid email or a 10-digit phone. Keeps the most
    complete record, concatenates distinct messages, and counts touchpoints
    (a returning lead is a stronger signal of intent).
    """
    merged: dict[str, Lead] = {}
    order: list[str] = []

    for lead in leads:
        phone_digits = core_digits(lead.phone)
        key = lead.email if lead.email_valid else (phone_digits if len(phone_digits) == 10 else None)
        if key is None:
            uid = f"_uniq_{len(order)}"
            merged[uid] = lead
            order.append(uid)
            continue
        if key in merged:
            existing = merged[key]
            existing.touchpoints += 1
            if lead.message and lead.message not in existing.message:
                existing.message = f"{existing.message} | {lead.message}".strip(" |")
            # prefer the more complete field values
            existing.name = existing.name or lead.name
            existing.phone = existing.phone or lead.phone
            if not existing.email_valid and lead.email_valid:
                existing.email, existing.email_valid = lead.email, True
        else:
            merged[key] = lead
            order.append(key)
    return [merged[k] for k in order]


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #

def load(input_path: Path) -> list[Lead]:
    leads: list[Lead] = []
    with input_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email, valid = clean_email(row.get("email", ""))
            lead = Lead(
                name=clean_name(row.get("name", "")),
                email=email,
                phone=clean_phone(row.get("phone", "")),
                source=(row.get("source", "") or "").strip(),
                message=(row.get("message", "") or "").strip(),
                email_valid=valid,
            )
            if not is_blank(lead):
                leads.append(lead)
    return leads


def enrich(leads: list[Lead], use_ai: bool = False) -> list[Lead]:
    ai_fn = None
    if use_ai:
        try:
            from ai_classifier import classify_with_ai  # optional module
            ai_fn = classify_with_ai
        except Exception:
            ai_fn = None

    for lead in leads:
        ai_result = ai_fn(lead.message) if ai_fn else None
        lead.category = (ai_result or {}).get("category") or classify_intent(lead.message)
        if not lead.email and not lead.phone:
            lead.flags.append("no-contact-method")
        if lead.email and not lead.email_valid:
            lead.flags.append("invalid-email")
        if len(core_digits(lead.phone)) not in (0, 10):
            lead.flags.append("check-phone")
        lead.completeness = score_completeness(lead)
        lead.priority = assess_priority(lead.category, lead.message, lead.completeness)
        if lead.touchpoints > 1 and lead.priority == "Medium":
            lead.priority = "High"  # repeat contact = hotter lead
        lead.owner = ROUTING.get(lead.category, "Front Desk")
    # sort: High > Medium > Low, then most complete first
    rank = {"High": 0, "Medium": 1, "Low": 2}
    leads.sort(key=lambda l: (rank.get(l.priority, 3), -l.completeness))
    return leads


# --------------------------------------------------------------------------- #
# Output writers
# --------------------------------------------------------------------------- #

def write_csv(leads: list[Lead], path: Path) -> None:
    cols = ["priority", "category", "owner", "name", "email", "phone",
            "completeness", "touchpoints", "source", "flags", "message"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for l in leads:
            d = asdict(l)
            d["flags"] = ";".join(l.flags)
            w.writerow([d[c] for c in cols])


def write_json(leads: list[Lead], path: Path) -> None:
    path.write_text(json.dumps([asdict(l) for l in leads], indent=2), encoding="utf-8")


def write_brief(leads: list[Lead], path: Path) -> None:
    today = datetime.now().strftime("%A, %B %d, %Y")
    high = [l for l in leads if l.priority == "High" and l.category != "Spam"]
    spam = [l for l in leads if l.category == "Spam"]
    actionable = [l for l in leads if l.category != "Spam"]

    lines = [f"# Daily Lead Brief — {today}", ""]
    lines.append(f"**{len(actionable)} actionable leads** processed · "
                 f"**{len(high)} need attention now** · "
                 f"**{len(spam)} spam auto-archived** · "
                 f"0 sorted by hand")
    lines.append("")
    if high:
        lines.append("## 🔴 Act first")
        for l in high:
            contact = l.email or l.phone or "no contact on file"
            note = " · ".join(l.flags) if l.flags else ""
            lines.append(f"- **{l.name or 'Unknown'}** ({l.category}) → {l.owner} — {contact}"
                         + (f"  _[{note}]_" if note else ""))
            lines.append(f"    > {l.message[:140]}")
        lines.append("")
    rest = [l for l in actionable if l.priority != "High"]
    if rest:
        lines.append("## Queue")
        for l in rest:
            contact = l.email or l.phone or "no contact"
            lines.append(f"- [{l.priority}] **{l.name or 'Unknown'}** — {l.category} → {l.owner} ({contact})")
        lines.append("")
    if spam:
        lines.append("## Auto-archived")
        for l in spam:
            lines.append(f"- {l.name or 'Unknown'} — flagged spam, no reply sent")
    lines.append("")
    lines.append("---")
    lines.append("_Generated by LeadFlow. Re-run anytime; rules live in the config block of `leadflow.py`._")
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def run(input_path: Path, outdir: Path, use_ai: bool = False) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    raw = load(input_path)
    deduped = dedupe(raw)
    leads = enrich(deduped, use_ai=use_ai)

    write_csv(leads, outdir / "clean_leads.csv")
    write_json(leads, outdir / "leads.json")
    write_brief(leads, outdir / "daily_brief.md")

    return {
        "rows_in": sum(1 for _ in input_path.open(encoding="utf-8")) - 1,
        "valid_leads": len(raw),
        "after_dedupe": len(deduped),
        "high_priority": sum(1 for l in leads if l.priority == "High" and l.category != "Spam"),
        "spam": sum(1 for l in leads if l.category == "Spam"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="LeadFlow — automated inbound-lead triage.")
    ap.add_argument("--input", default="sample_leads.csv", type=Path)
    ap.add_argument("--outdir", default="outputs", type=Path)
    ap.add_argument("--ai", action="store_true", help="use the optional OpenAI classifier if available")
    args = ap.parse_args()

    stats = run(args.input, args.outdir, use_ai=args.ai)
    print("LeadFlow run complete:")
    for k, v in stats.items():
        print(f"  {k:>16}: {v}")
    print(f"  outputs written to: {args.outdir}/")


if __name__ == "__main__":
    main()
