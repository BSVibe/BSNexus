"""``backend.src.core.llm`` — greenfield home for the litellm-direct
LLM dispatch path (``executor_type=llm_api``).

Per CLAUDE.md MUST rule "Two-path LLM dispatch", the ``litellm``
import is fenced to this package — every other module talks to the
LLM through the resolver-returned client object (``BSGatewayClient``
or ``DirectLLMAdapter``). The fence is enforced by
``test_litellm_is_fenced_to_core_llm`` in the legacy-erasure suite.
"""

from backend.src.core.llm.direct_client import DirectLLMAdapter, DirectLLMError

__all__ = ["DirectLLMAdapter", "DirectLLMError"]
