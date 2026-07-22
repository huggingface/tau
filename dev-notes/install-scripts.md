# Tau install scripts

Tau's website now serves small bootstrap installers for macOS/Linux and Windows:

```text
website/static/install.sh
website/static/install.ps1
```

They remove the requirement that a new user already knows about or has `uv`.
Each script looks for `uv`, announces and runs Astral's official installer when
it is missing, installs `tau-ai` as an isolated uv tool, verifies the installed
command, and explains when a shell restart may be needed. Neither script uses
administrator privileges.

The scripts intentionally remain thin wrappers around `uv tool install tau-ai`.
Tau does not gain a second environment manager, and experienced users can still
use uv, pipx, or pip directly. Both scripts are published as readable static
files so users can inspect them before execution.

## Validation

Run the POSIX script syntax check and website build:

```text
sh -n website/static/install.sh
hugo --source website --minify
```

The normal Python test, lint, format, type-check, and package-build commands are
also expected to remain green.
