from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Phase:
    name: str
    fraction: float


PHASES: tuple[Phase, ...] = (
    Phase("healthy", 0.06),
    Phase("degrading", 0.16),
    Phase("warning", 0.14),
    Phase("critical", 0.18),
    Phase("incident_detected", 0.08),
    Phase("investigating", 0.10),
    Phase("recovering", 0.18),
    Phase("healthy_again", 0.10),
)

TIMELINE_MARKERS = ("healthy", "warning", "critical", "detected", "incident", "recovery")


def phase_at(progress: float) -> tuple[str, float]:
    """Return phase name and intensity in the range 0..1.

    Intensity rises through the failure and falls through recovery.
    """
    cursor = 0.0
    bounded = max(0.0, min(0.999999, progress))
    mapping = {
        "healthy": 0.0,
        "degrading": 0.45,
        "warning": 0.72,
        "critical": 1.0,
        "incident_detected": 1.0,
        "investigating": 0.95,
        "recovering": None,
        "healthy_again": 0.0,
    }
    for phase in PHASES:
        end = cursor + phase.fraction
        if bounded <= end or phase is PHASES[-1]:
            local = 0 if phase.fraction == 0 else (bounded - cursor) / phase.fraction
            intensity = mapping[phase.name]
            if intensity is None:
                intensity = max(0.0, 1.0 - local)
            return phase.name, float(intensity)
        cursor = end
    return "healthy_again", 0.0


def progress_for(elapsed_seconds: float, duration_seconds: float, speed: float) -> float:
    virtual_elapsed = max(0.0, elapsed_seconds) * max(speed, 0.1)
    if duration_seconds <= 0:
        return 1.0
    return max(0.0, min(1.0, virtual_elapsed / duration_seconds))


def is_terminal(progress: float) -> bool:
    return progress >= 1.0
