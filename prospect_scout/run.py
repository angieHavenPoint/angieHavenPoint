"""Run one Haven Point Prospect Scout session (first test).

Usage:
    python prospect_scout/run.py --check   # free preflight only, never creates a session
    python prospect_scout/run.py           # preflight, then the paid run (asks for 'yes' first)

Run settings for the first test:
- on demand only (no schedule)
- up to 3 qualified companies
- $2.00 platform-enforced session budget, set when the session is created
- at most one grader review and at most one correction pass (see GRADING below)
- prints model, tokens, web search count, and session list cost at the end

GRADING (what max_iterations=1 does, and what this script adds):
- The platform's `max_iterations` caps grader evaluations. With 1, the grader
  reviews the first draft once. If criteria are unmet, the result is
  `max_iterations_reached`, the platform gives the agent one final turn
  (documented as an "acknowledgment" turn, which is not graded), then the
  session goes idle. No second review runs.
- That final turn is not guaranteed to revise the files. So after it, this
  script checks whether the agent edited anything. If not, it sends exactly one
  correction request. Either way there is at most one correction pass.
- Guard: if a second grader review ever starts (iteration 1), the script
  interrupts it.
- Whatever the grader flagged is saved to grader_review.md in the run folder,
  so unresolved issues are reported, never retried.

Authentication: the SDK resolves credentials itself (ANTHROPIC_API_KEY, then
ANTHROPIC_AUTH_TOKEN, then the profile written by `ant auth login`). This script
never reads, prints, or stores a credential. It only reports whether the
environment variables are set.
"""

import argparse
import datetime
import json
import os
import pathlib
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = pathlib.Path(__file__).resolve().parent
AGENT_FILE = "agents/haven-point-prospect-scout.md"
ENV_FILE = "environments/prospect-research.yaml"
REQUIRED_FILES = [
    REPO_ROOT / AGENT_FILE,
    REPO_ROOT / ENV_FILE,
    HERE / "rubric.md",
    HERE / "requirements.txt",
]
LOCKFILE = REPO_ROOT / "claude-lock.json"

CONFIGURED_MODEL = "claude-opus-5-5"  # keep in sync with the agent file
BUDGET_CENTS = "200"  # $2.00, minor units as an integer string
MAX_COMPANIES = 3
MAX_GRADER_REVIEWS = 1  # passed as max_iterations
EDIT_TOOLS = {"write", "edit", "bash"}


# ---------------------------------------------------------------------------
# Preflight: everything here is free. No session is created.
# ---------------------------------------------------------------------------

def fail(msg: str) -> None:
    sys.exit(f"PREFLIGHT FAILED: {msg}\nNo session was created. Nothing was billed.")


def check_files() -> tuple[str, str]:
    missing = [str(p.relative_to(REPO_ROOT)) for p in REQUIRED_FILES if not p.is_file()]
    if missing:
        fail("missing project files: " + ", ".join(missing))
    if not (HERE / "rubric.md").read_text().strip():
        fail("prospect_scout/rubric.md is empty")
    print("[ok] project files present")

    agent_id = os.getenv("PROSPECT_SCOUT_AGENT_ID")
    env_id = os.getenv("PROSPECT_SCOUT_ENV_ID")
    if agent_id and env_id:
        print("[ok] agent and environment IDs from environment variables")
        return agent_id, env_id
    if not LOCKFILE.is_file():
        fail("claude-lock.json not found. Run the `ant apply` step in SETUP.md first.")
    try:
        resources = json.loads(LOCKFILE.read_text())["resources"]
        ids = resources[f"./{AGENT_FILE}"]["id"], resources[f"./{ENV_FILE}"]["id"]
    except (KeyError, json.JSONDecodeError):
        fail(f"claude-lock.json has no entries for {AGENT_FILE} and {ENV_FILE}. Re-run `ant apply`.")
    print("[ok] agent and environment IDs from claude-lock.json")
    return ids


def report_credential_sources() -> None:
    """Report which credential sources are present. Never prints a value."""
    def state(name: str) -> str:
        return "set" if os.environ.get(name) else "not set"

    print(f"[info] ANTHROPIC_API_KEY: {state('ANTHROPIC_API_KEY')}")
    print(f"[info] ANTHROPIC_AUTH_TOKEN: {state('ANTHROPIC_AUTH_TOKEN')}")
    print(f"[info] ANTHROPIC_PROFILE: {state('ANTHROPIC_PROFILE')}")
    config_dir = pathlib.Path(os.environ.get("ANTHROPIC_CONFIG_DIR", pathlib.Path.home() / ".config" / "anthropic"))
    print(f"[info] `ant auth login` profile folder: {'found' if config_dir.is_dir() else 'not found'}")
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("[warn] An environment variable credential is set. It takes priority over your")
        print("       `ant auth login` profile and may bill a different organization.")
        print("       Unset it to use your login: unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN")


def check_auth_and_ids(agent_id: str, env_id: str):
    try:
        import anthropic
    except ImportError:
        fail("the anthropic package is not installed. Run: pip install -r prospect_scout/requirements.txt")

    try:
        client = anthropic.Anthropic()
        client.models.list(limit=1)  # free, read-only: proves the credential works
    except TypeError:
        # The SDK raises TypeError when it finds no credential source at all.
        fail("no credentials found. Run `ant auth login` (see SETUP.md step 2) and try again.")
    except anthropic.AuthenticationError:
        fail("credentials were rejected. Run `ant auth login` and try again.")
    except anthropic.APIConnectionError:
        fail("could not reach the Anthropic API. Check your connection.")
    except anthropic.AnthropicError as e:
        fail(f"could not authenticate ({type(e).__name__}). Run `ant auth login` and try again.")
    print("[ok] authenticated (free read-only check)")

    try:
        agent = client.beta.agents.retrieve(agent_id)
        client.beta.environments.retrieve(env_id)
    except anthropic.NotFoundError:
        fail("agent or environment not found for this login. You may be signed in to a different "
             "organization or workspace than the one `ant apply` used.")
    except anthropic.PermissionDeniedError:
        fail("this login can't access the agent or environment (permission denied).")
    model = getattr(agent.model, "id", agent.model)
    print(f"[ok] agent and environment found (model: {model})")
    return client


def confirm_billing() -> None:
    print("\nBefore the paid run, confirm in the Console (platform.claude.com):")
    print("  1. `ant auth status` shows the organization and workspace you expect to bill.")
    print("  2. Settings > Billing shows available credits for that organization.")
    print(f"  This session is capped at ${int(BUDGET_CENTS) / 100:.2f} list cost.")
    if input("Type 'yes' to start the paid run: ").strip().lower() != "yes":
        sys.exit("Stopped before creating a session. Nothing was billed.")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def outcome_description(today: str) -> str:
    return (
        f"Today is {today}. Research and return up to {MAX_COMPANIES} qualified prospect companies "
        "for Haven Point Consulting, following your system prompt's target profile and evidence rules. "
        "Return fewer if the evidence is insufficient. Write prospects.md and prospects.csv to "
        "/mnt/session/outputs/. The grader reviews your work once. If it flags issues, make one "
        "correction pass, then list anything still unresolved under 'Open issues' in prospects.md. "
        "Do not attempt further rounds."
    )


def correction_message(explanation: str) -> str:
    return (
        "This is your one correction pass. The grader's review is below. Fix what you can in "
        "/mnt/session/outputs/prospects.md and prospects.csv without padding the list or weakening "
        "the evidence rules. List anything you could not resolve under 'Open issues' in prospects.md. "
        "Then stop.\n\nGrader review:\n" + explanation
    )


def approve(event) -> bool:
    """A call the server could not judge. Ask the person at the terminal."""
    print(f"\n[approval needed] {event.name}: {json.dumps(event.input, default=str)[:500]}")
    return input("Allow this call? (y/N): ").strip().lower() == "y"


def drain(client, session_id: str, events: list, state: dict) -> str:
    """Open the stream, send events, and process until the session stops. Returns the stop reason."""
    with client.beta.sessions.events.stream(session_id=session_id) as stream:
        client.beta.sessions.events.send(session_id=session_id, events=events)
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, end="", flush=True)
            elif event.type in ("agent.tool_use", "agent.mcp_tool_use"):
                state["tool_calls"][event.name] = state["tool_calls"].get(event.name, 0) + 1
                if state["evaluations"] and event.name in EDIT_TOOLS:
                    state["edited_after_review"] = True
                if event.evaluated_permission == "ask":
                    client.beta.sessions.events.send(
                        session_id=session_id,
                        events=[{
                            "type": "user.tool_confirmation",
                            "tool_use_id": event.id,
                            "result": "allow" if approve(event) else "deny",
                        }],
                    )
            elif event.type == "span.model_request_end":
                for key in state["tokens"]:
                    state["tokens"][key] += getattr(event.model_usage, key, 0) or 0
            elif event.type == "span.outcome_evaluation_start":
                if event.iteration >= MAX_GRADER_REVIEWS and not state["second_review_blocked"]:
                    print("\n[guard] A second grader review started. Interrupting it.")
                    state["second_review_blocked"] = True
                    client.beta.sessions.events.send(session_id=session_id, events=[{"type": "user.interrupt"}])
            elif event.type == "span.outcome_evaluation_end":
                state["evaluations"].append({
                    "iteration": event.iteration,
                    "result": event.result,
                    "explanation": event.explanation,
                })
                for key in state["grader_tokens"]:
                    state["grader_tokens"][key] += getattr(event.usage, key, 0) or 0
                print(f"\n\n[grader review, iteration {event.iteration}] {event.result}\n{event.explanation}\n")
            elif event.type == "session.error":
                print(f"\n[session error] {getattr(event, 'error', event)}")
            elif event.type == "session.status_idle":
                if event.stop_reason.type != "requires_action":
                    return event.stop_reason.type
            elif event.type == "session.status_terminated":
                return "terminated"
    return "stream_closed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="run the free preflight only")
    args = parser.parse_args()

    print("=== Preflight (free) ===")
    agent_id, env_id = check_files()
    report_credential_sources()
    client = check_auth_and_ids(agent_id, env_id)
    if args.check:
        print("\nPreflight passed. No session was created. Run without --check for the paid run.")
        return
    confirm_billing()

    today = datetime.date.today().isoformat()
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title=f"Prospect Scout first test {today}",
        budget={"type": "limit", "max_list_cost": {"amount": BUDGET_CENTS, "currency": "USD"}},
    )
    print(f"\nSession: {session.id}")
    print(f"Watch live: https://platform.claude.com/workspaces/default/sessions/{session.id}")
    print("(swap 'default' for your workspace slug if you use a non-default workspace)\n")

    state = {
        "tokens": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        "grader_tokens": {"input_tokens": 0, "output_tokens": 0},
        "tool_calls": {},
        "evaluations": [],
        "edited_after_review": False,
        "second_review_blocked": False,
        "correction_sent": False,
    }

    stop = drain(client, session.id, [{
        "type": "user.define_outcome",
        "description": outcome_description(today),
        "rubric": {"type": "text", "content": (HERE / "rubric.md").read_text()},
        "max_iterations": MAX_GRADER_REVIEWS,
    }], state)

    # One correction pass, only if the review found gaps and the agent hasn't corrected yet.
    review = state["evaluations"][0] if state["evaluations"] else None
    needs_fix = review is not None and review["result"] in ("max_iterations_reached", "needs_revision")
    if needs_fix and not state["edited_after_review"] and stop not in ("budget_reached", "terminated"):
        print("\n[correction] Sending the one correction pass.\n")
        state["correction_sent"] = True
        stop = drain(client, session.id, [{
            "type": "user.message",
            "content": [{"type": "text", "text": correction_message(review["explanation"])}],
        }], state)

    if stop == "budget_reached":
        print("\nThe session paused at the $2.00 budget. Partial outputs (if any) are downloaded below.")

    # Outputs land in /mnt/session/outputs/ and take a moment to index.
    run_dir = HERE / "runs" / session.id
    run_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for _ in range(3):
        files = client.beta.files.list(scope_id=session.id, betas=["managed-agents-2026-04-01"]).data
        if files:
            break
        time.sleep(2)
    for f in files:
        client.beta.files.download(f.id).write_to_file(run_dir / f.filename)
        print(f"Saved {run_dir / f.filename}")

    # Unresolved grader findings are reported, never retried.
    if state["evaluations"]:
        lines = ["# Grader review", ""]
        for ev in state["evaluations"]:
            lines += [f"## Iteration {ev['iteration']}: {ev['result']}", "", ev["explanation"], ""]
        lines += [
            "The grader reviewed once. Any correction after this review was not re-graded,",
            "so check prospects.md 'Open issues' and the items above before using the list.",
        ]
        (run_dir / "grader_review.md").write_text("\n".join(lines))

    # Authoritative totals live on the session object.
    session = client.beta.sessions.retrieve(session.id)
    usage = session.usage
    list_cost = usage.list_cost
    server_tools = usage.server_tool_use
    model = getattr(session.agent.model, "id", session.agent.model) or CONFIGURED_MODEL

    summary = {
        "session_id": session.id,
        "stop_reason": stop,
        "model": str(model),
        "grader_reviews": len(state["evaluations"]),
        "correction_pass": "agent's final turn" if state["edited_after_review"] and not state["correction_sent"]
        else ("sent by script" if state["correction_sent"] else "not needed or not possible"),
        "second_review_blocked": state["second_review_blocked"],
        "session_usage": usage.model_dump(),
        "agent_tokens_from_stream": state["tokens"],
        "grader_tokens_from_stream": state["grader_tokens"],
        "web_search_requests": getattr(server_tools, "web_search_requests", None),
        "tool_calls_seen": state["tool_calls"],
        "list_cost_usd": int(list_cost.amount) / 100 if list_cost else None,
        "budget_usd": int(BUDGET_CENTS) / 100,
        "grader_evaluations": state["evaluations"],
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    print("\n=== Run report ===")
    print(f"Model:               {summary['model']}")
    print(f"Stop reason:         {stop}")
    print(f"Grader reviews:      {summary['grader_reviews']} (correction pass: {summary['correction_pass']})")
    print(f"Input tokens:        {usage.input_tokens}")
    print(f"Output tokens:       {usage.output_tokens}")
    print(f"Cache read tokens:   {usage.cache_read_input_tokens}")
    print(f"Grader tokens:       {state['grader_tokens']['input_tokens']} in / {state['grader_tokens']['output_tokens']} out")
    print(f"Web searches:        {summary['web_search_requests']} (tool calls seen: {state['tool_calls']})")
    if list_cost is not None:
        # amount is in minor units (cents) as an integer string, same as the budget.
        print(f"Session list cost:   ${int(list_cost.amount) / 100:.2f} {list_cost.currency} (cap: $2.00)")
    else:
        print("Session list cost:   not reported yet; check the session in Console")
    print("List cost is priced at public list rates. Your invoiced amount appears in Console > Billing.")
    print(f"Full summary:        {run_dir / 'run_summary.json'}")


if __name__ == "__main__":
    main()
