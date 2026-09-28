"""Plain fixtures shared by the Kimi contract tests.

Host-shaped wire records only. Not a runner, not a second assertion library.
"""

from __future__ import annotations


def user_record(text, when_ms):
    return dict(
        type="context.append_message",
        time=when_ms,
        message=dict(
            role="user",
            content=[dict(type="text", text=text)],
            origin=dict(kind="user"),
        ),
    )


def skill_origin(activation, *, trigger="user-slash", in_turn=False, args=""):
    from paths import PLUGIN_ROOT

    origin = dict(
        kind="skill_activation",
        activationId=activation,
        skillName="mindie-agent",
        trigger=trigger,
        skillType="prompt",
        skillPath=str(PLUGIN_ROOT / "skills" / "mindie-agent" / "SKILL.md"),
        skillSource="plugin",
        skillArgs=args,
    )
    if in_turn:
        origin["inTurn"] = True
    return origin


def pad_records(start_ms, chunks):
    pad = "x" * 4000
    rows = []
    for index in range(chunks):
        rows.append(
            dict(
                type="context.append_loop_event",
                time=start_ms + index,
                event=dict(
                    type="tool.result",
                    toolCallId=f"pad{index}",
                    result=dict(output=pad),
                ),
            )
        )
    return rows
