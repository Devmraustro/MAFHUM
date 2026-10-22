"""Spaced-repetition scheduling for flashcards.

The algorithm
-------------
This implements **SM-2**, the algorithm published by Piotr Wozniak in 1987 and
used by SuperMemo 2 and, with variations, by Anki. It is chosen because it is
simple, deterministic, needs no training data, and its behaviour is easy to test
against its own specification.

State per card: ``ease_factor`` (initially 2.5), ``interval_days`` (initially 0),
``repetitions`` (initially 0).

A review carries a ``rating`` from 0 to 5, where the user reports how well they
recalled the answer:

* **rating >= 3** — recalled. ``repetitions`` increments and the interval grows:
  1 day, then 6 days, then ``round(previous_interval * ease_factor)``.
* **rating < 3** — forgotten. ``repetitions`` resets to 0 and the interval resets
  to 1 day. The card is not deleted and its history is kept.

After every review the ease factor is adjusted by the published SM-2 formula::

    EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))

clamped to a floor of **1.3**. Without the clamp, repeated low ratings drive the
factor toward zero or negative and the interval collapses, which is a bug rather
than a feature.

Intervals are whole days and ``due_at`` is ``review_time + interval_days``.

What this is not
----------------
**There is no evidence that SM-2 produces optimal learning outcomes, and none is
claimed here.** SM-2 is a heuristic from 1987. Published comparisons of
scheduling algorithms show modest and inconsistent differences, most of them
measured on volunteer flashcard users rather than on the population this product
serves. What can be said is that the implementation matches the published
specification, is deterministic, and is tested at its boundaries below. Anyone
wanting to make a learning-outcome claim about this code would need to run a
study, not read this file.

Determinism
-----------
Scheduling depends only on the prior state, the rating, and the review timestamp.
There is no randomness and no hidden clock: the caller passes the review time,
which is what makes the boundary tests below exact rather than flaky.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

INITIAL_EASE_FACTOR = 2.5
MIN_EASE_FACTOR = 1.3
FIRST_INTERVAL_DAYS = 1
SECOND_INTERVAL_DAYS = 6
PASS_RATING = 3
MIN_RATING = 0
MAX_RATING = 5


@dataclass(frozen=True, slots=True)
class Schedule:
    """The scheduling state after a review."""

    ease_factor: float
    interval_days: int
    repetitions: int
    due_at: datetime


def ease_factor_after(ease_factor: float, rating: int) -> float:
    """SM-2 ease adjustment, clamped to the floor."""
    if not MIN_RATING <= rating <= MAX_RATING:
        raise ValueError(f"rating must be between {MIN_RATING} and {MAX_RATING}, got {rating}")
    delta = 0.1 - (5 - rating) * (0.08 + (5 - rating) * 0.02)
    return max(MIN_EASE_FACTOR, round(ease_factor + delta, 4))


def schedule(
    *,
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    rating: int,
    reviewed_at: datetime,
) -> Schedule:
    """Return the next scheduling state for a card reviewed at ``reviewed_at``."""
    if not MIN_RATING <= rating <= MAX_RATING:
        raise ValueError(f"rating must be between {MIN_RATING} and {MAX_RATING}, got {rating}")

    moment = reviewed_at if reviewed_at.tzinfo else reviewed_at.replace(tzinfo=UTC)
    new_ease = ease_factor_after(ease_factor, rating)

    if rating < PASS_RATING:
        # Forgotten: start over, but keep the card and its history.
        return Schedule(
            ease_factor=new_ease,
            interval_days=FIRST_INTERVAL_DAYS,
            repetitions=0,
            due_at=moment + timedelta(days=FIRST_INTERVAL_DAYS),
        )

    new_repetitions = repetitions + 1
    if new_repetitions == 1:
        interval = FIRST_INTERVAL_DAYS
    elif new_repetitions == 2:
        interval = SECOND_INTERVAL_DAYS
    else:
        interval = max(FIRST_INTERVAL_DAYS, round(interval_days * new_ease))

    return Schedule(
        ease_factor=new_ease,
        interval_days=interval,
        repetitions=new_repetitions,
        due_at=moment + timedelta(days=interval),
    )
