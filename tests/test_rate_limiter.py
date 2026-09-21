from bcs.rate_limiter import wait_for_rate_limit


def test_rate_limiter_runs():
    wait_for_rate_limit()
