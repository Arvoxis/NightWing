# Context / memory snapshot

These three files are a copy of the prior Claude Code session's persistent memory,
snapshotted so the knowledge travels inside this repo.

- `MEMORY.md` — the index (one line per memory).
- `quorum-project.md` — the full merged project state (decisions, architecture, roles, plan).
- `quorum-wire-contract.md` — the message-contract reference.

**To resume on a new machine:** the primary handoff is `../../HANDOFF.md` — read that
first; it is self-contained. These files are the same knowledge in the memory format.

**Optional — restore auto-loading memory** (so a new Claude Code "just knows" the
project without being told): copy `MEMORY.md`, `quorum-project.md`, and
`quorum-wire-contract.md` into the new machine's memory directory:

- Windows: `C:\Users\<you>\.claude\projects\<encoded-project-path>\memory\`
- macOS/Linux: `~/.claude/projects/<encoded-project-path>/memory/`

The `<encoded-project-path>` is derived from wherever you put this repo. If unsure,
just skip this — reading `HANDOFF.md` is enough to continue.
