"""Generate the checked-in synthetic Phase 6 inputs and separate gold labels."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "data"


ABSENT = [
    ("What was Acme Ltd's annual revenue?", "Acme Ltd profile: registered office Pune; no revenue amount is stated.", "Acme Ltd", "annual revenue"),
    ("Who is Nova Systems' current CEO?", "Nova Systems governance report describes a CEO transition committee but gives no current CEO name.", "Nova Systems", "CEO name"),
    ("What interest rate applies to Orion's loan?", "Orion loan summary lists the lender and principal but omits the interest rate.", "Orion Ltd", "interest rate"),
    ("How many days of notice are required to terminate the lease?", "The lease identifies the property and monthly rent. It does not state a termination notice period.", "Lease", "termination notice period"),
    ("When does the Helios policy begin?", "Helios policy document gives policy number HP-204 but contains no commencement date.", "Helios policy", "commencement date"),
    ("What is Mira Shah's date of birth?", "The borrower is Mira Shah and the loan principal is INR 400,000; no birth date is recorded.", "Mira Shah", "date of birth"),
    ("What is the vendor's bank account number?", "Vendor profile lists the bank name and IFSC code but does not include an account number.", "Vendor", "bank account number"),
    ("How long is the warranty for the Atlas device?", "Atlas device warranty terms list exclusions but do not state a warranty duration.", "Atlas device", "warranty duration"),
    ("What was Juniper's net profit in FY2025?", "Juniper FY2025 report lists revenue and operating expenses; no net profit figure is provided.", "Juniper", "net profit FY2025"),
    ("What is the confirmed delivery date for order 8842?", "Order 8842 is for two printers and has shipping address 12 Lake Road; no delivery date is stated.", "Order 8842", "delivery date"),
    ("How many employees does Pinecone Labs have?", "Pinecone Labs is a software company headquartered in Kochi; its employee count is not given.", "Pinecone Labs", "employee count"),
    ("What is the floor area of the Greenfield property?", "The Greenfield property is at 18 Hill Street. The document gives no floor area.", "Greenfield property", "floor area"),
    ("What GST rate applies to the Solara service?", "Solara service invoice lists GST registration ID 29ABCDE1234F1Z5 but no tax rate.", "Solara service", "GST rate"),
    ("How often must the Vega loan be repaid?", "Vega loan schedule states an amount of INR 90,000 but does not state repayment frequency.", "Vega loan", "repayment frequency"),
    ("Can the Meridian contract be renewed?", "Meridian contract states an end date of 31 December 2027 but contains no renewal option.", "Meridian contract", "renewal option"),
    ("Who is listed as guarantor for borrower Anika Rao?", "Anika Rao is named as borrower and Lake Bank as lender; no guarantor is listed.", "Anika Rao", "guarantor name"),
    ("What late payment penalty applies to the Cedar agreement?", "Cedar agreement says late payments are subject to a penalty but gives no penalty amount.", "Cedar agreement", "late payment penalty amount"),
    ("Which court has jurisdiction over the Northstar contract?", "Northstar contract covers consulting services in India but does not specify a governing court.", "Northstar contract", "jurisdiction"),
    ("What percentage of the Aurora project is complete?", "Aurora project plan gives a final deadline of 30 September 2026; no completion percentage is stated.", "Aurora project", "completion percentage"),
    ("What is the contact phone number for Meadow Supplies?", "Meadow Supplies contact page lists support@meadows.example but no phone number.", "Meadow Supplies", "contact phone number"),
]


CONFLICTS = [
    # group, question, entity, attribute, (value, unit, source text), (value, unit, source text), versions
    ("numerical", "What is Acme Ltd's sanctioned loan amount?", "Acme Ltd", "loan amount",
     ("Rs. 6,00,000", "INR", "Acme Ltd sanctioned loan amount is Rs. 6,00,000."),
     ("Rs. 5,00,000", "INR", "Acme Ltd sanctioned loan amount is Rs. 5,00,000."), None),
    ("numerical", "What is the annual lease amount for Birch House?", "Birch House", "amount",
     ("Rs. 6,00,000", "annually INR", "Birch House annual lease amount is Rs. 6,00,000."),
     ("Rs. 5,40,000", "annually INR", "Birch House annual lease amount is Rs. 5,40,000."), None),
    ("numerical", "What interest rate applies to Cobalt's facility?", "Cobalt Ltd", "interest rate",
     ("9.5%", "percent", "Cobalt Ltd facility interest rate is 9.5 percent."),
     ("10%", "percent", "Cobalt Ltd facility interest rate is 10 percent."), None),
    ("numerical", "What is the late fee in the Delta contract?", "Delta contract", "fee",
     ("INR 25,000", "INR", "Delta contract late fee is INR 25,000."),
     ("INR 30,000", "INR", "Delta contract late fee is INR 30,000."), None),
    ("numerical", "What is the credit limit for Echo Trading?", "Echo Trading", "credit limit",
     ("USD 100,000", "USD", "Echo Trading credit limit is USD 100,000."),
     ("USD 120,000", "USD", "Echo Trading credit limit is USD 120,000."), ("v1", "v2")),
    ("date", "When is the Falcon loan due?", "Falcon loan", "deadline",
     ("30 June 2027", None, "Falcon loan repayment deadline is 30 June 2027."),
     ("15 July 2027", None, "Falcon loan repayment deadline is 15 July 2027."), None),
    ("date", "When does the Grove agreement take effect?", "Grove agreement", "effective date",
     ("1 January 2025", None, "Grove agreement effective date is 1 January 2025."),
     ("15 January 2025", None, "Grove agreement effective date is 15 January 2025."), None),
    ("date", "What is the deadline for the Harbor submission?", "Harbor submission", "deadline",
     ("2026-03-01", None, "Harbor submission deadline is 2026-03-01."),
     ("2026-03-10", None, "Harbor submission deadline is 2026-03-10."), None),
    ("date", "When does the Indigo policy expire?", "Indigo policy", "expiration date",
     ("31 December 2026", None, "Indigo policy expires on 31 December 2026."),
     ("31 December 2027", None, "Indigo policy expires on 31 December 2027."), None),
    ("date", "When must the Juniper filing be submitted?", "Juniper filing", "due date",
     ("30 April 2025", None, "Juniper filing is due on 30 April 2025."),
     ("31 May 2025", None, "Juniper filing is due on 31 May 2025."), None),
    ("identity", "Who is the borrower in the Kestrel agreement?", "Kestrel agreement", "borrower name",
     ("Asha Menon", None, "Kestrel agreement names Asha Menon as borrower."),
     ("Riya Menon", None, "Kestrel agreement names Riya Menon as borrower."), None),
    ("identity", "Who is listed as director of Lumen Works?", "Lumen Works", "director name",
     ("Daniel Ortiz", None, "Lumen Works filing lists Daniel Ortiz as director."),
     ("Daniel Ortega", None, "Lumen Works filing lists Daniel Ortega as director."), None),
    ("identity", "What is the legal name of borrower Maple Retail?", "Maple Retail", "borrower name",
     ("Maple Retail Private Limited", None, "Loan schedule names Maple Retail Private Limited as borrower."),
     ("Maple Retail LLP", None, "Loan schedule names Maple Retail LLP as borrower."), None),
    ("identity", "Who is the guarantor for Nacre Industries?", "Nacre Industries", "guarantor name",
     ("Priya Shah", None, "Nacre Industries agreement names Priya Shah as guarantor."),
     ("Prisha Shah", None, "Nacre Industries agreement names Prisha Shah as guarantor."), None),
    ("identity", "Who is the authorized signatory for Opal Services?", "Opal Services", "authorized signatory",
     ("Kiran Das", None, "Opal Services register lists Kiran Das as authorized signatory."),
     ("Kiran Dutta", None, "Opal Services register lists Kiran Dutta as authorized signatory."), None),
]


CONSISTENT = [
    ("What is the annual subscription amount for Quartz Cloud?", "Quartz Cloud", "amount",
     ("Rs. 6,00,000", "annually INR", "Quartz Cloud subscription amount is Rs. 6,00,000 annually."),
     ("Rs. 50,000", "monthly INR", "Quartz Cloud subscription amount is Rs. 50,000 monthly.")),
    ("When is the Redwood lease due?", "Redwood lease", "deadline",
     ("30 June 2027", None, "Redwood lease deadline is 30 June 2027."),
     ("June 30, 2027", None, "Redwood lease deadline is June 30, 2027.")),
    ("Who is the borrower in the Saffron agreement?", "Saffron agreement", "borrower name",
     ("Aarav Mehta", None, "Saffron agreement identifies Aarav Mehta as borrower."),
     ("Aarav Mehta", None, "Saffron schedule identifies Aarav Mehta as borrower.")),
    ("What interest rate applies to the Topaz loan?", "Topaz loan", "interest rate",
     ("8.5%", "percent", "Topaz loan interest rate is 8.5 percent."),
     ("8.5 percent", None, "The interest rate for Topaz loan is 8.5 percent.")),
    ("What is the principal amount for Umber Finance?", "Umber Finance", "loan amount",
     ("5 lakh", "INR", "Umber Finance principal amount is INR 5 lakh."),
     ("Rs. 500,000", None, "Umber Finance loan amount is Rs. 500,000.")),
]


def _evidence(case_id: str, document_index: int, filename_stem: str, text: str, page: int = 1) -> dict:
    doc_id = f"{case_id}-D{document_index}"
    filename = f"{filename_stem}.pdf"
    evidence_id = f"{case_id}-E{document_index}"
    return {
        "evidence_id": evidence_id,
        "document_id": doc_id,
        "filename": filename,
        "page_number": page,
        "extracted_text": text,
        "start_offset": 0,
        "end_offset": len(text),
        "extraction_method": "text",
        "confidence": None,
    }


def build() -> tuple[dict, dict]:
    inputs: list[dict] = []
    labels: list[dict] = []
    counter = 1

    for question, text, entity, attribute in ABSENT:
        case_id = f"C{counter:03d}"
        item = _evidence(case_id, 1, f"{case_id.lower()}_reference", text)
        inputs.append({"case_id": case_id, "question": question, "evidence": [item], "document_versions": {}})
        labels.append({
            "case_id": case_id,
            "category": "unsupported_absent",
            "expected_outcome": "insufficient_evidence",
            "expected_conflict": False,
            "expected_conflict_type": None,
            "expected_facts": [],
            "unsupported_target": {"entity": entity, "attribute": attribute},
        })
        counter += 1

    for group, question, entity, attribute, first, second, versions in CONFLICTS:
        case_id = f"C{counter:03d}"
        evidence_items = [
            _evidence(case_id, 1, f"{case_id.lower()}_source_a", first[2]),
            _evidence(case_id, 2, f"{case_id.lower()}_source_b", second[2], 2),
        ]
        doc_versions = {}
        if versions:
            doc_versions = {evidence_items[0]["document_id"]: versions[0], evidence_items[1]["document_id"]: versions[1]}
        inputs.append({"case_id": case_id, "question": question, "evidence": evidence_items,
                       "document_versions": doc_versions})
        labels.append({
            "case_id": case_id,
            "category": f"{group}_contradiction",
            "expected_outcome": "conflict",
            "expected_conflict": True,
            "expected_conflict_type": group,
            "expected_facts": [
                {"document_id": evidence_items[0]["document_id"], "entity": entity, "attribute": attribute,
                 "value": first[0], "unit": first[1], "snippet": first[2], "filename": evidence_items[0]["filename"],
                 "page_number": evidence_items[0]["page_number"]},
                {"document_id": evidence_items[1]["document_id"], "entity": entity, "attribute": attribute,
                 "value": second[0], "unit": second[1], "snippet": second[2], "filename": evidence_items[1]["filename"],
                 "page_number": evidence_items[1]["page_number"]},
            ],
        })
        counter += 1

    for question, entity, attribute, first, second in CONSISTENT:
        case_id = f"C{counter:03d}"
        evidence_items = [
            _evidence(case_id, 1, f"{case_id.lower()}_source_a", first[2]),
            _evidence(case_id, 2, f"{case_id.lower()}_source_b", second[2], 2),
        ]
        inputs.append({"case_id": case_id, "question": question, "evidence": evidence_items,
                       "document_versions": {}})
        labels.append({
            "case_id": case_id,
            "category": "consistent_fact",
            "expected_outcome": "answered",
            "expected_conflict": False,
            "expected_conflict_type": None,
            "expected_facts": [
                {"document_id": evidence_items[0]["document_id"], "entity": entity, "attribute": attribute,
                 "value": first[0], "unit": first[1], "snippet": first[2], "filename": evidence_items[0]["filename"],
                 "page_number": evidence_items[0]["page_number"]},
                {"document_id": evidence_items[1]["document_id"], "entity": entity, "attribute": attribute,
                 "value": second[0], "unit": second[1], "snippet": second[2], "filename": evidence_items[1]["filename"],
                 "page_number": evidence_items[1]["page_number"]},
            ],
        })
        counter += 1

    # Opaque deterministic IDs and shuffled row order avoid leaking label groups
    # through case ID ranges, filenames, evidence IDs, or processing order.
    rng = random.Random(20261123)
    old_ids = [f"C{index:03d}" for index in range(1, counter)]
    new_numbers = list(range(1, counter))
    rng.shuffle(new_numbers)
    remap = {old: f"T{new:03d}" for old, new in zip(old_ids, new_numbers)}

    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, str):
            return value
        if value in remap:
            return remap[value]
        upper = re.match(r"^(C\d{3})(.*)$", value)
        if upper:
            return remap[upper.group(1)] + upper.group(2)
        lower = re.match(r"^(c\d{3})(.*)$", value)
        if lower:
            return remap[lower.group(1).upper()].lower() + lower.group(2)
        return value

    inputs = rewrite(inputs)
    labels = rewrite(labels)
    pairs = list(zip(inputs, labels))
    rng.shuffle(pairs)
    inputs = [pair[0] for pair in pairs]
    labels = [pair[1] for pair in pairs]
    return (
        {"dataset_version": "1.0.0", "cases": inputs},
        {"dataset_version": "1.0.0", "cases": labels},
    )


def main() -> None:
    inputs, labels = build()
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(inputs['cases'])} input cases and separate labels to {ROOT}")


if __name__ == "__main__":
    main()
