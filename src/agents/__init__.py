"""Multi-agent review system (Phase 4).

Agents are organized as small, single-responsibility modules orchestrated via
LangGraph. The coordinator fans out work to specialized review agents in
parallel and merges their findings before ranking.
"""

from __future__ import annotations

__all__: list[str] = []
