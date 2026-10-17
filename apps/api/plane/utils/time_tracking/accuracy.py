# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S7 — estimate-accuracy metrics (pure functions, no queries).

DESIGN §2 item 36 and the PO answers (binding thresholds):

* **Population**: completed work items (state group ``completed``; Cancelled never) that
  have an estimate, with some time logged. The estimate is judged by its **original**
  value (the one set before work started); the **current** value (after re-estimates) is
  reported beside it.
* **Per task**: ``ratio = actual / original`` and ``error % = (actual - original) / original``.
* **Within target**: ``|error| <= 20 %`` (the boundary counts as accurate).
* **Mostly solo**: the original estimator logged ``>= 80 %`` of the task's time.
* **Trend**: by completion date in the workspace timezone, per week (Monday start) or
  month; a bucket with fewer than 5 tasks is flagged ``low_sample``.

All comparisons against the thresholds are done in integers (no float rounding at the
boundary); only the reported ratios / percentages are rounded.
"""

# Python imports
from datetime import date, timedelta
from statistics import median

WITHIN_PCT = 20
SOLO_PCT = 80
MIN_SAMPLE = 5

GRANULARITIES = ("week", "month")


def is_within(original, actual, pct=WITHIN_PCT):
    """``|actual - original| <= pct % of original`` (integer arithmetic)."""
    return abs(actual - original) * 100 <= pct * original


def is_solo(estimator_minutes, actual, pct=SOLO_PCT):
    """The estimator logged at least ``pct`` % of the task's time (``actual`` > 0)."""
    return actual > 0 and estimator_minutes * 100 >= pct * actual


def error_pct(original, actual):
    """Signed error in percent of ``original`` (positive = took longer than estimated)."""
    return (actual - original) * 100.0 / original


def ratio(actual, original):
    return actual / original if original else None


def _round(value, places):
    return None if value is None else round(value, places)


def task_metrics(original, current, actual, estimator_minutes):
    """Per-task numbers, all rounded for output."""
    return {
        "ratio": _round(ratio(actual, original), 3),
        "error_pct": _round(error_pct(original, actual), 1),
        "within": is_within(original, actual),
        "ratio_current": _round(ratio(actual, current), 3),
        "error_pct_current": _round(error_pct(current, actual), 1) if current else None,
        "within_current": is_within(current, actual) if current else None,
        "estimator_share": _round(estimator_minutes / actual, 3) if actual else None,
        "solo": is_solo(estimator_minutes, actual),
    }


def summarize(rows):
    """Aggregate metrics for a set of task rows.

    Each row needs integer ``original_minutes``, ``current_minutes``, ``actual_minutes``
    and ``estimator_minutes`` (rows with ``original_minutes`` 0 can't exist: the
    estimate is at least one minute). ``n == 0`` gives null ratios.
    """
    n = len(rows)
    original = sum(r["original_minutes"] for r in rows)
    current = sum(r["current_minutes"] for r in rows)
    actual = sum(r["actual_minutes"] for r in rows)
    errors = [abs(error_pct(r["original_minutes"], r["actual_minutes"])) for r in rows]
    errors_current = [abs(error_pct(r["current_minutes"], r["actual_minutes"])) for r in rows if r["current_minutes"]]
    within = sum(1 for r in rows if is_within(r["original_minutes"], r["actual_minutes"]))
    solo = sum(1 for r in rows if is_solo(r["estimator_minutes"], r["actual_minutes"]))
    return {
        "n": n,
        "original_minutes": original,
        "current_minutes": current,
        "actual_minutes": actual,
        "ratio": _round(ratio(actual, original), 3),
        "error_pct": _round(error_pct(original, actual), 1) if original else None,
        "ratio_current": _round(ratio(actual, current), 3),
        "median_abs_error_pct": _round(median(errors), 1) if errors else None,
        "median_abs_error_pct_current": _round(median(errors_current), 1) if errors_current else None,
        "within_count": within,
        "within_share": _round(within / n, 3) if n else None,
        "solo_count": solo,
        "low_sample": n < MIN_SAMPLE,
    }


def bucket_start(day, granularity):
    """First day of the week (Monday) or month ``day`` falls in."""
    if granularity == "month":
        return date(day.year, day.month, 1)
    return day - timedelta(days=day.isoweekday() - 1)


def parse_range(params, today, max_days):
    """Inclusive workspace-local completion-date range ``[from, to]`` (``ValueError`` on bad input).

    Defaults to the 90 days ending today. ``from`` alone runs to today; ``to`` alone covers
    the 90 days before it. ``start_date`` / ``end_date`` are accepted as in the S5 report.
    """
    raw_from = params.get("from") or params.get("start_date")
    raw_to = params.get("to") or params.get("end_date")
    try:
        end = date.fromisoformat(raw_to) if raw_to else today
        start = date.fromisoformat(raw_from) if raw_from else end - timedelta(days=89)
    except (TypeError, ValueError):
        raise ValueError("from and to must be dates (YYYY-MM-DD).")
    if end < start:
        raise ValueError("to must be on or after from.")
    if (end - start).days + 1 > max_days:
        raise ValueError(f"The range can be at most {max_days} days.")
    return start, end
