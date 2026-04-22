"""Prompt composition layer.

BSage owns knowledge search; BSNexus owns prompt assembly. A run's
composition is:

    fragments = await knowledge.search(intent)     # BSage or Noop
    composition = assembler.compose(run, fragments, template)

The composition is persisted as a CompositionSnapshot and referenced
from the ExecutionRun.
"""

from backend.src.core.composer.knowledge_client import (
    BSageKnowledgeClient,
    KnowledgeClient,
    KnowledgeFragment,
    NoopKnowledgeClient,
    resolve_knowledge_client,
)
from backend.src.core.composer.prompt_assembler import (
    Composition,
    PersonaTemplate,
    PersonaTemplateRegistry,
    PromptAssembler,
    default_template_registry,
)

__all__ = [
    "BSageKnowledgeClient",
    "Composition",
    "KnowledgeClient",
    "KnowledgeFragment",
    "NoopKnowledgeClient",
    "PersonaTemplate",
    "PersonaTemplateRegistry",
    "PromptAssembler",
    "default_template_registry",
    "resolve_knowledge_client",
]
