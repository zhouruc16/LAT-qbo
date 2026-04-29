# legacy/

Pre-existing artifacts from the `accounting` project before the refactor
into the `tiktok_qbo` package. Kept here for historical reference; not
part of the active pipeline.

| File / dir | What it was |
|---|---|
| `tiktok_to_qbo.py` | Original monolithic 22KB script. Superseded by `src/tiktok_qbo/`. |
| `QBO_Invoice_Importer_Design.docx` | Initial design doc. |
| `sample-dry-run/` | Old test outputs from the monolithic script. |
| `sample-reconcile.csv` | Old test reconciliation output. |
| `CLAUDE-original.md` | Pre-session CLAUDE.md notes. The new (post-session) CLAUDE.md is a thin pointer; the original had more inline detail and is preserved here. |
| `docs-context-stubs/` | Empty stub files (decisions.md, findings.md, questions.md, todos.md) that pre-existed in `docs/context/` from a prior memory-system template. The new `docs/context/` (in repo root) replaces them with numbered, content-rich files. |

If you need to reference the legacy implementation (e.g., to confirm
behavior matches), look here. The new pipeline in `src/tiktok_qbo/` is
the source of truth for all current and future runs.
