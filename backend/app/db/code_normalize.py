"""Single-function leaf utility: canonical clinical-code normalisation.

Lifted out of ``app.evaluation.benchmark_aggregate`` so callers that only
need the normaliser do not transitively pull in numpy / scipy /
statsmodels at import time. The function and its docstring are the
canonical source of truth; ``benchmark_aggregate`` re-exports for
back-compat with existing eval scripts.
"""
from __future__ import annotations


def normalize_code(code: str, vocabulary: str) -> str:
    """Code normalization for fair set comparison.

    Strips whitespace and all dots, vocabulary-blind. The same
    transformation is applied to both reference and output codes, so
    OPCS-4 codes that carry dots (like "K40.1") are mutated
    symmetrically -- set membership is preserved either way. SNOMED CT
    has no dots so the strip is a no-op.

    This matches ``evaluator._norm`` exactly so the live
    ``/api/evaluate`` and the offline aggregator agree on every
    metric. The same rule is applied by the HDR UK cross-reference
    panel (T35) so its Jaccard overlap percentages are directly
    comparable to the project's F1 numbers.

    The ``vocabulary`` parameter is retained for API compatibility
    and may be used by future per-vocabulary normalization rules,
    but is currently ignored.
    """
    return (code or "").strip().replace(".", "")
