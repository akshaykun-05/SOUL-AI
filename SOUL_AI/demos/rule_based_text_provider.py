"""Narrow, deterministic provider for the reproducible synthetic PDF demo.

This is intentionally not a production NLP model. It derives every output from
fact sentences in the request's original evidence and abstains on other forms.
"""
from __future__ import annotations

import json
import re
from soul_ai.providers.base import LLMRequest, LLMResponse


FACT = re.compile(r"(?P<entity>[A-Za-z][A-Za-z0-9 &.-]*?)\s+(?P<attribute>loan amount)\s+is\s+(?P<value>INR\s+[\d,]+)\s*\.", re.I)


class RuleBasedTextProvider:
    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            payload = json.loads(request.user_prompt)
            if "original_evidence" in payload:
                output = self._verify(payload)
            elif "evidence" in payload:
                output = self._extract(payload)
            else:
                output = {"status": "insufficient_evidence", "claims": [], "reason": "Unsupported demo input."}
        except (ValueError, TypeError, KeyError):
            output = {"status": "insufficient_evidence", "claims": [], "reason": "Could not parse supplied evidence."}
        return LLMResponse(text=json.dumps(output), provider="local-demo-rules", model="synthetic-fact-parser")

    @staticmethod
    def _facts(evidence):
        rows = []
        for item in evidence:
            text = item.get("text", item.get("extracted_text", ""))
            for match in FACT.finditer(text):
                rows.append({"entity": match.group("entity").strip(),
                             "attribute": "loan amount", "value": match.group("value").strip(),
                             "text": match.group(0), "evidence_id": item.get("evidence_id"),
                             "filename": item.get("filename"), "page_number": item.get("page_number")})
        return rows

    def _extract(self, payload):
        facts = self._facts(payload["evidence"])
        question_terms = set(re.findall(r"[a-z]+", payload["question"].casefold()))
        matching = [fact for fact in facts
                    if set(re.findall(r"[a-z]+", fact["entity"].casefold())) & question_terms
                    and {"loan", "amount"}.issubset(question_terms)]
        if not matching:
            return {"status": "insufficient_evidence", "claims": [],
                    "reason": "No matching explicit fact was found in the supplied evidence."}
        # Preserve each original wording and value. Cross-document alternatives
        # are left visible for the response engine's independent contradiction pass.
        claims = [{"claim_text": f["text"], "entity": f["entity"],
                   "attribute": f["attribute"], "value": f["value"], "unit": "INR",
                   "temporal_context": None, "source_evidence_ids": [f["evidence_id"]]}
                  for f in matching]
        return {"status": "answered", "answer": " ".join(c["claim_text"] for c in claims),
                "claims": claims}

    def _verify(self, payload):
        claim = payload["claim"]["claim_text"]
        found = self._facts(payload["original_evidence"])
        proposed = next((fact for fact in self._facts([{"text": claim}]) ), None)
        if proposed is None:
            return {"status": "UNSUPPORTED", "evidence": [],
                    "reason": "The claim is outside the demo parser's explicit fact format."}
        key = (proposed["entity"].casefold(), proposed["attribute"])
        matching = [f for f in found if (f["entity"].casefold(), f["attribute"]) == key]
        if any(f["value"].casefold() == proposed["value"].casefold() for f in matching):
            return {"status": "SUPPORTED", "evidence": [{"filename": f["filename"],
                    "page_number": f["page_number"], "text": f["text"]}
                    for f in matching if f["value"].casefold() == proposed["value"].casefold()],
                    "reason": "The exact entity, attribute, and value occur in original evidence.",
                    "requires_human_review": False}
        if matching:
            return {"status": "CONTRADICTED", "evidence": [{"filename": f["filename"],
                    "page_number": f["page_number"], "text": f["text"]} for f in matching],
                    "reason": "Original evidence states a different value for this entity and attribute.",
                    "requires_human_review": False}
        return {"status": "UNSUPPORTED", "evidence": [],
                "reason": "No fact for this entity and attribute occurs in original evidence."}
