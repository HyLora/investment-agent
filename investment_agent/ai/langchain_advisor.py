"""Optional LangChain adapter (skeleton).

Prefer :class:`investment_agent.ai.ollama_advisor.OllamaAdvisor` for the
default local-security path (direct HTTP to Ollama, no extra deps).

Install optional extras if you want LangChain::

    pip install -e ".[langchain]"
"""

from __future__ import annotations

from investment_agent.ai.ollama_advisor import OllamaAdvisor


def build_langchain_ollama_advisor(
    model: str = "llama3.2",
    base_url: str = "http://127.0.0.1:11434",
) -> OllamaAdvisor:
    """Return the local Ollama advisor.

    A full LangChain ``ChatOllama`` chain can replace the HTTP client later;
    the explainability contract (MetricEvidence ids) remains identical.
    """
    # Soft-check: if langchain is installed we still reuse OllamaAdvisor so
    # MetricEvidence grounding stays centralized in one parser/prompt.
    try:
        import langchain_community  # noqa: F401
    except ImportError:
        pass
    return OllamaAdvisor(model=model, base_url=base_url)
