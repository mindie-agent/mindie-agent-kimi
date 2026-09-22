"""Thin stdlib adapter to shared diagnostics. Import performs no I/O."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

COMPONENT = "mindie-agent-kimi"
HOST = "kimi"
REPOSITORY = "mindie-agent/mindie-agent"
_STORAGE = "MindIE diagnostic storage unavailable; original outcome unchanged."
_warned = False


def _hex(value, length):
    if isinstance(value, str) and len(value) == length:
        if all(char in "0123456789abcdef" for char in value):
            return value
    return None


_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,79}")


def _read_small(path, limit=2048):
    """Bytes of a nofollow regular file within the supplied byte limit.

    FileNotFoundError if the path is absent. None if present but unusable
    (symlink, non-regular, oversize, or unreadable). No writes.
    """
    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            return None
        return raw
    except FileNotFoundError:
        raise
    except OSError:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _own_generation():
    """Generation that owns this module, or None.

    scripts/diagnostic_support.py -> parent of scripts.
    update/launch/<id>/diagnostic_support.py -> update/generations/<same id>.
    """
    directory = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(directory)
    if os.path.basename(directory) == "scripts":
        return parent
    if os.path.basename(parent) != "launch":
        return None
    update = os.path.dirname(parent)
    if os.path.basename(update) != "update":
        return None
    return os.path.join(update, "generations", os.path.basename(directory))


def _version_of(raw):
    try:
        data = json.loads(raw.decode())
    except (UnicodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    if isinstance(version, str) and _VERSION_RE.fullmatch(version):
        return version
    return None


def _generation_metadata():
    """Installed host-package version and completed-generation revision.

    Used only when diagnostic-build.json is absent. No current.json, git, or writes.
    """
    generation = _own_generation()
    if not generation:
        return {}
    result = {}
    plugin = os.path.join(generation, "host-package", "kimi.plugin.json")
    try:
        raw = _read_small(plugin, 16384)
    except FileNotFoundError:
        raw = None
    if raw:
        version = _version_of(raw)
        if version:
            result["version"] = version
    base = os.path.basename(generation)
    if _hex(base, 40):
        marker = os.path.join(generation, ".mindie-generation-complete")
        try:
            raw = _read_small(marker)
        except FileNotFoundError:
            raw = None
        if raw is not None:
            try:
                text = raw.decode()
            except UnicodeError:
                text = None
            # Updater writes the sha plus a single trailing newline.
            if text in (base, base + "\n"):
                result["revision"] = base
    return result


def build_metadata():
    """Read this package's diagnostic-build.json. No subprocess or network.

    An explicit adjacent file stays authoritative. Only a genuinely missing
    file falls back to this generation's host package and completion marker.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diagnostic-build.json")
    try:
        raw = _read_small(path)
    except FileNotFoundError:
        return _generation_metadata()
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode())
    except (UnicodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    revision = _hex(data.get("revision"), 40)
    version = data.get("version")
    result = {"revision": revision} if revision else {}
    if isinstance(version, str) and _VERSION_RE.fullmatch(version):
        result["version"] = version
    return result


def _warn():
    global _warned
    if _warned:
        return
    _warned = True
    try:
        descriptor = sys.stderr.fileno()
        if not os.get_blocking(descriptor):
            os.write(descriptor, (_STORAGE + "\n").encode("ascii"))
    except Exception:
        pass


def _record():
    try:
        from mindie_diagnostics.integration import record_failure
    except ImportError:
        from diagnostic_fallback import record_failure
    return record_failure


def failure(operation, stage, category, *, exception=None, revision=None,
            elapsed_ms=None, exit_code=None, reportable=True, incident_id=None):
    """Only static product metadata; a trusted inner incident is not recorded twice."""
    trusted = _hex(incident_id, 32)
    if trusted:
        return {"incident_id": trusted, "logging_failed": False}
    inner = reference({"diagnostic": getattr(exception, "mindie_diagnostic", None)})
    if inner is not None:
        return inner
    build = build_metadata()
    explicit = _hex(revision, 40)
    try:
        raw = _record()(
            COMPONENT, operation, stage=stage, category=category,
            exception=exception, host=HOST,
            revision=explicit or build.get("revision"),
            version=build.get("version"),
            elapsed_ms=elapsed_ms, exit_code=exit_code, reportable=reportable,
        )
        if not isinstance(raw, dict):
            raise TypeError("invalid diagnostic result")
        if type(raw.get("recorded")) is not bool or type(raw.get("logging_failed")) is not bool:
            raise TypeError("invalid diagnostic result")
        result = {"recorded": raw["recorded"], "logging_failed": raw["logging_failed"]}
        incident = _hex(raw.get("incident_id"), 32)
        if incident:
            result["incident_id"] = incident
        return result
    except Exception:
        _warn()
        return {"logging_failed": True}


def _ref(diagnostic):
    if not isinstance(diagnostic, dict):
        return None
    failed = diagnostic.get("logging_failed", False)
    if type(failed) is not bool:
        return None
    incident = _hex(diagnostic.get("incident_id"), 32)
    if incident:
        return {"incident_id": incident, "logging_failed": failed}
    if failed is True:
        return {"logging_failed": True}
    return None


def reference(result):
    """Inspect only structured references returned by a trusted inner boundary."""
    if not isinstance(result, dict):
        return None
    found = _ref(result.get("diagnostic"))
    if found is not None:
        return found
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return _ref(structured.get("diagnostic"))
    return None


def attach(result, diagnostic):
    """Preserve result semantics; expose only the projected incident reference."""
    if not isinstance(result, dict) or reference(result) is not None:
        return result
    projected = _ref(diagnostic)
    if projected is None:
        return result
    result = dict(result, diagnostic=projected)
    incident = projected.get("incident_id")
    text = (f"MindIE incident {incident}; read-only local status: /mindie-agent:reporting-status. Upload remains a separate opt-in."
            if incident else _STORAGE)
    content = result.get("content")
    if isinstance(content, list):
        result["content"] = [*content, {"type": "text", "text": text}]
    elif "content" not in result:
        result["content"] = [{"type": "text", "text": text}]
    return result


def reporting_config_path():
    explicit = os.environ.get("MINDIE_DIAGNOSTICS_CONFIG")
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return (base / "mindie-agent" / "diagnostics.json").expanduser().resolve()


def _unavailable(stage, error_type):
    return {
        "enabled": None, "status": "unavailable",
        "error": {"stage": stage, "type": error_type},
        "hint": "Inspect the selected MindIE runtime; no repair or retry was started.",
    }


def reporting_status():
    try:
        from mindie_diagnostics.integration import reporting_status as upstream
        result = upstream(config=reporting_config_path())
        if not isinstance(result, dict):
            raise TypeError("invalid reporting status")
        return result
    except Exception as exc:
        return _unavailable("reporting_status", type(exc).__name__)


def ensure_argv(python):
    if not isinstance(python, str) or not Path(python).is_absolute():
        raise ValueError("selected runtime requires an absolute interpreter")
    return [python, "-m", "mindie_diagnostics.cli", "reporting", "ensure",
            "--config", str(reporting_config_path())]


def configure_reporting(enabled, python):
    if type(enabled) is not bool:
        return _unavailable("reporting_configure", "TypeError")
    try:
        command = ensure_argv(python)
        from mindie_diagnostics.integration import configure_reporting as upstream
        result = upstream(enabled, repository=REPOSITORY, config=reporting_config_path())
        if not isinstance(result, dict) or result.get("enabled") is not enabled:
            return _unavailable("reporting_configure", "ConfigurationUnconfirmed")
        result = dict(result, run_outside_hook=False)
        if enabled:
            result.update(
                run_outside_hook=True, command=command,
                command_line=shlex.join(command),
                note=("Reporting settings are enabled. Execute this command once outside "
                      "the Hook and inspect its result before claiming reporter readiness. "
                      "Report any failure; do not automatically retry."),
            )
        return result
    except Exception as exc:
        return _unavailable("reporting_configure", type(exc).__name__)


def reporting_hint():
    return {
        "optional": True, "recommended": True,
        "independent_of_knowledge_contribution": True,
        "enable": "/mindie-agent:reporting-enable",
        "status": "/mindie-agent:reporting-status",
        "disable": "/mindie-agent:reporting-disable",
        "repository": REPOSITORY,
        "note": ("Optionally report sanitized product fault code metadata to the public "
                 "repository. Prompts, transcripts, commands, environment and credentials "
                 "are excluded. This is a separate shared user choice; consult its status."),
    }
