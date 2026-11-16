"""Exact unequal splits, reproducible from the seed in the reviewed request."""
import hashlib
import random


def allocate(total: int, weights: list[int]) -> list[int]:
    if total < 0 or not weights or any(type(w) is not int or w <= 0 for w in weights):
        raise ValueError('Positive integer weights and a nonnegative total are required')
    denominator = sum(weights)
    parts = [divmod(total * weight, denominator) for weight in weights]
    result = [part[0] for part in parts]
    for i in sorted(range(len(parts)), key=lambda i: (-parts[i][1], i))[:total - sum(result)]:
        result[i] += 1
    return result


def random_weights(seed: str, source: str, recipients: list[str]) -> list[int]:
    material = '\x00'.join([seed, source, *recipients]).encode()
    rng = random.Random(hashlib.sha256(material).digest())
    return [rng.randint(1, 10_000) for _ in recipients]
