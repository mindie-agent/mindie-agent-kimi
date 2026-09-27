"""Stdlib first-use and slash activationId consumption. No knowledge import.

The persistent choice lives in the profile-shared consent document
(``consent``); the legacy marker below only deduplicates native slash
activationIds and is a one-time migration source, never a consent source.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import consent
from paths import first_use_path, state_dir

CHOICES = ("contribute", "read-only", "later")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def first_use():
    saved = consent.load()
    if saved["state"] == "ok" and saved["choice"] in consent.CHOICES:
        return saved["choice"]
    return None


def set_first_use(choice: str) -> str:
    if choice not in CHOICES:
        raise ValueError("choice must be contribute, read-only, or later")
    return consent.record_choice(choice)


def consume_activation_id(activation_id: str) -> bool:
    """True once per native activationId (unconfigured path)."""
    if not isinstance(activation_id, str) or not activation_id or len(activation_id) > 256:
        raise ValueError("invalid activationId")
    path = state_dir() / "slash-activations.json"
    data = _load(path)
    seen = data.get("consumed")
    if not isinstance(seen, list):
        seen = []
    if activation_id in seen:
        return False
    seen.append(activation_id)
    data["consumed"] = seen[-256:]
    _store(path, data)
    return True


def three_choices() -> dict:
    return dict(
        configured=False,
        sharing=dict(configured=False, enabled=False),
        first_use=first_use(),
        choices=[
            dict(
                id="contribute",
                recommended=True,
                summary="Contribute public experience for the current project",
                next=(
                    "Say so in this conversation with the public repository and "
                    "account (e.g. 'contribute to owner/repo as name'), or run "
                    "/mindie-agent contribute owner/repo name"
                ),
            ),
            dict(
                id="read-only",
                summary="Read-only knowledge; no contribution",
                next="Reply read-only, or run /mindie-agent read-only",
            ),
            dict(
                id="later",
                summary="Configure later (sharing stays off)",
                next="Reply later, or run /mindie-agent later",
            ),
        ],
        setup="python3 scripts/setup.py --knowledge-python <venv-python>",
        note=(
            "One-time setup: the choice persists for this installation and is "
            "never asked again, including after restarts, upgrades or failures. "
            "Enabling contribution requires the explicit public repository and "
            "account from the user; the current project is the contribution "
            "scope. There is no automatic yes."
        ),
    )
