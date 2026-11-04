# Guarded rotation qualification

AutoCAD LT 2027, Windows File IPC, 2026-11-03. All native mutations were
performed on a disposable copy of a dense production P&ID.

- Rotated three pressure instrument inserts from 270 degrees to zero. Native
  readback preserved insertion anchors, XYZ scales and attribute content; all
  attached attribute rotations became zero. Screenshot confirmed upright text
  and retained connections on the circular/orthogonal symmetric symbols.
- Resubmitting stale rotation expectations failed before mutation or journal
  creation. The diagnostic identified the handle, DXF 50 and old/actual values.
- Rotated an X-mirrored level instrument from zero to 90 degrees and back.
  Native readback preserved signed scales and restored attribute positions within
  1e-8 drawing units; the final screenshot confirmed lettering and connections.

This qualifies these block families, not arbitrary symbol ports. A rotation
transforms the entire insert and its attributes. Check transformed ports and
nearby geometry before using it on asymmetric equipment or valves. No automatic
pipe or stem repair is performed.
