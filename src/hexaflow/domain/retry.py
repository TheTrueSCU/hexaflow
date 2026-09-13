"""Transient retry policies, backoff algorithms, and jitter calculation.

Notes/Architectural Intent:
    Provides deterministic and jittered retry policies to separate transient failures
    (network timeouts, rate limits) from permanent code/data errors. Contains pure
    algorithmic logic with zero external dependencies.
"""

import random
from enum import StrEnum
from typing import NamedTuple


class BackoffType(StrEnum):
    """Backoff strategies for retry policies.

    Notes/Architectural Intent:
        Defines how delay intervals scale across successive retry attempts.
    """

    CONSTANT = "CONSTANT"
    EXPONENTIAL = "EXPONENTIAL"
    LINEAR = "LINEAR"


class RetryPolicy(NamedTuple):
    """Immutable specification governing transient retry behavior for a step.

    Notes/Architectural Intent:
        Pairs attempt bounds with backoff algorithms and exception filters.
        Enables individual steps to isolate transient self-healing from workflow-level
        suspension.
    """

    max_attempts: int = 1
    backoff_type: BackoffType = BackoffType.EXPONENTIAL
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 60.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def should_retry(self, attempt: int, exception: BaseException) -> bool:
        """Determine whether another execution attempt should be scheduled.

        Args:
            attempt: The 1-indexed attempt number that just completed.
            exception: The exception raised by the step action.

        Returns:
            True if the attempt number is less than max_attempts and the exception matches retry_on.

        Notes/Architectural Intent:
            Evaluates both attempt exhaustion and exception type matching.
        """
        if attempt >= self.max_attempts:
            return False
        return isinstance(exception, self.retry_on)

    def calculate_delay(self, attempt: int) -> float:
        """Compute the delay in seconds before the next retry attempt.

        Args:
            attempt: The 1-indexed attempt number that just failed.

        Returns:
            Calculated sleep duration in seconds, bounded by max_delay_seconds.

        Notes/Architectural Intent:
            Implements constant, linear, or exponential backoff with full jitter to avoid
            thundering herd problem against downstream dependencies.
        """
        match self.backoff_type:
            case BackoffType.CONSTANT:
                delay = self.initial_delay_seconds
            case BackoffType.LINEAR:
                delay = self.initial_delay_seconds * attempt
            case BackoffType.EXPONENTIAL:
                delay = self.initial_delay_seconds * (self.backoff_factor ** (attempt - 1))

        delay = min(delay, self.max_delay_seconds)
        if self.jitter:
            delay = random.uniform(0.0, delay)
        return max(0.0, delay)


__all__ = [
    "BackoffType",
    "RetryPolicy",
]
