"""Canonical tool-surface dump harness for the tools-consolidation parity gate (V3).

TEST-ONLY HISTORICAL PARITY PROBE — not a runtime shim and not imported by any
product code. It exists only to certify that the tools consolidation is
schema-neutral: boot an ``Agent`` with default capabilities plus opt-in vision +
web_search in a throwaway workdir with a stubbed LLM service, then serialise the
full built-in tool surface — every tool name, description, and parameter schema
(including the injected ``reasoning`` property) — plus ``get_all_providers()``
metadata, as deterministic JSON.

Run at the base commit and again on the candidate; the two JSON blobs must be
byte-identical. To compare *both* trees with one file, this harness is
import-path agnostic: it resolves ``get_all_providers`` from ``tools.registry``
(post-move), with a deliberate test-only fallback to the now-removed
``lingtai.capabilities`` for the pre-move base-commit leg of the gate only. That
fallback is historical-parity plumbing — it must not be broadened or reused as a
runtime import shim.

Usage:
    python -m tests._tool_surface_dump > /path/to/parity.json
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


def _make_mock_service() -> MagicMock:
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


def _get_all_providers():
    """Resolve get_all_providers from whichever registry module is present.

    Test-only: ``tools.registry`` is the post-move home; the
    ``lingtai.capabilities`` fallback exists solely so this parity harness can
    run unchanged against the pre-move base commit. Not a runtime shim.
    """
    try:
        from tools.registry import get_all_providers  # type: ignore
    except Exception:
        from lingtai.capabilities import get_all_providers  # type: ignore
    return get_all_providers()


def _schema_to_dict(schema) -> dict:
    return {
        "name": schema.name,
        "description": schema.description,
        "parameters": schema.parameters,
    }


def _fake_vision_service():
    """A concrete VisionService subclass so vision registers without a real key.

    The tool *schema* (what parity compares) is independent of the service, so a
    stub is faithful: it exercises exactly the ``add_tool`` path a live provider
    would, deterministically and offline.
    """
    from lingtai.services.vision import VisionService

    class _FakeVision(VisionService):
        def analyze_image(self, image_path, prompt=None):
            return "stub"

    return _FakeVision()


def _fake_search_service():
    from lingtai.services.websearch import SearchService, SearchResult

    class _FakeSearch(SearchService):
        def search(self, query, max_results=5):
            return [SearchResult(title="t", url="u", snippet="s")]

    return _FakeSearch()


def dump() -> dict:
    from lingtai.agent import Agent

    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td) / "agent"
        agent = Agent(
            service=_make_mock_service(),
            agent_name="parity",
            working_dir=workdir,
            capabilities={
                "vision": {"vision_service": _fake_vision_service()},
                "web_search": {"search_service": _fake_search_service()},
            },
        )
        try:
            schemas = agent._build_tool_schemas()
            # Report which of the opt-in caps actually registered, so a parity
            # mismatch caused by provider availability is visible rather than silent.
            registered = sorted(name for name, _ in agent._capabilities)
            surface = {
                "tool_schemas": sorted(
                    (_schema_to_dict(s) for s in schemas),
                    key=lambda d: d["name"],
                ),
                "registered_capabilities": registered,
                "providers": _get_all_providers(),
            }
        finally:
            agent.stop(timeout=2.0)
    return surface


def main() -> int:
    surface = dump()
    json.dump(surface, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
