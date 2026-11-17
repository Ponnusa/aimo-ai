# Paste into Claude Code in the new repository

Read `CLAUDE.md`, `README.md`, `PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/PROTOCOLS.md`, `docs/SECURITY.md`, `docs/ROADMAP.md`, and `TASKS.md` fully.

This is a **new repository**. Do not migrate or import the previous Valio hackathon application yet. The goal is AIMO BRAIN: an open, cross-organization business agent network, with food wholesale as the first domain module.

Begin with **Milestone M0 only**, then propose the implementation of M1. Before coding, summarize the planned folder structure, local run commands, database isolation approach, version-pinned A2A/MCP SDK compatibility and tests. Then implement M0 with a runnable local developer environment, a deterministic fake LLM, CI and meaningful smoke tests. Keep all secrets as environment variables and use fictional data.

Do not simulate A2A by making direct Python function calls between agents. In M1, use the official A2A SDK and independent networked services. Do not implement live ordering or Valio Aimo API calls without explicit authorization. After completing M0, run checks, update `TASKS.md`, explain the exact commands and test results, and stop for review before starting M1.
