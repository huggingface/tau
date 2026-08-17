# Project trust and security

Tau resolves trust for the canonical destination cwd before reading ambient
project Markdown/JSON or importing project extensions. Protected inputs include
project skills, prompts, themes, system-prompt files, AGENTS.md context,
extension candidates, and reserved future project settings/package metadata.
User/global and explicit CLI resources remain eligible.

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

Published details: `website/content/guides/project-trust.md`.
