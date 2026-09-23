"""UUIDv7 identifiers (RFC 9562).

Time-ordered, so B-tree indexes stay compact and inserts stay append-friendly, while
still being unguessable (74 random bits). Python gains `uuid.uuid7` only in 3.14.
"""

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0  # 12-bit sequence in rand_a keeps IDs monotonic within one millisecond


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            _counter = int.from_bytes(os.urandom(2)) & 0x3FF  # leave headroom before overflow
        else:
            _counter += 1
            if _counter > 0xFFF:  # sequence exhausted: borrow the next millisecond
                _last_ms += 1
                _counter = 0
            now_ms = _last_ms

    rand_b = int.from_bytes(os.urandom(8)) & ((1 << 62) - 1)
    value = (now_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= _counter << 64
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    return value.int >> 80
