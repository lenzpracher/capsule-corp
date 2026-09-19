"""Coding-agent runners.

The catalogue never talks to a model vendor directly. It talks to a :class:`Runner`,
so the agent can be swapped without touching capsule storage or verification.
"""

from capsule_corp.runners.base import AgentRequest, AgentResult, Runner, RunnerError, Usage
from capsule_corp.runners.pi import PiRunner, get_runner

__all__ = ["AgentRequest", "AgentResult", "PiRunner", "Runner", "RunnerError", "Usage", "get_runner"]
