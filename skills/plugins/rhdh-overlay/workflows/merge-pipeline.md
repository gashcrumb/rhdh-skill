# Workflow: Track PRs Through the Merge Pipeline

Keep a local list of overlay PRs and refresh where each one sits between opening and merging.
A code-clean PR is not a mergeable PR: it still needs the trusted-user gate, a publish for its
current head, smoke tests, and an e2e run. This workflow answers "where are these PRs now, and what
does each one need next" in one pass.

Read `references/ci-feedback.md` for the publish comment formats.

## The ledger

The ledger is a JSON file outside the repository, keyed by PR number, holding each PR's last known
stage and a short stage history. It lives at `$XDG_STATE_HOME/rhdh-overlay/pipeline.json`, falling
back to `~/.local/state/rhdh-overlay/pipeline.json`. Nothing is written into the checkout.

The ledger is what makes a repeated invocation cheap and comparable: the report names what changed
since the previous check instead of dumping the same table twice.

```bash
python scripts/pr-pipeline.py add 4440 4441 4442    # start tracking
python scripts/pr-pipeline.py status                 # refresh from GitHub and report
python scripts/pr-pipeline.py status --json          # same, machine-readable
python scripts/pr-pipeline.py status --no-write      # look without touching the ledger
python scripts/pr-pipeline.py list                   # what is tracked, no network
python scripts/pr-pipeline.py hold 4440 --reason "waiting on a named reviewer"
python scripts/pr-pipeline.py release 4440           # clear a hold
python scripts/pr-pipeline.py remove 4440            # stop tracking
```

`--repo`, `--ledger`, `--no-write` and `--json` are accepted before or after the command name.

**Hold deliberately.** A PR parked for a reason the pipeline cannot see — a human asked for a
specific reviewer, the fix already shipped on a release branch, a decision is pending — belongs in
the ledger as a hold with its reason. Otherwise it sits in the command plan forever, and the plan
stops being trustworthy at a glance.

## Stages

The stage is the most actionable fact about a PR, not its earliest unmet gate. A PR with failing e2e
evidence reports `e2e-failed` even when its publish predates the head, because the failure needs a
decision first. Everything non-actionable lands in the report's notes column.

| Stage | Meaning | Signal | Next |
|---|---|---|---|
| `code` | review findings open | `fullsend-ai-review` requested changes, no fix run since | `/fs-fix` |
| `fixing` | the fix agent is working | a `Fix · Started` comment with no outcome after it | wait |
| `sonar` | the quality gate failed for this head | the last `sonarqubecloud` comment says `Quality Gate failed` and postdates the head | `/fs-fix` against that comment |
| `gate` | waiting for a trusted user | no `/ok-to-test` from a member, owner, or collaborator | `/ok-to-test` |
| `publish` | no images for this head | no successful publish dated at or after the head commit | `/publish` |
| `publishing` | publish running | `publish` check pending, queued, or in progress | wait |
| `publish-failed` | publish failed for this head | failed publish report newer than the head | `/fs-diagnose` |
| `smoke` | images out, load check pending | publish succeeded, no smoke result for this head | wait |
| `smoke-failed` | smoke failed | failed smoke report newer than the head | `/fs-diagnose` |
| `e2e` | smoke green, tests pending | no e2e result for this head | wait |
| `e2e-failed` | tests failed | last e2e result for this head is not a pass | `/fs-diagnose` |
| `blocked` | tests green, merge refused | merge state blocked by a required check or approval | wait |
| `mergeable` | every gate green | pass for this head, merge state not blocked | merge |
| `held` | parked on purpose | the ledger says so | none |

## Invariants

1. **The gate is sticky, the evidence is not.** `/ok-to-test` authorises a PR once; later commits do
   not revoke it. A publish, smoke, or e2e result speaks only for the head it ran against, so a
   result older than the latest commit is not evidence. The script compares every result to the head
   commit and says so when a result predates it.
2. **Order is fix, gate, publish.** Publishing before the gate is refused outright — openshift-ci
   answers "Cannot trigger testing until a trusted user reviews the PR and leaves an `/ok-to-test`
   message" — and costs a second publish cycle once the gate arrives.
3. **The publish workflow starts the tests.** A successful publish report ends by posting
   `/test e2e-ocp-helm` itself, so a published head already has a run queued: the `e2e` stage means
   wait, not retrigger. The report asks for `/test e2e-ocp-helm` only when no run was started for the
   head. A rerun after a failure is a deliberate choice, and worth stating in the comment.
4. **A quality-gate failure outranks the gate and the publish.** Fixing SonarQube findings means another
   commit, and a commit invalidates every publish, smoke, and e2e result already earned, so the fix goes
   first. The request quotes the failing analysis comment's URL, because the findings live there and the
   agent reads them directly. A later `Quality Gate passed`, or a failure older than the head, is a
   superseded analysis and is not a stage.

   Spend **one** fix attempt on it, then check whether the analysis is actually required. In this
   repository it is not: the `main` ruleset requires exactly one status check, `publish`. An advisory
   failure therefore informs the merge instead of blocking it, and a second round that leaves a
   structural residue — duplication measured as a percentage of *new* lines punishes a small diff for
   a single repeated block — is money spent to satisfy a dashboard. Proceed, and record the residue in
   the merge rationale.
5. **Only trusted comments gate.** A slash command from a bot, or from an outside contributor, does
   not satisfy the gate. GitHub Apps report a `CONTRIBUTOR` association, which reads like a human's
   and is not one.
6. **One workspace per PR.** The e2e scope is `-w <workspace>`. If the script reports more than one
   workspace for a PR, the pipeline steps do not apply to it unchanged.

## Acting on the report

The script is read-only. Posting anything is an external write: invoke the named skill `mutation-gate`
and follow the gate it owns.

1. Run `status`. Read the command plan and the notes.
2. Group identical commands. State the whole set as one approved operation per PR — the target is
   `redhat-developer/rhdh-plugin-export-overlays#<number>@<head-sha>`, the preview body is the command
   — and wait for approval before posting any of them.
3. Post only what was approved, one PR at a time, and report each one's comment URL.
4. For `/fs-fix`, post the command the report prints. It is already the right body for the stage: the
   standard request to address the review threads and rebase, or, when the quality gate failed, that
   analysis comment's URL. Both point the agent at the threads or the findings, which it reads itself.
   Do not paraphrase the findings into the comment: a rewritten instruction is a new specification, and
   a wrong one becomes a commit. The report lists the review findings in its notes, for your reading.
5. Do not poll. An e2e run takes minutes to tens of minutes; post the step, report, and end the turn.
   The next `status` invocation shows what happened. A fix run is the same: it announces itself with a
   workflow link, then reports an outcome, and the `fixing` stage exists so a second request is not
   posted against a run that is already working.

## Reporting

Report the table, the changes since the previous check, the command plan, and anything held. Name the
stage for each PR rather than a bare pass or fail: "publish-failed for the current head, diagnosis
already posted" is actionable; "red" is not.
