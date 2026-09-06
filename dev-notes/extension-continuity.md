# Awaited extension continuity

Memory extensions need more than a post-compaction instruction: they must be able
to finish a write while original context exists, then recover its receipt on the
correct branch after reload. This change adds consistent extension notifications
around all actual compaction paths, read-only persisted active-branch entries, and
a bounded tool-free summarization call through the current provider.

CodingSession owns compaction. One helper pairs notifications around summary and
replacement; manual, detailed, threshold and overflow entry points use it.
The extension API copies input/output values and guards its generation before
and after the summary. No provider ownership or credentials move to extensions.
The extension owns the awaited call and cancellation; no detached work is created.

This follows Pi's branch inspection model while preserving Tau's separation:
provider/harness primitives stay frontend-free, session policy stays in coding,
and persistence destinations remain extension policy. Entry-appended delivery is
not needed for branch receipt reconstruction and is not changed here.

The existing observation failure contract is preserved: an extension failure is
a diagnostic, not a compaction veto. External writes must validate receipts and
report failures honestly. Threshold/manual frontend iterator delivery and TUI
status remain separate from the extension callback fix (refs #506).

Verification: `uv run pytest tests/test_extension_continuity.py tests/test_coding_session.py`.
The tests block a pre-compaction callback, prove history is unchanged until it
finishes, append a custom receipt, exercise all four paths, cancellation/no-op,
and tool-free summary without transcript mutation. Full-suite results belong in
the PR verification report.
