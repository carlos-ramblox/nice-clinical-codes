"""T37: smoke test that dm+d and BNF retrievers join the parallel fan-out."""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.graph import _RETRIEVERS, RETRIEVER_NAMES, build_graph  # noqa: E402


def test_dmd_and_bnf_registered_in_retriever_table():
    assert "dmd" in _RETRIEVERS
    assert "bnf" in _RETRIEVERS
    assert _RETRIEVERS["dmd"][0] == "dmd_retriever"
    assert _RETRIEVERS["bnf"][0] == "bnf_retriever"


def test_retriever_names_alias_mirrors_private_dict():
    assert RETRIEVER_NAMES == frozenset(_RETRIEVERS)


def test_build_graph_accepts_dmd_and_bnf_in_disabled_set():
    graph = build_graph(disabled_retrievers={"dmd", "bnf"})
    assert graph is not None


def test_build_graph_rejects_unknown_retriever_name():
    import pytest
    with pytest.raises(ValueError, match="Unknown retriever name"):
        build_graph(disabled_retrievers={"this-retriever-does-not-exist"})


def test_build_graph_rejects_disabling_every_retriever():
    import pytest
    with pytest.raises(ValueError, match="Cannot disable all retrievers"):
        build_graph(disabled_retrievers=set(_RETRIEVERS.keys()))
