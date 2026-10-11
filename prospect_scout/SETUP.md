# Haven Point Prospect Scout: first test

An on-demand Claude Managed Agent. It finds up to 3 evidence-backed prospects and writes `prospects.md` and `prospects.csv`.

## What's in this repo

| File | Purpose |
| --- | --- |
| `agents/haven-point-prospect-scout.md` | Agent config (model, tools) plus the system prompt |
| `environments/prospect-research.yaml` | Cloud sandbox, unrestricted networking |
| `prospect_scout/rubric.md` | What the grader checks (starter rubric, tune after run 1) |
| `prospect_scout/run.py` | Starts one session, streams it, downloads outputs, prints the cost report |

## First-test settings

- **Trigger:** on demand only. No schedule.
- **Scope:** up to 3 companies. Fewer if the evidence is weak.
- **Budget:** $2.00 hard cap. If the agent hits it, the session pauses and it can't overspend. At most one in-flight model request can finish past the cap.
- **Grading:** one review, then at most one correction pass (`max_iterations: 1`). Anything still open is listed under "Open issues" in the report.
- **Report:** prints the model, input and output tokens, grader tokens, web search count, and session list cost.

## Step 1. Install tools (one time)

```sh
pip install -r prospect_scout/requirements.txt
ant --version   # needs 1.30.0 or later
```

If you don't have `ant`, install it from the Anthropic CLI docs.

## Step 2. Confirm billing BEFORE anything is created

```sh
ant auth login     # opens a browser; sign in to the Console org you want billed
ant auth status    # read the organization and workspace it reports
```

Then in the Console (platform.claude.com), check these:

- **Settings > Organization:** the name matches what `ant auth status` printed.
- **Settings > Billing:** the organization shows an available credit balance.
- **Max plan credits:** a claude.ai Max subscription and Console API billing are usually separate accounts. Check whether your Max plan includes API credits, and if so, which Console organization they're attached to. Billing will confirm this. Don't assume it.
- **Stale keys:** make sure no stale `ANTHROPIC_API_KEY` is set in your shell (`echo $ANTHROPIC_API_KEY`). A set key outranks your login and could bill a different org.

## Step 3. Create the agent and environment

```sh
ant apply --dry-run -v agents/haven-point-prospect-scout.md environments/prospect-research.yaml
ant apply agents/haven-point-prospect-scout.md environments/prospect-research.yaml
```

The dry run prints the plan, including the organization and workspace in its header. If those don't match Step 2, stop. Then commit `claude-lock.json`, which holds the IDs.

Setting up an agent and environment costs nothing. Charges start only when a session runs.

## Step 4. Run the test

```sh
python prospect_scout/run.py
```

The script makes you type `yes` before it creates a session. Outputs and `run_summary.json` land in `prospect_scout/runs/<session-id>/`.

## Changing things later

- **Agent prompt or tools:** edit the agent file and run `ant apply` again. This creates a new agent version.
- **Rubric, company count, or budget:** edit `rubric.md` or the constants at the top of `run.py`.
