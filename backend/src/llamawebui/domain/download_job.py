"""Download job state rules."""

from enum import StrEnum


class DownloadState(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TRANSITIONS = {
    DownloadState.QUEUED: frozenset({DownloadState.DOWNLOADING, DownloadState.CANCELLED}),
    DownloadState.DOWNLOADING: frozenset(
        {
            DownloadState.PAUSED,
            DownloadState.COMPLETED,
            DownloadState.FAILED,
            DownloadState.CANCELLED,
        }
    ),
    DownloadState.PAUSED: frozenset({DownloadState.QUEUED, DownloadState.CANCELLED}),
    DownloadState.FAILED: frozenset({DownloadState.QUEUED, DownloadState.CANCELLED}),
    DownloadState.COMPLETED: frozenset(),
    DownloadState.CANCELLED: frozenset(),
}


def require_transition(current: DownloadState, target: DownloadState) -> None:
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"invalid download state transition: {current} -> {target}")
