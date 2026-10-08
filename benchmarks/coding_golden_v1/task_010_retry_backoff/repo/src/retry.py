def backoff_delay(attempt, base=1, cap=30):
    return min(cap, base * 2 ** attempt)

