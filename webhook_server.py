"""
webhook_server.py — expose LeadFlow as a live HTTP endpoint.

This turns the batch pipeline into a real-time API: point a web form, Zapier,
or Make.com webhook at POST /lead and each submission is normalized, classified,
prioritized, and routed on the spot — returning clean JSON the caller can push
straight into a CRM.

This is the bridge between "a script" and "an automation a business runs":
  Web form / Zapier / Make  ──HTTP POST──>  /lead  ──>  clean, routed JSON

Run it:
    pip install flask
    python webhook_server.py
    # then:
    curl -X POST http://localhost:5000/lead \\
         -H "Content-Type: application/json" \\
         -d '{"name":"Marcus","email":"marcus@x.com","phone":"6265550199",
              "message":"cracked tooth, in a lot of pain, can I come in today?"}'
"""

from __future__ import annotations

from flask import Flask, request, jsonify

import leadflow as lf

app = Flask(__name__)


@app.post("/lead")
def intake():
    """Accept one lead as JSON, return the enriched/routed record."""
    payload = request.get_json(silent=True) or {}

    email, valid = lf.clean_email(payload.get("email", ""))
    lead = lf.Lead(
        name=lf.clean_name(payload.get("name", "")),
        email=email,
        phone=lf.clean_phone(payload.get("phone", "")),
        source=payload.get("source", "api"),
        message=(payload.get("message", "") or "").strip(),
        email_valid=valid,
    )
    if lf.is_blank(lead):
        return jsonify({"error": "empty lead — needs a contact method or message"}), 400

    # Reuse the exact same enrichment logic as the batch pipeline.
    use_ai = bool(request.args.get("ai"))
    lf.enrich([lead], use_ai=use_ai)

    return jsonify({
        "routed_to": lead.owner,
        "category": lead.category,
        "priority": lead.priority,
        "completeness": lead.completeness,
        "flags": lead.flags,
        "contact": {"name": lead.name, "email": lead.email, "phone": lead.phone},
    }), 200


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
