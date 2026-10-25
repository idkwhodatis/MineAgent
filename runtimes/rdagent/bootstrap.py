"""Trusted fresh-interpreter setup for the pinned, network-disabled RD runtime.

This is not a process launcher. The supervisor supplies confinement, immutable
inputs, exact runtime identity, and the authenticated broker bootstrap channel.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class OutcomeOnlyStorage:
    """Public upstream storage seam: never inspect/serialize arbitrary objects."""

    def __init__(self, max_events=4096):
        self.max_events = max_events
        self.events = 0

    def log(self, obj, tag="", timestamp=None, **kwargs):
        self.events += 1
        if self.events > self.max_events:
            from packages.research_runtime.control import BrokerAbort

            raise BrokerAbort("budget_exhausted")
        return "discarded-upstream-object"

    def iter_msg(self):
        return iter(())

    def truncate(self, time):
        return None


class BoundedLogSink:
    """Only count upstream text bytes; durable platform evidence is separate."""

    def __init__(self, max_bytes=65536):
        self.max_bytes = max_bytes
        self.bytes_seen = 0

    def write(self, message):
        self.bytes_seen += len(message.encode("utf-8", errors="replace"))
        if self.bytes_seen > self.max_bytes:
            from packages.research_runtime.control import BrokerAbort

            raise BrokerAbort("budget_exhausted")

    def flush(self):
        return None


def configure_upstream(workspace: Path):
    if any(name == "rdagent" or name.startswith("rdagent.") for name in sys.modules):
        raise RuntimeError("RD-Agent requires a fresh trusted interpreter")
    if not workspace.is_absolute() or not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("A pre-admitted absolute workspace directory is required")
    if any(workspace.iterdir()):
        raise ValueError(
            "RD-Agent workspace must start empty; native knowledge is not restored"
        )
    os.chdir(workspace)
    # Set before imports: LiteLLM otherwise tries fetching model prices during
    # import. No credentials, provider discovery, or remote telemetry is needed.
    os.environ.pop("LOG_UI_SERVER_PORT", None)
    os.environ.update(
        {
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "LITELLM_TELEMETRY": "False",
            "DO_NOT_TRACK": "1",
            "CACHE_WITH_PICKLE": "False",
            "ALLOW_UNSAFE_LEGACY_PICKLE": "False",
            "WORKSPACE_PATH": str(workspace / "workspaces"),
            "LOG_TRACE_PATH": str(workspace / "suppressed-logs"),
            "LOG_STORAGES": "{}",
            "BACKEND": "packages.rdagent_adapter.broker_bridge.APIBackend",
            "MULTI_PROC_N": "1",
            "MAX_RETRY": "10",
            "USE_CHAT_CACHE": "False",
            "DUMP_CHAT_CACHE": "False",
            "USE_EMBEDDING_CACHE": "False",
            "DUMP_EMBEDDING_CACHE": "False",
            "LOG_LLM_CHAT_CONTENT": "False",
        }
    )
    from loguru import logger
    from rdagent.core.conf import RD_AGENT_SETTINGS
    from rdagent.log import rdagent_logger
    from rdagent.oai.llm_conf import LLM_SETTINGS

    # Install public storage replacement before importing scenario templates.
    rdagent_logger.storage = OutcomeOnlyStorage()
    rdagent_logger.other_storages = []
    logger.remove()
    logger.add(BoundedLogSink(), format="{level}", catch=False)
    RD_AGENT_SETTINGS.cache_with_pickle = False
    RD_AGENT_SETTINGS.allow_unsafe_legacy_pickle = False
    RD_AGENT_SETTINGS.multi_proc_n = 1
    RD_AGENT_SETTINGS.workspace_path = workspace / "workspaces"
    for field in (
        "use_chat_cache",
        "dump_chat_cache",
        "use_embedding_cache",
        "dump_embedding_cache",
        "log_llm_chat_content",
    ):
        setattr(LLM_SETTINGS, field, False)
    from rdagent.components.coder.factor_coder import FactorCoSTEER

    if RD_AGENT_SETTINGS.cache_with_pickle or RD_AGENT_SETTINGS.multi_proc_n != 1:
        raise RuntimeError("RD-Agent runtime settings differ from frozen profile")
    return FactorCoSTEER
