"""[Role] Measure production wire.jsonl parsing on public synthetic input.
[Invariants] Keep user/assistant text; exclude tool and reasoning material.
[Timeouts & Locks] Fixed 300 reads of one local transcript; no network or models.
[Side-Effect Policy] Owns a temporary fixture and writes the benchmark result.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transcript


def main():
    with tempfile.TemporaryDirectory(prefix="mindie-kimi-benchmark-") as directory:
        session = Path(directory) / "sessions" / "workdir" / "bench"
        wire = session / "agents" / "main" / "wire.jsonl"
        wire.parent.mkdir(parents=True)
        (session / "state.json").write_text('{"createdAt": 1000}\n', encoding="utf-8")
        records = []
        for index in range(50):
            for role in ("user", "assistant"):
                records.append({
                    "type": "context.append_message", "time": 2000000000000 + index,
                    "message": {
                        "role": role, "origin": {"kind": "user"},
                        "content": [
                            {"type": "text", "text": f"Public Ascend 910C observation {index}."},
                            {"type": "think", "think": "excluded-reasoning"},
                        ],
                    },
                })
        wire.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        for _ in range(20):
            observed = transcript.read_material(wire, 0, session_id="bench")
        assert observed["records"] == 100
        assert "Public Ascend 910C observation 49." in observed["text"]
        assert "excluded-reasoning" not in observed["text"]
        iterations = 300
        started = time.perf_counter_ns()
        for _ in range(iterations):
            observed = transcript.read_material(wire, 0, session_id="bench")
        elapsed = time.perf_counter_ns() - started
        assert observed["records"] == 100
        result = {
            "status": "pass",
            "metrics": [{
                "name": "public_transcript_parse", "unit": "us/op",
                "value": elapsed / iterations / 1000, "iterations": iterations,
            }],
        }
    Path(os.environ["MINDIE_BENCH_RESULT_PATH"]).write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
