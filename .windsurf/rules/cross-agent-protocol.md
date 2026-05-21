# Cross-Agent Coordination Protocol — Windsurf

## Multi-Agent Team

You (Windsurf) are part of a multi-agent team. Other active agents: Cline, Claude Code.
All agents coordinate through the AutoClaw Orchestrator.

## Your Mailbox

Check at the START of every task and AFTER completing work:
- **Inbox**: `.autoclaw/orchestrator/comms/inboxes/windsurf/`
- **Shared**: `.autoclaw/orchestrator/comms/inboxes/shared/`

Message types: review_request, review_response, consensus_vote, task_claim,
task_complete, finding_report, question, answer.

## Send Messages

Write JSON to target inbox. Filename: `{timestamp}-{type}-windsurf.json`
- To Cline: `.autoclaw/orchestrator/comms/inboxes/cline/`
- To Claude Code: `.autoclaw/orchestrator/comms/inboxes/claude-code/`
- Broadcast: `.autoclaw/orchestrator/comms/inboxes/shared/`

## On Task Completion

1. Broadcast task_complete to shared/
2. Write review_request to other agents' inboxes
3. Check YOUR inbox for pending reviews

## Consensus

Tasks require 2/3 majority approval. Security findings require unanimous.
Write votes to `consensus/active/{task_id}-windsurf.json`.

## Scope

Check `.autoclaw/orchestrator/sprints/plan-summary.yaml` for assignments.
Stay in your assigned scope. Coordinate via messages for cross-scope changes.
