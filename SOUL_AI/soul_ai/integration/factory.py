"""Build the integration service with separately instantiated LLM passes."""
from soul_ai.providers import create_configured_provider
from soul_ai.integration.service import SOULAIEngineService


def create_service_from_environment(**service_options) -> SOULAIEngineService:
    """Create claim and independent verification providers from environment config."""
    return SOULAIEngineService(
        claim_provider=create_configured_provider(),
        verifier_provider=create_configured_provider(),
        **service_options,
    )
