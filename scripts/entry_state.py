"""Stdlib first-use and slash activationId consumption. No knowledge import.

The persistent choice lives in the profile-shared consent document
(``consent``); the legacy marker below only deduplicates native slash
activationIds and is a one-time migration source, never a consent source.
The dedupe file is written under the same canonical cross-process lock and
atomic replace the shared store provides (no third update protocol).
"""

from __future__ import annotations

import json
from pathlib import Path

import consent
from paths import first_use_path, state_dir


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def first_use():
    saved = consent.load()
    if saved["state"] == "ok" and saved["choice"] in consent.CHOICES:
        return saved["choice"]
    return None


def consume_activation_id(activation_id: str) -> bool:
    """True once per native activationId (unconfigured path)."""
    if not isinstance(activation_id, str) or not activation_id or len(activation_id) > 256:
        raise ValueError("invalid activationId")
    path = state_dir() / "slash-activations.json"
    with consent._shared_file_lock(path):
        data = _load(path)
        seen = data.get("consumed")
        if not isinstance(seen, list):
            seen = []
        if activation_id in seen:
            return False
        seen.append(activation_id)
        data["consumed"] = seen[-256:]
        consent._store(path, data)
    return True


def configuration_required() -> dict:
    """Incomplete configuration is a state, never a product tier."""
    return dict(
        configured=False,
        experience="needs-configuration",
        sharing=dict(configured=False, enabled=False),
        first_use=first_use(),
        choices=[],
        required=["runtime", "public_repository", "account", "project_scope"],
        setup="python3 scripts/setup.py --knowledge-python <venv-python>",
        note=("Supply only missing destination and scope information. Reuse "
              "previously approved values. Installation or task binding alone "
              "does not make the experience loop available. Explicitly disabled "
              "and legacy declined settings remain disabled until changed."),
    )
