"""ADI runtime — localhost HTTP + SSE for agents that talk, see, and act."""
from .channels import SpeechQueue
from .fallback import (AllProvidersFailed, Answer, Chain, Provider,
                       command_provider, echo_provider, http_json_provider)
from .proactive import DEFAULT_TEMPLATES, ProactiveGate
from .server import Runtime

__all__ = [
    "Runtime", "Chain", "Provider", "Answer", "AllProvidersFailed",
    "SpeechQueue", "ProactiveGate", "DEFAULT_TEMPLATES",
    "command_provider", "http_json_provider", "echo_provider",
]
__version__ = "0.1.0"
