# LeadFlow — Automated Inbound-Lead Triage

A small-business automation that turns a messy pile of inbound leads into clean,
deduplicated, prioritized, and routed records — ready to drop straight into a
CRM, a Google Sheet, or a downstream webhook. No human sorting required.

> Built as a demonstration project to show end-to-end business-process
> automation in Python: data cleaning, rules-based triage, an optional AI
> classifier, and a live API endpoint.

---

## The problem it solves

Every small service business gets leads from everywhere at once — website forms,
Instagram DMs, forwarded emails, phone notes. They arrive messy and inconsistent:
duplicate submissions, half-filled fields, mistyped emails, and the genuinely
urgent ones ("I'm in pain, can I come in today?") buried next to spam.

Someone usually sorts this by hand every morning. **LeadFlow does it in seconds.**

## What it does

The pipeline runs five stages:

1. **Clean** — normalizes names (`nguyen, kim` → `Kim Nguyen`), lowercases emails,
   formats phone numbers to `+1 (XXX) XXX-XXXX`, and validates each field.
2. **Deduplicate** — merges leads that share an email or phone, combines their
   messages, and counts touchpoints (a returning lead is a hotter lead).
3. **Classify** — labels each lead by intent (New Booking, Sales Inquiry,
   Support, Billing, Spam) using a transparent, tunable rules engine.
4. **Prioritize** — flags urgency (pain, emergencies, "asap", billing disputes)
   and scores how complete each record is.
5. **Route & report** — assigns an owner per category and writes three outputs:
   a CRM-ready CSV, a JSON feed for downstream tools, and a human-readable
   **Daily Lead Brief** in Markdown.

```
  raw_leads.csv ──> clean ──> dedupe ──> classify ──> prioritize ──> route
                                                                       │
                          ┌────────────────────┬─────────────────────┐
                          ▼                    ▼                     ▼
                   clean_leads.csv         leads.json          daily_brief.md
                     (for the CRM)      (for Zapier/Make)     (for the owner)
```

## What it catches that a manual inbox misses

Run on the included sample, LeadFlow automatically caught every one of these —
the kind of thing a tired human skimming an inbox at 8am quietly lets slip:

- **A returning hot lead, hiding as two messages.** Marcus emailed about a
  cracked tooth, then called back the next day ("the pain is getting worse").
  LeadFlow recognized the same person, **merged both contacts into one record**,
  counted it as a repeat touchpoint, and pushed it to the top of the queue.
- **Broken email addresses, before they cost you a reply.** `tara.brooks@gmail`
  and `chrisp@` *look* fine at a glance but would bounce. LeadFlow **flagged both
  as `invalid-email`** so the front desk reaches out by phone instead of emailing
  into the void.
- **An urgent patient buried under routine questions.** An emergency extraction
  request sat in the middle of the list. LeadFlow read the urgency in the text
  and **promoted it to High priority** automatically.
- **Spam, gone without a glance.** A crypto-bonus blast was **auto-archived** —
  never wasting a second of staff time.
- **A junk row, dropped.** A blank submission was **discarded as noise** rather
  than cluttering the CRM.
- **Inconsistent phone formats, standardized.** `626-555-0199`, `(415) 555 2231`,
  and `2065550144` all came out as clean `+1 (XXX) XXX-XXXX`.

Net result on the sample: **12 raw rows → 9 clean, routed leads in seconds, with
zero manual sorting** — and not one urgent lead or bad contact slipped through.

## Skills demonstrated

| Skill | Where |
|---|---|
| **Python** | The whole pipeline — dataclasses, type hints, modular functions, a zero-dependency core. |
| **Business Process Automation** | Replaces a manual daily sorting task with a repeatable, configurable pipeline. |
| **API Development** | `webhook_server.py` exposes the pipeline as a live `POST /lead` endpoint for forms / Zapier / Make. |
| **OpenAI API & Prompt Engineering** | `ai_classifier.py` — an engineered, JSON-strict prompt with graceful fallback to the rules engine. |

## Quickstart

```bash
# 1) Core pipeline — no installation needed (standard library only)
python leadflow.py --input sample_leads.csv --outdir outputs

# 2) With the optional AI classifier (needs an OpenAI key; falls back automatically)
export OPENAI_API_KEY="sk-..."
python leadflow.py --input sample_leads.csv --outdir outputs --ai

# 3) As a live webhook (real-time, one lead at a time)
pip install flask
python webhook_server.py
curl -X POST http://localhost:5000/lead \
     -H "Content-Type: application/json" \
     -d '{"name":"Marcus","email":"marcus@x.com","phone":"6265550199",
          "message":"cracked tooth, in a lot of pain, can I come in today?"}'
```

## Example: messy in, clean out

**Input** (a real row from `sample_leads.csv`):
```
 marcus webb,Marcus.Webb@gmail.com ,626-555-0199,website_form,"...REALLY painful. ...see me today??"
Marcus Webb,marcus.webb@gmail.com,(626) 555-0199,phone,"Following up — the pain is getting worse, asap"
```

**Output** — the two rows are recognized as the same person, merged, and surfaced
to the top of the brief:
```
🔴 Act first
- Marcus Webb (New Booking) → Front Desk — marcus.webb@gmail.com
    > Hi, my crown cracked last night ... | Following up — the pain is getting worse, asap
```

On the included sample, LeadFlow takes **12 raw rows → 9 clean leads** (drops 1
blank, merges 2 duplicates), surfaces the **4 urgent** ones, and auto-archives
**1 spam** — with zero manual sorting.

## Files

| File | Purpose |
|---|---|
| `leadflow.py` | Core pipeline + CLI. Zero dependencies. |
| `ai_classifier.py` | Optional OpenAI classifier with graceful fallback. |
| `webhook_server.py` | Optional Flask endpoint exposing the pipeline as an API. |
| `sample_leads.csv` | Realistic messy input for the demo. |
| `outputs/` | Generated `clean_leads.csv`, `leads.json`, `daily_brief.md`. |

## Designed to be retuned

Every rule lives in plain config tables at the top of `leadflow.py` — intent
keywords, urgency words, and the routing map. Adapting LeadFlow to a different
business (a med spa, a law office, an HVAC company) is a matter of editing those
tables, not rewriting logic. The same enrichment code powers both the batch run
and the live webhook, so behavior stays identical whether leads arrive in a
nightly export or in real time.

## Notes

- The core triage is **deterministic** — same input, same output every time —
  which is what a business needs to trust an automation.
- The AI layer is strictly optional and **fails safe**: no key or a bad response
  silently falls back to the rules engine, so a batch never crashes.
- This is a demonstration project built on synthetic sample data.
