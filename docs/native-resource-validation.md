# Native drawing regression checks

Run on a new, disposable DWG in an isolated managed job. Load the current
`revision_tools.lsp` and `tests/native/resources.lsp` through `system.execute_lisp`.
Then call, through `pcad:run`:

```lisp
(pcad:run 'pcad-test:resources nil)
(pcad:run 'pcad-test:plot-resources '("C:/scratch/new-resource-test.pdf"))
```

The first function checks layer preservation, extensionless SHX and Windows
TrueType resolution, disabled/unassigned plot styles and document-state preservation.
It requires the standard `simplex.shx` and Windows `arial.ttf` resources. The second
creates test geometry and simulates a missing CTB by editing the disposable
layout's DXF record. No installed resource is deleted or changed. It verifies
refusal without output, successful plotting with `monochrome.ctb`, state restoration
and existing-output refusal. Never run it on an issued or user-owned drawing.

Validation on Windows x64 / AutoCAD LT 2025:

- 18 resource/layer checks and 8 plot checks passed in native AutoLISP.
- The missing CTB produced `PLOT_STYLE_MISSING` and no PDF. Restoring a valid CTB
  produced a nonempty PDF with font/status evidence and `rendering_verified: false`.
- The original Windows claim/cancel race test failed on the unmodified protocol-2
  implementation with two winners. The protocol-3 fence passes it.
- Linux Python 3.12: 328 tests passed. Windows Python 3.14: 320 tests passed;
  `tests/test_wsl_launcher.py` was excluded because those eight checks require WSL.

These are bounded regression checks, not the full native release qualification.
The existing `native-qualification.json` is intentionally unchanged. Use the
[queue qualification workflow](queue-workflow.md) before declaring another build
production-qualified. Restart both Python and AutoLISP when changing builds.
