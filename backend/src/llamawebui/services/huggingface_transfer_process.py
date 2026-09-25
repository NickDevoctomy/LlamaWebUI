"""Run one Hugging Face file transfer in an interruptible child process."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

from huggingface_hub import hf_hub_download


class TransferRequest(TypedDict):
    repo_id: str
    filename: str
    revision: str
    destination: str
    token: str | None


class TransferResponse(TypedDict, total=False):
    path: str
    error: str


def execute_transfer(request: TransferRequest) -> TransferResponse:
    """Execute a pinned transfer and return a JSON-safe result."""
    token = request["token"]
    try:
        downloaded = hf_hub_download(
            repo_id=request["repo_id"],
            filename=request["filename"],
            revision=request["revision"],
            local_dir=Path(request["destination"]),
            token=token,
        )
    except Exception as error:
        message = str(error)
        if token:
            message = message.replace(token, "[redacted]")
        return {"error": message or type(error).__name__}
    return {"path": str(downloaded)}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        response = execute_transfer(request)
    except Exception as error:
        response = {"error": str(error) or type(error).__name__}
    sys.stdout.write(json.dumps(response))
    return 0 if "path" in response else 1


if __name__ == "__main__":
    raise SystemExit(main())
