from soul_ai.api.contracts import ResponseStatus, VerificationRequest, VerificationResponse


def test_verification_request_round_trips_through_json():
    request = VerificationRequest(
        request_id="req-1",
        question="What is the effective date?",
        document_ids=["doc-1"],
    )

    restored = VerificationRequest.model_validate_json(request.model_dump_json())

    assert restored == request


def test_rejected_response_has_no_success_payload():
    response = VerificationResponse(
        request_id="req-2",
        status=ResponseStatus.rejected,
        rejected_reason="No relevant evidence was found.",
    )

    payload = response.model_dump(mode="json")

    assert payload["status"] == "rejected"
    assert payload["answer"] is None
    assert payload["claims"] == []
