"""Unit tests for RetryPolicy backoff and attempt evaluation.

Notes/Architectural Intent:
    Tests constant, linear, and exponential backoff calculations and retry decision logic.
"""

from hexaflow.domain.retry import BackoffType, RetryPolicy


def test_should_retry_within_max_attempts() -> None:
    """Validate retry occurs within max attempts for matching exceptions."""
    policy = RetryPolicy(max_attempts=3, retry_on=(ValueError,))

    retry_1 = policy.should_retry(attempt=1, exception=ValueError("Invalid data"))
    assert retry_1 is True

    retry_2 = policy.should_retry(attempt=2, exception=ValueError("Invalid data"))
    assert retry_2 is True

    retry_3 = policy.should_retry(attempt=3, exception=ValueError("Invalid data"))
    assert retry_3 is False


def test_should_not_retry_unmatched_exception() -> None:
    """Validate retry rejects exceptions not in retry_on tuple."""
    policy = RetryPolicy(max_attempts=5, retry_on=(TimeoutError,))

    should_retry = policy.should_retry(attempt=1, exception=KeyError("Missing key"))
    assert should_retry is False


def test_constant_backoff_delay() -> None:
    """Validate constant backoff produces identical bounded delay without jitter."""
    policy = RetryPolicy(
        max_attempts=3,
        backoff_type=BackoffType.CONSTANT,
        initial_delay_seconds=2.0,
        jitter=False,
    )
    delay_1 = policy.calculate_delay(attempt=1)
    assert delay_1 == 2.0

    delay_2 = policy.calculate_delay(attempt=2)
    assert delay_2 == 2.0


def test_linear_backoff_delay() -> None:
    """Validate linear backoff scales linearly without jitter."""
    policy = RetryPolicy(
        max_attempts=4,
        backoff_type=BackoffType.LINEAR,
        initial_delay_seconds=1.5,
        jitter=False,
    )
    delay_1 = policy.calculate_delay(attempt=1)
    assert delay_1 == 1.5

    delay_2 = policy.calculate_delay(attempt=2)
    assert delay_2 == 3.0

    delay_3 = policy.calculate_delay(attempt=3)
    assert delay_3 == 4.5


def test_exponential_backoff_delay_bounded_by_max() -> None:
    """Validate exponential backoff scales exponentially and caps at max_delay_seconds."""
    policy = RetryPolicy(
        max_attempts=5,
        backoff_type=BackoffType.EXPONENTIAL,
        initial_delay_seconds=1.0,
        backoff_factor=2.0,
        max_delay_seconds=5.0,
        jitter=False,
    )
    delay_1 = policy.calculate_delay(attempt=1)
    assert delay_1 == 1.0

    delay_2 = policy.calculate_delay(attempt=2)
    assert delay_2 == 2.0

    delay_3 = policy.calculate_delay(attempt=3)
    assert delay_3 == 4.0

    delay_4 = policy.calculate_delay(attempt=4)
    assert delay_4 == 5.0  # Capped at max_delay_seconds


def test_jittered_delay_stays_within_bounds() -> None:
    """Validate jitter yields a float in [0.0, calculated_delay]."""
    policy = RetryPolicy(
        max_attempts=3,
        backoff_type=BackoffType.CONSTANT,
        initial_delay_seconds=4.0,
        jitter=True,
    )
    for _ in range(10):
        delay = policy.calculate_delay(attempt=1)
        in_bounds = 0.0 <= delay <= 4.0
        assert in_bounds is True
