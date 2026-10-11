"""Run one Haven Point Prospect Scout session (first test).

Setup (once): `ant apply` the agent and environment files, which writes
claude-lock.json. This script reads the IDs from env vars, falling back to
the lockfile.

Run settings for the first test:
- on demand only (no schedule)
- up to 3 qualified companies
- $2.00 platform-enforced session budget
- one grader review, then at most one correction pass (max_iterations=1)
- prints model, tokens, web search count, and list cost at the end
"""

import datetime
import json
import os
import pathlib
import sys
import time

import anthropic

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "claude-lock.json"
RUBRIC = (pathlib.Path(__file__).parent / "rubric.md").read_text()

CONFIGURED_MODEL = "claude-opus-5-5"  # keep in sync with agents/haven-point-prospect-scout.md
BUDGET_CENTS = "200"  # $2.00, minor units as an integer string
MAX_COMPANIES = 3


def load_ids() -> tuple[str, str]:
    agent_id = os.getenv("PROSPECT_SCOUT_AGENT_ID")
    env_id = os.getenv("PROSPECT_SCOUT_ENV_ID")
    if agent_id and env_id:
        return agent_id, env_id
    if not LOCKFILE.exists():
        sys.exit("No IDs found. Run `ant apply` first (see prospect_scout/SETUP.md).")
    resources = json.loads(LOCKFILE.read_text())["resources"]
    return (
        resources["./agents/haven-point-prospect-scout.md"]["id"],
        resources["./environments/prospect-research.yaml"]["id"],
    )


def confirm_billing() -> None:
    print("Before the live run, confirm in the Console (platform.claude.com):")
    print("  1. `ant auth status` shows the organization and workspace you expect to bill.")
    print("  2. Settings > Billing shows an available credit balance for that organization.")
    print(f"  This session is capped at ${int(BUDGET_CENTS) / 100:.2f} list cost.")
    if input("Type 'yes' to start the live run: ").strip().lower() != "yes":
        sys.exit("Stopped before creating a session. Nothing was billed.")


def approve(event) -> bool:
    """A call the server could not judge. Ask the person at the terminal."""
    name = getattr(event, "name", "tool")
    print(f"\n[approval needed] {name}: {json.dumps(getattr(event, 'input', {}), default=str)[:500]}")
    return input("Allow this call? (y/N): ").strip().lower() == "y"


def outcome_description(today: str) -> str:
    return (
        f"Today is {today}. Research and return up to {MAX_COMPANIES} qualified prospect companies "
        "for Haven Point Consulting, following your system prompt's target profile and evidence rules. "
        "Return fewer if the evidence is insufficient. Write prospects.md and prospects.csv to "
        "/mnt/session/outputs/. You get one review from the grader and at most one correction pass. "
        "If the grader flags issues you cannot resolve in that pass, list them under 'Open issues' "
        "in prospects.md instead of trying again."
    )


def main() -> None:
    agent_id, env_id = load_ids()
    confirm_billing()

    client = anthropic.Anthropic()
    today = datetime.date.today().isoformat()

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title=f"Prospect Scout first test {today}",
        budget={"type": "limit", "max_list_cost": {"amount": BUDGET_CENTS, "currency": "USD"}},
    )
    print(f"Session: {session.id}")
    print(f"Watch live: https://platform.claude.com/workspaces/default/sessions/{session.id}")
    print("(swap 'default' for your workspace slug if you use a non-default workspace)\n")

    tokens = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    grader_tokens = {"input_tokens": 0, "output_tokens": 0}
    tool_calls: dict[str, int] = {}
    evaluations = []
    final_stop = None

    # Stream first, then send the kickoff while the stream is open.
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{
                "type": "user.define_outcome",
                "description": outcome_description(today),
                "rubric": {"type": "text", "content": RUBRIC},
                "max_iterations": 1,  # one grader review; the agent may run one final revision
            }],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, end="", flush=True)
            elif event.type in ("agent.tool_use", "agent.mcp_tool_use"):
                name = getattr(event, "name", "unknown")
                tool_calls[name] = tool_calls.get(name, 0) + 1
                if event.evaluated_permission == "ask":
                    client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[{
                            "type": "user.tool_confirmation",
                            "tool_use_id": event.id,
                            "result": "allow" if approve(event) else "deny",
                        }],
                    )
            elif event.type == "span.model_request_end":
                for key in tokens:
                    tokens[key] += getattr(event.model_usage, key, 0) or 0
            elif event.type == "span.outcome_evaluation_end":
                evaluations.append({"iteration": event.iteration, "result": event.result, "explanation": event.explanation})
                for key in grader_tokens:
                    grader_tokens[key] += getattr(event.usage, key, 0) or 0
                print(f"\n\n[grader, iteration {event.iteration}] {event.result}\n{event.explanation}\n")
            elif event.type == "session.error":
                print(f"\n[session error] {event}")
            elif event.type == "session.status_idle":
                if event.stop_reason.type != "requires_action":
                    final_stop = event.stop_reason.type
                    break
            elif event.type == "session.status_terminated":
                final_stop = "terminated"
                break

    if final_stop == "budget_reached":
        print("\nThe session paused at the $2.00 budget. Partial outputs (if any) are downloaded below.")

    # Outputs land in /mnt/session/outputs/ and take a moment to index.
    run_dir = pathlib.Path(__file__).parent / "runs" / session.id
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

    # Authoritative totals live on the session object.
    session = client.beta.sessions.retrieve(session.id)
    usage = session.usage
    served_model = getattr(getattr(session, "agent", None), "model", None)
    list_cost = getattr(usage, "list_cost", None)
    server_tools = getattr(usage, "server_tool_use", None)

    summary = {
        "session_id": session.id,
        "stop_reason": final_stop,
        "model": str(getattr(served_model, "id", served_model) or CONFIGURED_MODEL),
        "session_usage": usage.model_dump() if hasattr(usage, "model_dump") else str(usage),
        "agent_tokens_from_stream": tokens,
        "grader_tokens_from_stream": grader_tokens,
        "web_search_requests": getattr(server_tools, "web_search_requests", None),
        "tool_calls_seen": tool_calls,
        "list_cost": list_cost.model_dump() if hasattr(list_cost, "model_dump") else list_cost,
        "budget_cents": BUDGET_CENTS,
        "grader_evaluations": evaluations,
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    print("\n=== Run report ===")
    print(f"Model:               {summary['model']}")
    print(f"Stop reason:         {final_stop}")
    print(f"Input tokens:        {getattr(usage, 'input_tokens', tokens['input_tokens'])}")
    print(f"Output tokens:       {getattr(usage, 'output_tokens', tokens['output_tokens'])}")
    print(f"Cache read tokens:   {getattr(usage, 'cache_read_input_tokens', tokens['cache_read_input_tokens'])}")
    print(f"Grader tokens:       {grader_tokens['input_tokens']} in / {grader_tokens['output_tokens']} out")
    print(f"Web searches:        {summary['web_search_requests']} (tool calls seen: {tool_calls})")
    if list_cost is not None:
        # amount is in minor units (cents) as an integer string, same as the budget.
        print(f"Session list cost:   ${int(list_cost.amount) / 100:.2f} {list_cost.currency} (cap: $2.00)")
    else:
        print("Session list cost:   not reported yet; check the session in Console")
    print("List cost is priced at public list rates. Your invoiced amount appears in Console > Billing.")
    print(f"Full summary:        {run_dir / 'run_summary.json'}")


if __name__ == "__main__":
    main()
