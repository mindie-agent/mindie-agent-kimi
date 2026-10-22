"""Standard C6 native worker for the MindIE summary protocol.

[Role]
Consumes one explicit `mindie-distill-request/1` already bound by the shared
`graded.worker.run` through the original organizer `--identity` capability and
`with_worker_identity`. It runs the existing isolated native worker once and
returns one standard `mindie-summary-outcome/1` envelope whose `result` is the
shared-validated `mindie-distill-decision/1`.

[Invariants]
- The shared runtime owns request preparation, identity binding, prompt,
  decision schema, validation, SummaryLedger and local admission; this worker
  never scans a ledger or filesystem and never duplicates curation.
- A request without an explicit milestone or completion returns a standard
  `returned` outcome with a noop decision and `model_calls=0`; an ordinary
  Stop is never treated as task completion.
- A model call is accounted once only after the native process starts; an
  interrupted or non-terminal invocation is `outcome_unknown`, never retried.
- The worker never publishes, installs, or rewrites a remote result.

[Timeouts & Locks]
- Reuses the organizer's 300s bounded native timeout with the enclosing
  worker's remaining budget and 5s cleanup reserve; C6 result cap is 128 KiB,
  matching the shared decision validator.

[Side-Effect Policy]
- Reuses the organizer's disposable isolated home and native subprocess only.
  It writes no knowledge state and calls no GitHub or network API.
"""

from __future__ import annotations

import time

import organizer
from bounded import CommandTimedOut, OutputLimitExceeded
from mindie_knowledge.materials import summarizer

REQUEST_SCHEMA = organizer.DISTILL_REQUEST_SCHEMA
INSTRUCTION = "Distill the selected sanitized evidence into the requested JSON decision."
MAX_RESULT = 128 * 1024
AGENT = """---
name: mindie-distill
description: Authorized C6 distillation from sanitized evidence
tools: []
subagents: []
---

Follow the user prompt exactly. Return only the requested JSON decision.
"""


class UnsupportedDistillError(ImportError):
    """The paired shared runtime does not expose the C6 organizer API."""


def _shared():
    """Import the paired runtime lazily so a legacy 0.0.2 pin still starts."""
    try:
        from mindie_knowledge.graded import distill
    except ImportError as exc:
        raise UnsupportedDistillError("shared C6 distillation API is unavailable") from exc
    required = (
        "REQUEST_SCHEMA", "build_prompt", "decision_schema", "parse_validate_result",
        "value_gate", "noop_decision", "validate_decision", "validate_request",
    )
    missing = [name for name in required if not hasattr(distill, name)]
    if missing:
        raise UnsupportedDistillError(
            "shared C6 distillation API is incomplete: " + ", ".join(missing)
        )
    return distill


def _receipt():
    return dict(
        native_started=False, turn_completed=False, turn_failed=False,
        usage=None, cleanup_failed=False,
    )


def _validate_identity(shared, request):
    """Require the exact original organizer identity and full C6 request."""
    if request.get("policy_identity") != organizer.identity():
        raise organizer.ConfigurationError("distillation worker policy changed")
    shared.validate_request(request)


def _prompt(shared, request):
    # The shared prompt already carries both the decision schema and request.
    return shared.build_prompt(request)


def _noop(shared, request, reason):
    return shared.validate_decision(shared.noop_decision(request, reason), request)


def _classify(exc, receipt):
    if isinstance(exc, UnsupportedDistillError):
        return "configuration", "dependency_unavailable"
    if isinstance(exc, organizer.NativeStartError):
        return "configuration", "native_start_failed"
    if type(exc) is organizer.ConfigurationError:
        return "configuration", "worker_policy_changed"
    if isinstance(exc, organizer.ConfigurationError):
        return "configuration", "configuration_invalid"
    if isinstance(exc, ImportError):
        return "configuration", "dependency_unavailable"
    if isinstance(exc, (CommandTimedOut, TimeoutError)):
        return "deadline", "native_deadline"
    if isinstance(exc, OutputLimitExceeded):
        return "output_limit", "native_output_limit"
    if isinstance(exc, organizer.InvalidResultError):
        reason = "native_output_unreadable" if receipt.get("turn_completed") else "result_contract_invalid"
        return "invalid_result", reason
    if isinstance(exc, organizer.NativeFailure):
        reason = getattr(exc, "reason", "native_failure")
        if isinstance(reason, str) and reason.startswith("native_") and reason in summarizer.SUMMARY_FAILURE_REASONS:
            return "native", reason
        return "native", "native_failure"
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        reason = "result_contract_invalid" if receipt.get("native_started") else "request_invalid"
        return "invalid_result", reason
    return "unknown", "unknown_failure"


def _status(receipt):
    if receipt.get("native_started") and not receipt.get("turn_completed") and not receipt.get("turn_failed"):
        return "outcome_unknown"
    return "failed"


def run(request):
    """Run one bound C6 request and emit the standard summary outcome."""
    started = time.monotonic()
    receipt = _receipt()
    result, status, error, error_reason = None, "failed", None, None
    try:
        shared = _shared()
        if not isinstance(request, dict) or request.get("schema_version") != shared.REQUEST_SCHEMA:
            raise organizer.UnsupportedSchema("unsupported organizer request schema")
        _validate_identity(shared, request)
        reason = shared.value_gate(request)
        if reason is not None:
            result = _noop(shared, request, reason)
            status = "returned"
        else:
            organizer._invoke_native(
                _prompt(shared, request), receipt,
                instruction=INSTRUCTION, agent_text=AGENT, max_output=MAX_RESULT,
            )
            if not receipt.get("turn_completed"):
                raise organizer.NativeFailure("native execution did not report completion")
            raw = receipt.get("final_message")
            if not isinstance(raw, str) or not raw:
                raise organizer.InvalidResultError("completed distillation message is unavailable")
            result = shared.parse_validate_result(raw, request)
            status = "returned"
    except organizer.UnsupportedSchema:
        raise
    except (organizer.NativeFailure, organizer.NativeStartError, ImportError,
            ValueError, KeyError, TypeError, AssertionError) as exc:
        error, error_reason = _classify(exc, receipt)
        status = _status(receipt)
    usage = receipt.get("usage")
    usage_known = bool(
        isinstance(usage, dict)
        and all(type(usage.get(key)) is int and usage[key] >= 0 for key in summarizer.COUNTERS)
    )
    model_calls = int(bool(receipt.get("native_started")))
    return summarizer.outcome(
        request, status=status, result=result, raw_result=None,
        usage=summarizer.clean_usage(usage) if usage_known else None,
        usage_known=usage_known, model_calls=model_calls,
        elapsed_ms=round((time.monotonic() - started) * 1000),
        error=error, error_reason=error_reason,
        cleanup_failed=bool(receipt.get("cleanup_failed", False)),
    )
