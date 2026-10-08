# Contributing to AlvaOS

Join us in making self-hosting simple, stable, and transparent.

## Philosophy
- **Stability first**: conservative tech over cutting-edge features.
- **Simplicity**: no overengineering; keep code boring and explicit.
- **Transparent**: no magic, no hidden telemetry.

## Getting started
1. **Explore**: read [STRUCTURE.md](STRUCTURE.md), the [ROADMAP.md](ROADMAP.md) and the [README](/README.md).
2. **Pick an area**: backend, frontend, Android app, installer/scripts, or docs.
3. **Workflow**: fork, branch, code and test, open a pull request.

## Before you open a pull request
- `ruff check .` has no findings, and `cd backend && python -m pytest` passes. Add tests for logic you add or change.
- Frontend: look at it in a browser at desktop and phone width, in light and dark.
- Android: `cd android && ./gradlew :core:test` (the app itself needs the Android SDK).
- Docs: add an entry to the top of [BACKLOG.md](BACKLOG.md) (what changed and why, in a few bullets), update [ROADMAP.md](ROADMAP.md) if it moves something, and add steps to [TESTING.md](TESTING.md) for anything that needs a real machine.
- Validate what ends up in config files and commands; the web UI never runs system commands directly.
- Keep a pull request to one subject.

## Guidelines
- **General**: clear names, explicit error handling, idempotent scripts.
- **Backend**: log operations; keep privileged work behind the helper and its policy.
- **Frontend**: responsive, reusable components, plain language.
- **Comments**: say *why*, not *what*. If the code says it, the comment does not. No filler, no comments that only restate the next line.
- **Docs**: simple, up-to-date language with examples.

## AI tools: assisted, not generated
AI tools (chat assistants, code assistants) may be used here, and AlvaOS itself is built with them. The rule is that **the human is responsible, not the tool**:

- **You understand it.** You can explain every line you submit and why it is there. "The AI wrote it" is not an answer to a review question.
- **You read it.** Every line is read by a human before it is committed, including tests and docs. No unreviewed bulk output.
- **You test it.** Run the code and the tests. A change that only looks right is not done; things that need real hardware go into [TESTING.md](TESTING.md) and are marked as untested until someone ran them.
- **No invented facts.** Check names, flags, APIs, versions and links against the real thing. AI tools make plausible things up.
- **Clean up after it.** Remove generic comments, needless abstractions, dead code and padding before you commit. The result should read like the code around it.
- **Licenses and secrets.** Do not paste code you may not use, and never put private data, keys or tokens into a tool.
- **Say so when it matters.** You do not need to label every change, but if a large part of a change was machine-drafted, mention it in the pull request so reviewers know where to look harder.

Pull requests that are obvious unreviewed AI output (code nobody can explain, made-up APIs, boilerplate comments) are closed with a request to redo them.

## Testing and support
- Automated: `pytest` for the backend, `:core:test` for the Android core, CI on every push.
- Manual: [TESTING.md](TESTING.md) is the checklist for a real installation.
- **Questions**: open an issue for bugs or start a discussion for ideas.

*Contributions are licensed under the same license as AlvaOS as a whole. Thank you for helping simplify NAS management.*
