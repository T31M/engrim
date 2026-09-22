## Project Memory (engrim) - use it every session, scoped by project path

A project-tagged SQLite memory store persists decisions, facts, feedback, and state across
sessions. Lifecycle hooks inject relevant context and record the raw conversation, but hooks do
not create curated memory records for you.

- At the start of non-trivial work, use `engrim_recall` when the MCP server is available, or run
  `engrim recall -q "<topic>"`. Use `engrim_context` or `engrim context` for the current memory pack.
- Immediately after a decision, durable fact, user correction, system-state change, user detail,
  or external reference is established, use `engrim_add`. The CLI fallback is
  `engrim add -t <decision|fact|feedback|state|user|reference> -s "<one line>" [--tags a,b]`.
- Before ending a turn that did non-trivial work, check that each durable item is curated. Use
  `engrim_review` when available, or run `engrim review`.
- Add `--global` for cross-project truths about the user or working conventions.
- Supersede stale records with `engrim supersede --id N --status superseded`.

Keep records high-signal. Retrieval precision is more important than record volume.
