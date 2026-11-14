import re

_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token|authorization)\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
]


def redact(value: str) -> str:
    redacted = value
    for pattern in _PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_mapping(payload: dict) -> dict:
    cleaned = {}
    for key, item in payload.items():
        if isinstance(item, str):
            if key.lower() in {"password", "secret", "token", "authorization", "api_key"}:
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact(item)
        elif isinstance(item, dict):
            cleaned[key] = redact_mapping(item)
        else:
            cleaned[key] = item
    return cleaned
