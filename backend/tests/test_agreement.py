"""
Pin Cohen's kappa and the Landis & Koch banding for T30.

Tests verify the formula at three representative levels (perfect
agreement, total disagreement, chance-level agreement), the load-
bearing edge cases (empty input, single-category collapse, length
mismatch), and the qualitative label table.

Fixtures use 4-item lists so the arithmetic is verifiable at a
glance — a 50-item construction is large enough that the integer
split makes exactly κ=0 unreachable (2x = 25 has no integer
solution), and an inexact zero would mask the simpler ``test_pe``
edge cases.

Run from backend/:
    pytest tests/test_agreement.py -v
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.agreement import cohen_kappa, landis_koch_label  # noqa: E402


# ---------------------------------------------------------------------------
# cohen_kappa: formula at three representative levels
# ---------------------------------------------------------------------------


def test_perfect_agreement_returns_one() -> None:
    """If every item agrees, κ = 1 regardless of marginal distribution.

    Construction (4 items)::
        a = [include, include, exclude, exclude]
        b = [include, include, exclude, exclude]
        po = 4/4 = 1
        pe = 0.5 * 0.5 + 0.5 * 0.5 = 0.5
        κ  = (1 - 0.5) / (1 - 0.5) = 1
    """
    a = ["include", "include", "exclude", "exclude"]
    b = ["include", "include", "exclude", "exclude"]
    assert cohen_kappa(a, b) == 1.0


def test_inverted_agreement_with_balanced_marginals_returns_minus_one() -> None:
    """Totally inverted decisions on a balanced 50/50 split give κ = -1.

    Construction (4 items)::
        a = [include, include, exclude, exclude]
        b = [exclude, exclude, include, include]
        po = 0/4 = 0
        pa(I) = 0.5, pa(E) = 0.5
        pb(I) = 0.5, pb(E) = 0.5
        pe = 0.5 * 0.5 + 0.5 * 0.5 = 0.5
        κ  = (0 - 0.5) / (1 - 0.5) = -1
    """
    a = ["include", "include", "exclude", "exclude"]
    b = ["exclude", "exclude", "include", "include"]
    assert cohen_kappa(a, b) == -1.0


def test_chance_agreement_returns_zero() -> None:
    """Agreement at the chance level gives κ = 0.

    Construction (4 items, exact arithmetic)::
        a = [include, include, exclude, exclude]   ->  50% I / 50% E
        b = [include, exclude, include, exclude]   ->  50% I / 50% E
        Per-position: I/I (agree), I/E, E/I, E/E (agree) -> 2 agreements
        po = 2/4 = 0.5
        pe = (0.5 * 0.5) + (0.5 * 0.5) = 0.5
        κ  = (0.5 - 0.5) / (1 - 0.5) = 0
    """
    a = ["include", "include", "exclude", "exclude"]
    b = ["include", "exclude", "include", "exclude"]
    assert cohen_kappa(a, b) == 0.0


# ---------------------------------------------------------------------------
# cohen_kappa: edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_none() -> None:
    """``None`` (not 0, not NaN) when there is no data — empty inputs
    are insufficient data, not an error. The UI surfaces this as
    ``kappa: n/a`` via ``landis_koch_label(None)``."""
    assert cohen_kappa([], []) is None


def test_single_category_collapse_returns_one_not_nan() -> None:
    """Both reviewers concentrate on the same single label: ``pe = 1``,
    the standard formula divides 0 by 0. Convention is 1.0 (perfect
    agreement) — silent NaN would propagate to the UI as
    ``kappa: NaN``, which is worse than misleading and would also
    poison any downstream aggregation."""
    a = ["include", "include", "include", "include"]
    b = ["include", "include", "include", "include"]
    result = cohen_kappa(a, b)
    assert result == 1.0
    assert isinstance(result, float)
    assert not math.isnan(result)


def test_length_mismatch_raises_value_error() -> None:
    """A length mismatch is a caller-side bug — silently
    ``zip``-truncating to the shorter list would mask it. Fail loud
    so ``submit_review``-side callers see the bug at first occurrence,
    not at "why is the kappa off?" time."""
    with pytest.raises(ValueError, match="length mismatch"):
        cohen_kappa(["include"], ["include", "exclude"])


# ---------------------------------------------------------------------------
# landis_koch_label: one row per band + None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kappa, expected_label",
    [
        (-0.5, "poor"),
        (0.10, "slight"),
        (0.30, "fair"),
        (0.50, "moderate"),
        (0.70, "substantial"),
        (0.90, "almost perfect"),
        (None, "n/a"),
    ],
)
def test_landis_koch_label_bands(kappa: float | None, expected_label: str) -> None:
    """One row per Landis & Koch band, plus the ``None`` case. The
    boundary values (0.21, 0.41, 0.61, 0.81) are intentionally not
    exercised here — picked mid-band values to keep the test
    resilient to inclusive-vs-exclusive-boundary debates that the
    original 1977 paper does not settle either way."""
    assert landis_koch_label(kappa) == expected_label
