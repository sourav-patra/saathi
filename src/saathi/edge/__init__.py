"""The public edge (M07): signature, size and parse checks, then one transaction per message, then 200.

The edge holds no private key and calls no model. It inserts, and it calls three security-definer functions that
return an id, a time or the core heartbeat (`resolve_sender`, `rate_state`, `beat`).
"""

from saathi.edge.adapters import InboundAdapter, TelegramElderInbound, WhatsAppInbound
from saathi.edge.app import create_edge_app
from saathi.edge.handler import EdgeCounters, EdgeDeps, handle_inbound
from saathi.edge.ratelimit import rate_state

__all__ = [
    "EdgeCounters",
    "EdgeDeps",
    "InboundAdapter",
    "TelegramElderInbound",
    "WhatsAppInbound",
    "create_edge_app",
    "handle_inbound",
    "rate_state",
]
