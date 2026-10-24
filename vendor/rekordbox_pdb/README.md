# vendored: rekordbox-pdb (fragmede)

MIT-licensed, dependency-free reader/writer of Pioneer's rekordbox
DeviceSQL `export.pdb` format (https://github.com/fragmede/rekordbox-pdb),
vendored here so the converter + tests need no network installs.

This project uses it as an **independent validator and reference**:
`tools/onelibrary_to_pdb.py` (our PDB *writer*) and
`tools/onelibrary_to_pdb_test.py` verify their page layout, row directory
and string encodings against this library's reader behaviour, which was in
turn validated against real rekordbox exports. Its `FORMAT.md`-style rules
(row-dir stride 0x24, masks at base-4/base-2, offsets relative to the heap
start, DeviceSQL string kinds 0x90/0x40/short-ASCII) are the ground truth we
write to. See LICENSE (MIT, Copyright (c) 2026 fragmede).
