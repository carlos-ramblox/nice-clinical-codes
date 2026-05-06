"""
Inter-rater agreement metrics for two-reviewer Delphi adjudication (T30).

Pure-Python, no I/O. Lives under services/ rather than db/ because
agreement is a computation, not a persistence concern; ``hitl_store``
imports it on demand when the second reviewer's votes complete.

Categories are the codelist-decision values: ``include`` / ``exclude``
/ ``uncertain``. The functions here treat the labels as nominal and
unweighted — a weighted Cohen's kappa (treating include↔uncertain
disagreement as less severe than include↔exclude) is defensible but
forces a contestable weight choice; Watson 2017 Stage 3 uses
unweighted, so we ship that first. A ``cohen_kappa_weighted`` is the
natural extension if the methods paper later requires it.

Confidence intervals on κ (SE(κ), bootstrap) are not implemented here
— they are a methods-paper deliverable and can land in a follow-up.

References
----------
1. Cohen, J. (1960). A coefficient of agreement for nominal scales.
   *Educational and Psychological Measurement*, 20(1), 37-46.
2. Landis, J. R., & Koch, G. G. (1977). The measurement of observer
   agreement for categorical data. *Biometrics*, 33(1), 159-174.
3. McHugh, M. L. (2012). Interrater reliability: the kappa statistic.
   *Biochemia Medica*, 22(3), 276-282.
   https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/
"""
from __future__ import annotations


def cohen_kappa(
    decisions_a: list[str], decisions_b: list[str]
) -> float | None:
    """Cohen's kappa for two reviewers, unweighted, nominal categories.

    Parameters
    ----------
    decisions_a, decisions_b
        Equal-length sequences of vote labels in matched order — the
        i-th element of each list is the two reviewers' votes on the
        same code. Categories are not validated here; the DB CHECK
        constraint on ``decision_votes.vote`` enforces the
        ``include`` / ``exclude`` / ``uncertain`` set upstream.

    Returns
    -------
    float | None
        ``None`` when the inputs are empty (insufficient data is not
        an error). Otherwise a float in [-1, 1]: 1 is perfect
        agreement, 0 is chance-level agreement, negative values
        indicate worse-than-chance agreement.
        Returns 1.0 when both reviewers concentrate on the same single
        label — the standard formula's denominator is 0 in that case
        (``pe == 1``); the McHugh 2012 convention is that perfect
        agreement is perfect agreement and the chance-adjustment is
        undefined but the result is 1.0.

    Raises
    ------
    ValueError
        If the two lists have different lengths. This is a caller-side
        bug — the caller is responsible for matching reviewer A's vote
        with reviewer B's vote on the same code, in the same order.
        Fail loud rather than silently zip-truncate.
    """
    if len(decisions_a) != len(decisions_b):
        raise ValueError(
            f"length mismatch between reviewer vote lists: "
            f"a={len(decisions_a)}, b={len(decisions_b)}"
        )
    n = len(decisions_a)
    if n == 0:
        return None

    # Observed agreement: fraction of items where both reviewers gave
    # the same label.
    po = sum(1 for x, y in zip(decisions_a, decisions_b) if x == y) / n

    # Expected agreement under independent chance: dot product of the
    # two label distributions over the union of observed labels. A
    # label that appears in only one list contributes 0 to this sum
    # (one of pa, pb is zero), so iterating over the union is
    # equivalent to iterating over the fixed three-label set.
    labels = set(decisions_a) | set(decisions_b)
    pe = 0.0
    for label in labels:
        pa = decisions_a.count(label) / n
        pb = decisions_b.count(label) / n
        pe += pa * pb

    # ``pe == 1`` happens iff both reviewers concentrate on the same
    # single label (Cauchy-Schwarz equality case). In that case
    # ``po == 1`` too — every item is the same on both sides — and
    # the ratio ``(1 - 1) / (1 - 1)`` is 0/0, undefined. Convention
    # per McHugh 2012: return 1.0. The epsilon guards against
    # accumulated floating-point error when ``pe`` approaches 1 but
    # doesn't quite hit it; in the exact-1 case (counts of 1.0) it's
    # belt-and-braces.
    if abs(1.0 - pe) < 1e-12:
        return 1.0

    return (po - pe) / (1.0 - pe)


def landis_koch_label(kappa: float | None) -> str:
    """Return the Landis & Koch (1977) qualitative label for a kappa.

    Bands::

        kappa <  0          -> "poor"
        0    - 0.20         -> "slight"
        0.21 - 0.40         -> "fair"
        0.41 - 0.60         -> "moderate"
        0.61 - 0.80         -> "substantial"
        0.81 - 1.00         -> "almost perfect"
        None                -> "n/a"

    The 0.41 boundary is the UI's amber-warning threshold (anything
    below "moderate" surfaces a Watson 2017 Stage 3 warning per the
    persona audit). Pinning the table here keeps the threshold
    co-located with the metric definition rather than scattered
    across the frontend; the UI imports the label and renders the
    band, no client-side numeric thresholds.
    """
    if kappa is None:
        return "n/a"
    if kappa < 0:
        return "poor"
    if kappa < 0.21:
        return "slight"
    if kappa < 0.41:
        return "fair"
    if kappa < 0.61:
        return "moderate"
    if kappa < 0.81:
        return "substantial"
    return "almost perfect"
