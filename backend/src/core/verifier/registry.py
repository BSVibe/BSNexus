"""Verifier registry — open mapping ``VerifierType → Verifier``."""

from __future__ import annotations

from backend.src.core.verifier.protocol import Verifier, VerifierType


class VerifierNotRegisteredError(KeyError):
    """Raised when an envelope's ``verifier_type`` has no registered verifier."""


class VerifierRegistry:
    """Type → implementation routing for the worker.

    The registry is mutable and can grow at runtime (or at lifespan
    setup). Each :class:`Verifier` advertises which ``VerifierType``
    values it accepts via its ``verifier_types`` attribute; ``register``
    indexes the verifier under each one.
    """

    def __init__(self) -> None:
        self._by_type: dict[VerifierType, Verifier] = {}

    def register(self, verifier: Verifier) -> None:
        if not verifier.verifier_types:
            raise ValueError(f"Verifier {type(verifier).__name__} declares no verifier_types")
        for vt in verifier.verifier_types:
            self._by_type[vt] = verifier

    def resolve(self, verifier_type: VerifierType) -> Verifier:
        try:
            return self._by_type[verifier_type]
        except KeyError as exc:
            raise VerifierNotRegisteredError(f"No verifier registered for {verifier_type.value!r}") from exc

    def supported_types(self) -> frozenset[VerifierType]:
        return frozenset(self._by_type.keys())


# Process-wide default registry. Tests construct their own instance to
# stay isolated; production wiring registers concrete verifiers in
# ``main.py``'s lifespan.
default_registry = VerifierRegistry()
