"""[Role] Stdlib first-use choice reader and slash activationId deduplication store.
[Invariants] Reads persistent choice from profile-shared consent store; caps consumed activationId history at 256 entries.
[Timeouts & Locks] Uses consent._shared_file_lock for atomic cross-process updates to slash-activations.json.
[Side-Effect Policy] consume_activation_id() atomically updates state_dir()/slash-activations.json; all other helpers are read-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import consent
from paths import first_use_path, state_dir


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def first_use():
    saved = consent.load()
    return saved["choice"] if saved["state"] == "ok" and saved["choice"] in consent.CHOICES else None


def consume_activation_id(activation_id: str) -> bool:
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
        data["consumed"] = [*seen, activation_id][-256:]
        consent._store(path, data)
    return True


def configuration_required() -> dict:
    return {
        "configured": False,
        "experience": "needs-configuration",
        "sharing": {"configured": False, "enabled": False},
        "first_use": first_use(),
        "choices": [],
        "required": ["runtime", "public_repository", "account", "project_scope"],
        "setup": "python3 scripts/setup.py --knowledge-python <venv-python>",
        "note": (
            "Supply only missing destination and scope information. Reuse "
            "previously approved values. Installation or task binding alone "
            "does not make the experience loop available. Explicitly disabled "
            "and legacy declined settings remain disabled until changed."
        ),
    }
