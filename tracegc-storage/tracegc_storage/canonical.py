import hashlib
import json

def canonical_payload(events: list[dict]) -> bytes:
    return json.dumps(
        events, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")

def payload_hash(events: list[dict]) -> str:
    return hashlib.sha256(canonical_payload(events)).hexdigest()
