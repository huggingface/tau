# Project trust and security

Tau resolves trust for the canonical destination cwd before reading ambient
project Markdown/JSON or importing project extensions. Protected inputs include
project skills, prompts, themes, system-prompt files, AGENTS.md context,
extension candidates, and reserved future project settings/package metadata.
User/global and explicit CLI resources remain eligible. Trusted built-in
extensions are installed Tau package code, load before this decision even with
`--no-extensions`, and are not ambient project candidates. Their presence alone
never creates a project trust prompt. Hidden status affects ordinary listings,
not execution or trust.

Interactive users can save exact or displayed-parent decisions or choose a
run-only result. `~/.tau/trust.json` is a locked, atomically replaced version-1
store. `defaultProjectTrust` in user `~/.tau/settings.json` is `ask`, `always`,
or `never`; headless `ask`/`never` decline. `--approve` and `--no-approve` are
run-only. Cancelling the interactive startup decision exits Tau; continuing
without project inputs requires selecting a decline option. Trusted project
extensions additionally require `--project-extensions`.

Project trust is only an input-loading guard. It is not a filesystem, process,
shell, network, tool, credential, provider, model, package-install,
prompt-injection, or exfiltration sandbox. Use OS/container/VM isolation and
restricted credentials/network when isolation is required.

## Built-in local-backend boundary

Built-in local backends are trusted Tau package code and do not create a project
trust prompt. Their provider/backend definitions live only in the active,
generation-owned runtime. Endpoint settings and safe model snapshots, when a
backend supports them, are user-level integration state—not project inputs.

Configuration secrets are collected by the host as secret fields, passed only to
the backend transaction, and kept out of reprs, snapshots, session metadata, and
diagnostics. No API key is synthesized when authentication is optional or absent.
A local backend never stops a server or deletes model files as part of reset.

Published details: `website/content/guides/project-trust.md`.
