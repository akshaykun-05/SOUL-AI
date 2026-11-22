from __future__ import annotations

import asyncio
import json

import pytest

from soul_ai.claim_extraction import ClaimExtractionStatus, ClaimExtractor
from soul_ai.extraction import ExtractionMethod, PdfEvidence
from soul_ai.providers.base import LLMResponse


class FakeProvider:
    def __init__(self, payload: dict):
        self.payload = payload
        self.request = None

    async def generate(self, request):
        self.request = request
        return LLMResponse(
            text=json.dumps(self.payload), provider="fake", model="test-model"
        )


def evidence(text: str) -> PdfEvidence:
    return PdfEvidence(
        evidence_id="ev-1",
        document_id="doc-1",
        filename="source.pdf",
        page_number=1,
        extracted_text=text,
        start_offset=0,
        end_offset=len(text),
        extraction_method=ExtractionMethod.text,
    )


def answered(answer: str, claims: list[dict]) -> dict:
    return {"status": "answered", "answer": answer, "claims": claims, "reason": None}


def claim(text: str, *, entity: str, attribute: str, value: str,
          unit: str | None = None, temporal_context: str | None = None) -> dict:
    return {
        "claim_text": text,
        "entity": entity,
        "attribute": attribute,
        "value": value,
        "unit": unit,
        "temporal_context": temporal_context,
        "source_evidence_ids": ["ev-1"],
    }


def run(extractor: ClaimExtractor, question: str, items: list[PdfEvidence]):
    return asyncio.run(extractor.extract(question, items))


def test_extracts_a_simple_factual_statement():
    provider = FakeProvider(answered(
        "Acme Corporation is headquartered in Pune.",
        [claim("Acme Corporation is headquartered in Pune.", entity="Acme Corporation",
               attribute="headquarters", value="Pune")],
    ))

    result = run(ClaimExtractor(provider),
        "Where is Acme headquartered?", [evidence("Acme Corporation is headquartered in Pune.")]
    )

    assert result.status == ClaimExtractionStatus.answered
    assert result.claims[0].entity == "Acme Corporation"
    assert result.claims[0].source_evidence_ids == ["ev-1"]
    assert "one independently verifiable fact" in provider.request.system_prompt


def test_splits_multiple_facts_from_a_compound_sentence():
    source = "Acme was founded in 2010 and employs 50 people."
    provider = FakeProvider(answered(
        "Acme was founded in 2010 and employs 50 people.",
        [
            claim("Acme was founded in 2010.", entity="Acme", attribute="founded", value="2010"),
            claim("Acme employs 50 people.", entity="Acme", attribute="employee count",
                  value="50", unit="people"),
        ],
    ))

    result = run(ClaimExtractor(provider), "Tell me about Acme.", [evidence(source)])

    assert result.status == ClaimExtractionStatus.answered
    assert len(result.claims) == 2
    assert [item.value for item in result.claims] == ["2010", "50"]


def test_preserves_amount_currency_and_units():
    source = "Northstar's annual fee is USD 1,200."
    provider = FakeProvider(answered(
        "Northstar's annual fee is USD 1,200.",
        [claim("Northstar's annual fee is USD 1,200.", entity="Northstar", attribute="annual fee",
               value="1,200", unit="USD")],
    ))

    result = run(ClaimExtractor(provider), "What is Northstar's annual fee?", [evidence(source)])

    assert result.claims[0].value == "1,200"
    assert result.claims[0].unit == "USD"


def test_preserves_date_as_temporal_context():
    source = "The agreement expires on 30 June 2027."
    provider = FakeProvider(answered(
        source,
        [claim(source, entity="agreement", attribute="expiration date", value="30 June 2027",
               temporal_context="30 June 2027")],
    ))

    result = run(ClaimExtractor(provider), "When does it expire?", [evidence(source)])

    assert result.claims[0].temporal_context == "30 June 2027"


def test_rejects_details_not_present_in_evidence():
    provider = FakeProvider(answered(
        "Acme earned 10 million dollars in profit.",
        [claim("Acme earned 10 million dollars in profit.", entity="Acme", attribute="profit",
               value="10 million", unit="dollars")],
    ))

    result = run(ClaimExtractor(provider),
        "What was Acme's profit?", [evidence("Acme employs 50 people.")]
    )

    assert result.status == ClaimExtractionStatus.insufficient_evidence
    assert result.claims == []
    assert result.answer is None


def test_rejects_claim_that_bundles_multiple_amounts():
    source = "Revenue was USD 10,000 in 2024 and USD 20,000 in 2025."
    bundled = "Revenue was USD 10,000 in 2024 and USD 20,000 in 2025."
    provider = FakeProvider(answered(
        source,
        [claim(bundled, entity="Revenue", attribute="amount", value="10,000",
               unit="USD", temporal_context="2024")],
    ))

    result = run(ClaimExtractor(provider), "What was the revenue?", [evidence(source)])

    assert result.status == ClaimExtractionStatus.insufficient_evidence
    assert result.claims == []


def test_ambiguous_evidence_returns_no_claims():
    provider = FakeProvider({
        "status": "ambiguous", "answer": None, "claims": [],
        "reason": "The document contains two different effective dates.",
    })

    result = run(ClaimExtractor(provider),
        "What is the effective date?", [evidence("The agreement lists two dates.")]
    )

    assert result.status == ClaimExtractionStatus.ambiguous
    assert result.claims == []
    assert result.reason


def test_no_evidence_does_not_call_the_provider():
    provider = FakeProvider({})

    result = run(ClaimExtractor(provider), "Any facts?", [])

    assert result.status == ClaimExtractionStatus.insufficient_evidence
    assert provider.request is None
