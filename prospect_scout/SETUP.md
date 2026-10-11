# Haven Point Prospect Scout: first test

An on-demand Claude Managed Agent. It finds up to 3 evidence-backed prospects and writes `prospects.md` and `prospects.csv`.

## What's in this project

```
agents/haven-point-prospect-scout.md     Agent config (model, tools) plus the system prompt
environments/prospect-research.yaml      Cloud sandbox, unrestricted networking
prospect_scout/rubric.md                 What the grader checks (starter rubric, tune after run 1)
prospect_scout/requirements.txt          Python dependency
prospect_scout/run.py                    Preflight, one session, outputs, cost report
prospect_scout/SETUP.md                  This file
```

Run every command from the project root, the folder that contains `agents/` and `prospect_scout/`.

## First-test settings

- **Trigger:** on demand only. No schedule.
- **Scope:** up to 3 companies. Fewer if the evidence is weak.
- **Budget:** a $2.00 hard cap, set by the platform when the session is created. At the cap, the session pauses instead of spending more. One model request that's already running can finish slightly past it.
- **Grading:** at most one grader review and at most one correction pass. Details are in "How grading works" below.
- **Report:** model, input and output tokens, grader tokens, web search count, and session list cost.

## Step 1. Install tools (one time)

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r prospect_scout/requirements.txt
ant --version   # needs 1.30.0 or later
```

If you don't have `ant`, install it from the Anthropic CLI docs.

## Step 2. Sign in and confirm billing

```sh
ant auth login     # opens a browser; sign in to the Console org you want billed
ant auth status    # shows the organization and workspace this login uses
```

`run.py` uses this same login. The Python SDK reads the profile that `ant auth login` saves, so you don't need an API key, and none is ever written into the code.

**Check that no environment variable overrides your login.** These commands report only "set" or "not set". They never print the value.

```sh
[ -n "${ANTHROPIC_API_KEY:-}" ] && echo "ANTHROPIC_API_KEY is set" || echo "ANTHROPIC_API_KEY is not set"
[ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] && echo "ANTHROPIC_AUTH_TOKEN is set" || echo "ANTHROPIC_AUTH_TOKEN is not set"
```

If either one is set, it takes priority over your login and may bill a different organization. To use your login instead, run `unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN`.

Then, in the Console (platform.claude.com), check these:

- **Settings > Organization:** the name matches what `ant auth status` printed.
- **Settings > Billing:** the organization shows available credits.
- **Max plan credits:** a claude.ai Max subscription and Console API billing are usually separate accounts. Billing shows whether any API credits from Max apply, and which organization they're attached to. Don't assume they do.

## Step 3. Create the agent and environment

```sh
ant apply --dry-run -v agents/haven-point-prospect-scout.md environments/prospect-research.yaml
ant apply agents/haven-point-prospect-scout.md environments/prospect-research.yaml
```

The dry run prints the plan, and its header shows the organization and workspace. If those don't match Step 2, stop. The real apply writes `claude-lock.json` with the IDs. Keep that file next to `agents/`.

Setting up an agent and environment costs nothing. Charges start only when a session runs.

## Step 4. Run the free preflight

```sh
python prospect_scout/run.py --check
```

This never creates a session and costs nothing. It checks the following and stops on the first problem:

1. **Project files:** the agent file, environment file, `rubric.md`, and `requirements.txt` are all present, and the rubric isn't empty.
2. **IDs:** the agent and environment IDs are in `claude-lock.json`, or set as `PROSPECT_SCOUT_AGENT_ID` / `PROSPECT_SCOUT_ENV_ID`.
3. **Credential sources:** reports which ones are set (yes or no only, never the value).
4. **Authentication:** makes a free, read-only API call to confirm your login works.
5. **Right organization:** confirms the agent and environment exist for this login. If you're signed in to a different org than `ant apply` used, this step catches it.

## Step 5. Run the test

```sh
python prospect_scout/run.py
```

The script runs the same preflight again, then asks you to type `yes` before it creates the paid session. Outputs land in `prospect_scout/runs/<session-id>/`:

- `prospects.md` and `prospects.csv`: the deliverables
- `grader_review.md`: what the grader flagged
- `run_summary.json`: model, tokens, searches, cost, and the grading record

## How grading works

**What the platform does.** The script sends `max_iterations: 1`. This caps the grader at **one review** of the first draft.

- **Draft passes:** the session ends.
- **Draft falls short:** the result is `max_iterations_reached`. The platform gives the agent one final turn, which its docs call an "acknowledgment" turn, and then the session stops. No second review runs, and that final turn is never graded.

**What the script adds.** The platform's final turn isn't guaranteed to fix anything, so the script enforces the rule itself:

- **If the agent edited its files in that final turn:** that counts as the correction pass, and the script sends nothing more.
- **If it didn't:** the script sends exactly one correction request with the grader's feedback. This is skipped if the session already hit the $2 budget.
- **Guard:** if a second grader review ever starts, the script interrupts it.

**Result:** at most one review and at most one correction. Unresolved issues are reported, never retried. They appear under "Open issues" in `prospects.md` and in `grader_review.md`.

**Since the correction is never re-graded,** read "Open issues" before you use the list.

## Changing things later

- **Agent prompt or tools:** edit the agent file and run `ant apply` again. This creates a new agent version.
- **Rubric, company count, or budget:** edit `rubric.md` or the constants at the top of `run.py`.
