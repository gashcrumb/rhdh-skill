"""Behaviour tests for the overlay PR pipeline classifier."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "skills" / "plugins" / "rhdh-overlay" / "scripts" / "pr-pipeline.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("pr_pipeline", SCRIPT)
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)

HEAD = "2026-10-20T10:00:00Z"
BEFORE = "2026-10-19T10:00:00Z"
AFTER = "2026-10-20T11:00:00Z"


def comment(login, body, at=AFTER, association="MEMBER"):
    return {
        "author": {"login": login},
        "authorAssociation": association,
        "body": body,
        "createdAt": at,
    }


def review(login, state, at=AFTER):
    return {"author": {"login": login}, "state": state, "submittedAt": at}


def publish_report(ok=True, at=AFTER, starts_e2e=False):
    outcome = "completed with success" if ok else "completed with failure"
    trigger = "\nRunning e2e tests\n/test e2e-ocp-helm" if starts_e2e else ""
    return comment(
        "github-actions",
        f"[Publish workflow](https://github.com/o/r/actions/runs/1) has {outcome}.{trigger}",
        at=at,
        association="CONTRIBUTOR",
    )


def smoke_report(ok=True, at=AFTER):
    verdict = "passed" if ok else "failed"
    return comment(
        "github-actions",
        f":white_check_mark: [Smoke tests workflow](https://github.com/o/r/actions/runs/2) {verdict}.",
        at=at,
        association="CONTRIBUTOR",
    )


def e2e_report(passed=True, at=AFTER, counts=(7, 16, 0, 4)):
    heading = "✅ Passed E2E Tests (-w rbac)" if passed else "❌ Failed E2E Tests (-w rbac)"
    passed_count, failed, flaky, skipped = counts
    summary = f"Passed: {passed_count} | Failed: {failed} | Flaky: {flaky} | Skipped: {skipped}"
    return comment(
        "rhdh-test-bot",
        f"### {heading}\n{summary}",
        at=at,
        association="NONE",
    )


def make_pr(**overrides):
    pr = {
        "number": 4444,
        "title": "fix(e2e): something",
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc123",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "BLOCKED",
        "commits": [{"committedDate": HEAD}],
        "files": [{"path": "workspaces/rbac/e2e-tests/tests/specs/rbac.spec.ts"}],
        "comments": [],
        "reviews": [],
        "statusCheckRollup": [],
    }
    pr.update(overrides)
    return pr


def classify(pr, **kwargs):
    return pipeline.classify(pr, **kwargs)


def test_open_findings_need_a_fix_run():
    pr = make_pr(reviews=[review("fullsend-ai-review", "CHANGES_REQUESTED")])

    assert classify(pr)["stage"] == "code"
    assert classify(pr)["command"] == "/fs-fix"


def test_a_fix_run_after_the_findings_clears_the_code_stage():
    pr = make_pr(
        reviews=[review("fullsend-ai-review", "CHANGES_REQUESTED", at=BEFORE)],
        comments=[
            comment(
                "fullsend-ai-coder",
                "🤖 Finished Fix · ✅ Success\nCommit: `abc123`",
                at=AFTER,
                association="CONTRIBUTOR",
            )
        ],
    )

    assert classify(pr)["stage"] == "gate"


def test_a_failed_fix_run_is_reported_not_treated_as_a_fix():
    pr = make_pr(
        reviews=[review("fullsend-ai-review", "CHANGES_REQUESTED", at=BEFORE)],
        comments=[
            comment(
                "fullsend-ai-coder",
                "🤖 Finished Fix · ❌ Failure (provider create failed)",
                at=AFTER,
                association="CONTRIBUTOR",
            )
        ],
    )

    result = classify(pr)
    assert result["stage"] == "code"
    assert "the last fix run failed" in result["notes"]


def test_a_bot_cannot_satisfy_the_trusted_gate():
    pr = make_pr(
        reviews=[review("fullsend-ai-review", "APPROVED")],
        comments=[comment("fullsend-ai-coder", "/ok-to-test", association="CONTRIBUTOR")],
    )

    assert classify(pr)["stage"] == "gate"


def test_a_member_comment_satisfies_the_gate():
    pr = make_pr(comments=[comment("a-reviewer", "/ok-to-test")])

    assert classify(pr)["stage"] == "publish"


def test_a_missing_publish_for_the_head_asks_for_one():
    pr = make_pr(
        comments=[comment("a-reviewer", "/ok-to-test"), publish_report(ok=True, at=BEFORE)]
    )

    result = classify(pr)
    assert result["stage"] == "publish"
    assert result["command"] == "/publish"


def test_a_running_publish_check_is_not_actionable():
    pr = make_pr(
        comments=[comment("a-reviewer", "/ok-to-test")],
        statusCheckRollup=[{"name": "publish", "state": "PENDING"}],
    )

    assert classify(pr)["stage"] == "publishing"
    assert classify(pr)["command"] == "none"


def test_a_head_that_passed_every_gate_is_mergeable():
    pr = make_pr(
        comments=[
            comment("a-reviewer", "/ok-to-test", at=BEFORE),
            publish_report(ok=True, at=HEAD, starts_e2e=True),
            smoke_report(ok=True, at=AFTER),
            e2e_report(passed=True, at=AFTER),
        ],
        mergeStateStatus="CLEAN",
    )

    assert classify(pr)["stage"] == "mergeable"
    assert classify(pr)["command"] == "merge"


def test_a_passing_run_older_than_the_head_is_not_evidence():
    pr = make_pr(
        comments=[
            comment("a-reviewer", "/ok-to-test", at=BEFORE),
            publish_report(ok=True, at=BEFORE),
            smoke_report(ok=True, at=BEFORE),
            e2e_report(passed=True, at=BEFORE),
        ]
    )

    result = classify(pr)
    assert result["stage"] == "publish"
    assert result["command"] == "/publish"


def test_a_failing_head_reports_e2e_failed_even_when_the_publish_is_stale():
    pr = make_pr(
        comments=[
            comment("a-reviewer", "/ok-to-test", at=BEFORE),
            publish_report(ok=True, at=BEFORE),
            smoke_report(ok=True, at=BEFORE),
            e2e_report(passed=False, at=AFTER),
        ]
    )

    result = classify(pr)
    assert result["stage"] == "e2e-failed"
    assert "7 / 16 / 0 / 4" in " ".join(result["notes"])
    assert "the successful publish predates this head" in result["notes"]


def test_a_publish_that_started_the_tests_is_waited_on_not_retriggered():
    pr = make_pr(
        comments=[
            comment("a-reviewer", "/ok-to-test", at=BEFORE),
            publish_report(ok=True, at=HEAD, starts_e2e=True),
            smoke_report(ok=True, at=AFTER),
        ]
    )

    result = classify(pr)
    assert result["stage"] == "e2e"
    assert result["command"] == "none"


def test_a_head_with_no_started_run_gets_one_requested():
    pr = make_pr(
        comments=[
            comment("a-reviewer", "/ok-to-test", at=BEFORE),
            publish_report(ok=True, at=HEAD),
            smoke_report(ok=True, at=AFTER),
        ]
    )

    result = classify(pr)
    assert result["stage"] == "e2e"
    assert result["command"] == "/test e2e-ocp-helm"


def test_a_blocked_merge_is_not_a_reason_to_rerun_the_tests():
    pr = make_pr(
        comments=[
            comment("a-reviewer", "/ok-to-test", at=BEFORE),
            publish_report(ok=True, at=HEAD, starts_e2e=True),
            smoke_report(ok=True, at=AFTER),
            e2e_report(passed=True, at=AFTER),
        ]
    )

    result = classify(pr)
    assert result["stage"] == "blocked"
    assert result["command"] == "none"
    assert "merge is blocked by a required check or approval" in result["notes"]


def test_a_failed_publish_names_the_failure():
    pr = make_pr(
        comments=[comment("a-reviewer", "/ok-to-test"), publish_report(ok=False)],
    )

    result = classify(pr)
    assert result["stage"] == "publish-failed"
    assert result["command"] == "/fs-diagnose"


def test_a_held_pr_is_not_actionable_whatever_its_signals_say():
    pr = make_pr(reviews=[review("fullsend-ai-review", "CHANGES_REQUESTED")])

    result = classify(pr, hold_reason="waiting on a named reviewer")
    assert result["stage"] == "held"
    assert result["command"] == "none"
    assert "waiting on a named reviewer" in result["notes"]


def test_a_closed_pr_is_closed():
    assert classify(make_pr(state="MERGED"))["stage"] == "closed"


def test_review_findings_are_carried_into_the_plan():
    pr = make_pr(
        reviews=[review("fullsend-ai-review", "CHANGES_REQUESTED")],
        comments=[
            comment(
                "fullsend-ai-review",
                "## Review\n### Findings\n#### Low\n- **[naming conventions]** use `error`",
                association="NONE",
            )
        ],
    )

    assert classify(pr)["findings"] == ["naming conventions"]


def test_workspaces_come_from_the_changed_paths():
    pr = make_pr(
        files=[
            {"path": "workspaces/rbac/e2e-tests/a.ts"},
            {"path": "workspaces/rbac/metadata/b.yaml"},
            {"path": "README.md"},
        ]
    )

    assert pipeline.workspaces_of(pr) == ["rbac"]


def test_ledger_round_trip(tmp_path, capsys):
    ledger = tmp_path / "pipeline.json"

    assert pipeline.main(["--ledger", str(ledger), "add", "4440", "4441"]) == 0
    assert pipeline.main(["--ledger", str(ledger), "hold", "4441", "--reason", "parked"]) == 0
    assert pipeline.main(["--ledger", str(ledger), "list"]) == 0
    output = capsys.readouterr().out
    assert "4440" in output
    assert "parked" in output

    assert pipeline.main(["--ledger", str(ledger), "release", "4441"]) == 0
    assert pipeline.main(["--ledger", str(ledger), "remove", "4440"]) == 0
    saved = json.loads(ledger.read_text())
    assert list(saved["prs"]) == ["4441"]
    assert "hold" not in saved["prs"]["4441"]


def test_a_stage_change_is_recorded_once(tmp_path):
    ledger = pipeline.load_ledger(tmp_path / "pipeline.json", "o/r")
    entry = pipeline.entry_for(ledger, 4440)

    first = pipeline.record(entry, {"stage": "gate", "command": "/ok-to-test", "head_at": HEAD})
    again = pipeline.record(entry, {"stage": "gate", "command": "/ok-to-test", "head_at": HEAD})
    moved = pipeline.record(entry, {"stage": "publish", "command": "/publish", "head_at": HEAD})

    assert first == {"changed": False, "previous": None}
    assert again == {"changed": False, "previous": "gate"}
    assert moved == {"changed": True, "previous": "gate"}
    assert [item["stage"] for item in entry["stage_history"]] == ["gate", "publish"]


@pytest.mark.parametrize("argument", ["--no-write", "--json"])
def test_global_options_are_accepted_after_the_command(tmp_path, argument):
    ledger = tmp_path / "pipeline.json"

    assert pipeline.main(["--ledger", str(ledger), "add", "4440"]) == 0
    assert pipeline.main(["list", argument, "--ledger", str(ledger)]) == 0
