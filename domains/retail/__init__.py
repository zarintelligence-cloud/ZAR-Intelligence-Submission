"""ZAR Retail Pack - Custom Apparel. Registers capabilities and exposes the workflow handlers."""
from zar_core.contracts import DomainInfo
from zar_core.registry import CapabilityRegistry
from .capabilities import handoff, hubble, horizon, kepler, pleiades, rosetta, spectra, voice
from .workflow_handlers import HANDLERS as WORKFLOW_HANDLERS

_PROVIDERS = {"requirements_analysis": rosetta.run, "artwork_analysis": spectra.run,
              "proof_generation": hubble.run, "roster_management": pleiades.run, "pricing": kepler.run,
              "fulfillment_confidence": horizon.run, "compose_reply": voice.run,
              "order_handoff": handoff.run}


def register(registry: CapabilityRegistry, info: DomainInfo) -> None:
    for capability, provider_name in info.capabilities.items():
        registry.register(info.id, capability, _PROVIDERS[capability], provider=provider_name)


__all__ = ["register", "WORKFLOW_HANDLERS"]
