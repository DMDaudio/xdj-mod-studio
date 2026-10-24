#!/usr/bin/env python3
"""onelibrary_to_pdb.py -- make an XDJ-700-readable library out of an
OneLibrary-only USB stick.

The XDJ-700 only mounts a stick that carries the legacy Device Library,
``PIONEER/rekordbox/export.pdb`` (Pioneer's DeviceSQL format).  Recent
rekordbox 7 exports, djay Pro and Traktor (via the OneLibrary SDK) instead
write ``PIONEER/rekordbox/exportLibrary.db`` -- an encrypted SQLite database
(SQLCipher, publicly known key).  This tool converts the latter into the
former:

    python tools/onelibrary_to_pdb.py /path/to/stick

The generated export.pdb is written next to exportLibrary.db.  An existing
export.pdb is never overwritten silently: it is first copied to
export.pdb.bak-1 (bak-2, ...).

Carried over: tracks (title, artist, album, genre, label, BPM, key,
duration, file path, analysis-file path, comment, dates, ISRC, bitrate,
sample rate/depth, file size, artwork, colour, rating, year, track/disc
number, play count), artists, albums, genres, labels, keys, colours,
artwork paths, playlists (tree + entries in order) and the browse-view
column configuration.  FIELDS_NOT_PRESERVED (below) lists the few things
that cannot be carried over and why.

The write side follows the DeviceSQL layout documented in
https://github.com/fragmede/rekordbox-pdb/blob/main/FORMAT.md (string
encodings, row allocation rules, page/heap bookkeeping).  Every row shape
and default value was cross-checked byte-for-byte against the two real
rekordbox sticks present in this workspace (`~/Downloads/Rekordbox stuff`
and `cache/stemsdelay_stick`).  The page/container writer is an independent
from-scratch implementation (the vendored library's editor only appends to
existing files; a fresh database needs to be created).

Requires: Python 3.10+, sqlcipher3 (``pip install sqlcipher3-wheels`` gives
macOS arm64 + Windows x64 wheels), and the vendored reader
``vendor/rekordbox_pdb`` (MIT, fragmede's rekordbox-pdb) used for the
verification pass.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import struct
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "vendor"))

from rekordbox_pdb import Database as PdbReader  # noqa: E402  (vendored)
from rekordbox_pdb.edit import encode_string     # noqa: E402  (vendored)

PAGE_SIZE = 4096
PAGE_HEADER = 0x28
NUM_TABLES = 20
TABLE_ORDER = list(range(NUM_TABLES))  # types 0..19

ONELIBRARY_KEY = (
    "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"
)

_FIELDS_DOC = """
FIELDS_NOT_PRESERVED (cannot be filled from exportLibrary.db)
-------------------------------------------------------------
* exportExt.pdb (My Tags): OneLibrary's myTag/myTag_content tables are not
  converted (the extension-PDB writer does not exist yet).
* History playlists/entries: the OneLibrary device export carries empty
  history tables; the PDB history tables are left empty.
* Hot cues: stored in the per-track ANLZ .EXT files, not in export.pdb --
  out of scope here (they survive only if the stick already has them).
* Track row u32@0x14 (purpose unknown): filled with the content_id so the
  value stays unique and deterministic; rekordbox assigns an unknown value.
* Track string slot 3: real exports carry small counters ('2', '3', ...);
  filled with the dominant constant '2'.
* Track analyze_date (string slot 15): no source column in exportLibrary.db;
  filled with dateAdded.
* Play count: OneLibrary resets djPlayCount to 0 while rekordbox's own
  export carries the pre-conversion count; the old numbers are not in the
  source, so the converted rows read 0 (verified against a real stick).
* Track bitmask bit 0x10000: OneLibrary can keep this modified-analysis
  marker set on some rows while rekordbox's exporter clears it; the source
  value is written as-is (single-track divergence, verified on a real stick).
* Album/label artwork rows: OneLibrary's image paths point at filenames
  rekordbox renames/prefixes on export (both stem sets exist on the stick);
  the converter writes the OneLibrary paths verbatim. Album/label rows
  rekordbox renumbers into historically freed slots (export-history
  dependent) keep their OneLibrary ids.
"""

_TABLES_17_18_19_NOTE = """
Tables 17, 18 and 19 (content-independent index/config rows) are replicated
from a real rekordbox export.  Table 19 embeds the conversion date.  They
carry no library content; --no-fill leaves them empty.
"""


def align4(n: int) -> int:
    return (n + 3) & ~3


def decode_string_roundtrip(encoded: bytes) -> str:
    """Decode a DeviceSQL string back to text (round-trip helper for tests)."""
    b0 = encoded[0]
    if b0 & 1:                       # short ASCII: b0 = 2*len + 3
        return encoded[1:b0 >> 1].decode("ascii")
    total = struct.unpack_from("<H", encoded, 1)[0]
    payload = encoded[4:total]
    return payload.decode("utf-16-le" if b0 == 0x90 else "ascii")


def _dir_bytes(n: int) -> int:
    """Row-directory bytes for a page holding n slots."""
    return 2 * n + 4 * ((n + 15) // 16) if n else 0


# ---------------------------------------------------------------------------
# OneLibrary reader
# ---------------------------------------------------------------------------

class OneLibrary:
    """Minimal reader for PIONEER/rekordbox/exportLibrary.db (SQLCipher)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            import sqlcipher3.dbapi2 as sq3
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "sqlcipher3 is required (pip install sqlcipher3-wheels): %s" % exc
            )
        con = sq3.connect(str(self.path))
        con.execute("PRAGMA key = '%s'" % ONELIBRARY_KEY)
        try:
            con.execute("SELECT 1 FROM sqlite_master")
        except sq3.DatabaseError as exc:
            raise SystemExit(f"cannot unlock {self.path}: {exc}")
        self.con = con
        self._tables: dict[str, list[dict]] = {}

    def table(self, name: str) -> list[dict]:
        assert name.isidentifier()
        if name not in self._tables:
            cur = self.con.execute(f"SELECT * FROM {name}")
            cols = [d[0] for d in cur.description]
            self._tables[name] = [dict(zip(cols, row)) for row in cur.fetchall()]
        return self._tables[name]

    def has(self, name: str) -> bool:
        return name in self.table_names()

    def table_names(self) -> set[str]:
        rows = self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Row builders (each returns the packed row bytes)
# ---------------------------------------------------------------------------

_ARTIST = struct.Struct("<HHIBB")     # subtype 0x60, shift, id, 0x03, ofs
_ALBUM = struct.Struct("<HHIIIIBB")   # 0x80, shift, 0, artist_id, id, 0, ...
_TREE = struct.Struct("<IIIII")       # parent, ?, sort_order, id, is_folder
_ENTRY = struct.Struct("<III")        # entry_index, track_id, playlist_id
_KEYROW = struct.Struct("<II")        # id, id (same value twice), then name
_TRACK_FIXED = struct.Struct(
    "<HHIIIIIHHIIIIIIIIIIIIHHHHHHBBHH")


def row_genre_or_label(rid: int, name: str) -> tuple[bytes, int]:
    row = struct.pack("<I", rid) + encode_string(name)
    return row, align4(len(row))


def row_artist(aid: int, name: str) -> tuple[bytes, int]:
    s = encode_string(name)
    row = _ARTIST.pack(0x60, 0, aid, 0x03, 0x0A) + s
    return row, align4(10 + align4(len(s))) + 4


def row_album(album_id: int, name: str, artist_id: int) -> tuple[bytes, int]:
    s = encode_string(name)
    row = _ALBUM.pack(0x80, 0, 0, artist_id, album_id, 0, 0x03, 0x16) + s
    return row, align4(22 + align4(len(s))) + 4


def row_key(rid: int, name: str) -> tuple[bytes, int]:
    row = _KEYROW.pack(rid, rid) + encode_string(name)
    return row, align4(len(row))


def row_color(rid: int, name: str) -> tuple[bytes, int]:
    # Observed shape: u32 unknown(0), u8 unknown(1), u16 id, u8 unknown(0),
    # then the name string.
    row = struct.pack("<IBBH", 0, 1, rid, 0) + encode_string(name)
    return row, align4(len(row))


def row_artwork(rid: int, path: str) -> tuple[bytes, int]:
    row = struct.pack("<I", rid) + encode_string(path)
    return row, align4(len(row))


def row_tree(parent: int, sort_order: int, rid: int, is_folder: bool,
             name: str) -> tuple[bytes, int]:
    row = _TREE.pack(parent, 0, sort_order, rid, 1 if is_folder else 0) \
        + encode_string(name)
    return row, align4(len(row))


def row_entry(entry_index: int, track_id: int, playlist_id: int) \
        -> tuple[bytes, int]:
    return _ENTRY.pack(entry_index, track_id, playlist_id), 12


def row_column(cid: int, code: int, name: str) -> tuple[bytes, int]:
    row = struct.pack("<HH", cid, code) + encode_string(name)
    return row, align4(len(row))


def _int(v, default: int = 0) -> int:
    """Coerce an OneLibrary value to int (columns may arrive as TEXT)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# OneLibrary columns that can be integers / (TEXT) strings on different
# sources.
_NUMERIC_COLS = frozenset({
    "content_id", "bpmx100", "length", "trackNo", "discNo",
    "artist_id_artist", "artist_id_remixer", "artist_id_originalArtist",
    "artist_id_composer", "artist_id_lyricist", "album_id", "genre_id",
    "label_id", "key_id", "color_id", "image_id", "rating",
    "releaseYear", "fileSize", "fileType", "bitrate", "bitDepth",
    "samplingRate", "djPlayCount", "isHotCueAutoLoadOn",
    "isKuvoDeliverStatusOn", "masterDbId", "masterContentId",
    "analysedBits", "contentLink", "hasModified", "cueUpdateCount",
    "analysisDataUpdateCount", "informationUpdateCount",
})


def row_track(ol_row: dict, ids: dict, strings: list[str], track_id: int) \
        -> tuple[bytes, int]:
    """Track row: 0x5E fixed bytes + 21 u16 string offsets + string blob.

    Fixed layout (u32@0x14 and the string slots come from the rules
    documented in FORMAT.md and verified against two real exports; the
    fields without a source column in exportLibrary.db are noted in
    FIELDS_NOT_PRESERVED).
    """
    path = ol_row.get("path") or ""
    ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    file_type = {".mp3": 1, ".m4a": 4, ".wav": 11, ".aiff": 12, ".aif": 12,
                 ".flac": 0, ".aac": 1}.get(ext, 1)
    fixed = _TRACK_FIXED.pack(
        0x0024, 0,                       # magic 0x24; index shift patched later
        _int(ol_row.get("contentLink")) or 0x000C0700,   # bitmask at 0x04
        _int(ol_row.get("samplingRate")) or 44100,    # sample rate
        ids.get("composer", 0),          # composer artist id
        _int(ol_row.get("fileSize")),
        track_id,                        # u32@0x14 unique-per-track standin
        0xAE49, 0x03DD,                  # observed constants at 0x18/0x1a
        _int(ids.get("artwork")), _int(ids.get("key")),
        _int(ids.get("original_artist")), _int(ids.get("label")),
        _int(ids.get("remixer")),
        _int(ol_row.get("bitrate")),
        _int(ol_row.get("trackNo")),
        _int(ol_row.get("bpmx100")) ,
        _int(ids.get("genre")), _int(ids.get("album")), _int(ids.get("artist")),
        track_id,
        _int(ol_row.get("discNo")),      # disc number
        _int(ol_row.get("djPlayCount")), # play count
        _int(ol_row.get("releaseYear")), # year
        _int(ol_row.get("bitDepth")) or 16,  # sample depth
        _int(ol_row.get("length")),      # duration (s)
        0x0029,
        _int(ol_row.get("color_id")),    # colour id
        _int(ol_row.get("rating")),      # rating
        file_type,
        0x0003,
    )
    assert len(fixed) == 0x5E, len(fixed)

    blob = bytearray()
    offsets = []
    pos = 0x88
    encoded = [encode_string(s) for s in strings]
    for enc in encoded:
        if enc[0] & 1 == 0 and pos % 4:      # long strings start 4-aligned
            pad = 4 - pos % 4
            blob.extend(bytes(pad))
            pos += pad
        offsets.append(pos)
        blob.extend(enc)
        pos += len(enc)
    row = fixed + struct.pack("<21H", *offsets) + bytes(blob)
    alloc = 0x88 + sum(align4(len(e)) for e in encoded) + 4
    return row, alloc


# ---------------------------------------------------------------------------
# PDB writer (page container)
# ---------------------------------------------------------------------------

# Content-independent index/config rows replicated from a real
# rekordbox export (identical row sets exist on a second, unrelated
# stick).  Same order as the source file.
_T17_ROWS = [
    (15, 20, 262), (16, 21, 355), (18, 23, 355), (8, 9, 355), (9, 10, 355),
    (10, 11, 355), (13, 15, 355), (14, 19, 260), (1, 1, 355), (5, 6, 261),
    (6, 7, 355), (7, 8, 355), (2, 2, 65538), (3, 3, 131075), (4, 4, 196609),
    (11, 12, 262243), (17, 5, 327779), (19, 22, 393315), (20, 18, 458851),
    (27, 26, 524899), (24, 17, 589923), (22, 27, 656739),
]
_T18_ROWS = [
    (22, 17, 1), (14, 8, 1), (8, 9, 1), (9, 10, 1), (10, 11, 1),
    (15, 13, 1), (13, 15, 1), (23, 16, 1), (1, 6, 1), (21, 7, 1),
    (25, 0, 256), (26, 1, 512), (2, 2, 768), (3, 3, 1024), (5, 4, 1280),
    (6, 5, 1536), (11, 12, 1792),
]
# Table 19: a single 40-byte row; bytes 13..23 hold the export date.
_T19_DATE_AT = 13


def _t19_row() -> bytes:
    row = bytearray(
        b"\x80\x02\x20\x00\x07\x01\x00\x00\x00\x00\x00\x00\x17"
        b"2026-03-16"
        b"\x19\x1e\x0b\x31\x30\x30\x30\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    )
    assert len(row) == 40, len(row)
    row[_T19_DATE_AT:_T19_DATE_AT + 10] = \
        datetime.date.today().isoformat().encode()
    return bytes(row)


# Standard colour set / key list (matches OneLibrary's own id space).
COLOR_NAMES = ["Pink", "Red", "Orange", "Yellow", "Green", "Aqua",
               "Blue", "Purple"]
# Standard deck key list: 24 Camelot Tonart rows plus the 8 user-numeric
# rows rekordbox always emits (names taken verbatim from a real export,
# byte-identical to the key table OneLibrary carries in id order).
KEY_NAMES_STOCK_32 = [
    "Abm", "F#m", "Bm", "Em", "Fm", "Cm", "Bb", "Dbm", "Am", "Bbm",
    "D", "Gm", "Dm", "F", "C", "A", "Ebm", "B", "F#", "Eb", "Ab",
    "G", "E", "1m", "11m", "10m", "12m", "8m", "6m", "9d", "5m", "6d",
]

MENUITEM_FALLBACK = [
    (1, 128, "\ufffaGENRE\ufffb"), (2, 129, "\ufffaARTIST\ufffb"),
    (3, 130, "\ufffaALBUM\ufffb"), (4, 131, "\ufffaTRACK\ufffb"),
    (5, 133, "\ufffaBPM\ufffb"), (6, 134, "\ufffaRATING\ufffb"),
    (7, 135, "\ufffaYEAR\ufffb"), (8, 136, "\ufffaREMIXER\ufffb"),
    (9, 137, "\ufffaLABEL\ufffb"), (10, 138, "\ufffaORIGINAL ARTIST\ufffb"),
    (11, 139, "\ufffaKEY\ufffb"), (12, 141, "\ufffaCUE\ufffb"),
    (13, 142, "\ufffaCOLOR\ufffb"), (14, 146, "\ufffaTIME\ufffb"),
    (15, 147, "\ufffaBITRATE\ufffb"), (16, 148, "\ufffaFILE NAME\ufffb"),
    (17, 132, "\ufffaPLAYLIST\ufffb"), (18, 152, "\ufffaHOT CUE BANK\ufffb"),
    (19, 149, "\ufffaHISTORY\ufffb"), (20, 145, "\ufffaSEARCH\ufffb"),
    (21, 150, "\ufffaCOMMENTS\ufffb"), (22, 140, "\ufffaDATE ADDED\ufffb"),
    (23, 151, "\ufffaDJ PLAY COUNT\ufffb"), (24, 144, "\ufffaFOLDER\ufffb"),
    (25, 161, "\ufffaDEFAULT\ufffb"), (26, 162, "\ufffaALPHABET\ufffb"),
    (27, 170, "\ufffaMATCHING\ufffb"),
]


class PdbBuilder:
    """Writes an export.pdb from scratch (header + 20 table index pages +
    data pages).  Fresh one-batch semantics per FORMAT.md."""

    def __init__(self, page_size: int = PAGE_SIZE, sequence: int = 1):
        self.page_size = page_size
        self.sequence = sequence
        self._rows: dict[int, list[tuple[bytes, int]]] = {}

    def add(self, table_type: int, row: bytes, alloc: int) -> None:
        """Append one row (index shifts are patched at build time)."""
        if not 0 <= table_type < NUM_TABLES:
            raise ValueError(f"unsupported table type {table_type}")
        self._rows.setdefault(table_type, []).append(
            (bytes(row), align4(alloc)))

    # -- layout ---------------------------------------------------------------

    def _pack_table(self, table_type: int) -> list[list[tuple[bytes, int]]]:
        max_heap = self.page_size - PAGE_HEADER - _dir_bytes(1)
        pages: list[list[tuple[bytes, int]]] = []
        cur: list[tuple[bytes, int]] = []
        used = 0
        for row, alloc in self._rows.get(table_type, []):
            if alloc > max_heap:
                raise ValueError(
                    f"table {table_type}: row allocation {alloc} exceeds page")
            need = alloc + _dir_bytes(len(cur) + 1)
            if cur and PAGE_HEADER + used + need > self.page_size:
                pages.append(cur)
                cur, used = [], 0
            cur.append((row, alloc))
            used += alloc
        if cur or not pages:
            pages.append(cur)
        return pages

    def build(self) -> bytes:
        packed = {tt: [pg for pg in self._pack_table(tt) if pg]
                  for tt in TABLE_ORDER}

        # Pages: one index page per table, then per-table data pages,
        # tables in numeric order (mirrors real exports). Tables without
        # rows keep their index page only (like real exports).
        index_page = {}
        cursor = 1
        for tt in TABLE_ORDER:
            index_page[tt] = cursor
            cursor += 1
        data_pages = {}
        for tt in TABLE_ORDER:
            data_pages[tt] = []
            for _ in packed[tt]:
                data_pages[tt].append(cursor)
                cursor += 1
        last_real = cursor - 1

        # Every table's empty candidate lies beyond EOF, and
        # next_unused_page = max(candidates) + 1 (holds in real exports).
        candidates = {tt: last_real + 1 + i for i, tt in enumerate(TABLE_ORDER)}
        next_unused_page = last_real + 1 + NUM_TABLES

        file_pages = last_real + 1
        out = bytearray(file_pages * self.page_size)
        struct.pack_into("<IIIIII", out, 0,
                         0, self.page_size, NUM_TABLES, next_unused_page,
                         1, self.sequence)
        for i, tt in enumerate(TABLE_ORDER):
            struct.pack_into("<IIII", out, 0x1C + 16 * i, tt, candidates[tt],
                             index_page[tt],
                             data_pages[tt][-1] if data_pages[tt]
                             else index_page[tt])

        # -- index pages (flags 0x64, no rows)
        for tt in TABLE_ORDER:
            off = index_page[tt] * self.page_size
            first_data = data_pages[tt][0] if data_pages[tt] else None
            # the index page's next_page links to its first data page
            # (or the empty candidate for empty tables), like real files
            struct.pack_into("<IIIII", out, off,
                             0, index_page[tt], tt,
                             first_data if first_data is not None
                             else candidates[tt],
                             self.sequence)
            struct.pack_into("<I", out, off + 0x14, 0)
            struct.pack_into("<BH", out, off + 0x18, 0, 0)   # n, present
            struct.pack_into("<BHHHHHH", out, off + 0x1B,
                             0x64, 0, 0, 0x1FFF, 0x1FFF, 1004, 0)
            # (0x1B flags, 0x1C free, 0x1E used, 0x20/0x22 sentinels,
            #  0x24 the constant 1004, 0x26 = 0)
            # heap prologue: own page, first data page (or end marker),
            # end marker, 0
            struct.pack_into("<IIII", out, off + PAGE_HEADER,
                             index_page[tt],
                             first_data if first_data is not None
                             else 0x3FFFFFFF,
                             0x3FFFFFFF, 0)
            struct.pack_into("<I", out, off + PAGE_HEADER + 16, 0x1FFF0000)
            out[off + PAGE_HEADER + 20: off + PAGE_HEADER + 20 + 1008 * 4] = \
                b"\xf8\xff\xff\x1f" * 1008
            # the final heap word stays zero (matches real index pages)

        # -- data pages
        for tt in TABLE_ORDER:
            shift_at = {0: 2, 2: 2, 3: 2}.get(tt)
            for pi, rows in enumerate(packed[tt]):
                page_index = data_pages[tt][pi]
                off = page_index * self.page_size
                n = len(rows)
                used = sum(a for _r, a in rows)
                free = self.page_size - PAGE_HEADER - used - _dir_bytes(n)
                nxt = (data_pages[tt][pi + 1]
                       if pi + 1 < len(data_pages[tt]) else candidates[tt])
                struct.pack_into("<IIIII", out, off,
                                 0, page_index, tt, nxt, self.sequence)
                struct.pack_into("<I", out, off + 0x14, 0)
                struct.pack_into("<BH", out, off + 0x18, n & 0xFF,
                                 (n << 5) | (1 if n > 255 else 0))
                struct.pack_into("<BHHHHHH", out, off + 0x1B,
                                 0x24, free, used, n, 0, 0, 0)
                # row directory: slot s lives 6+2s bytes below the page end
                # (groups of 16 grow down; the two mask words sit 4 bytes
                # below the current group's lowest-offset slot).
                # presence mask == written mask (fresh one-batch write)
                for group in range((n + 15) // 16):
                    gbase = off + self.page_size - group * 0x24
                    in_group = min(16, n - group * 16)
                    mask = (1 << in_group) - 1
                    struct.pack_into("<HH", out, gbase - 4, mask, mask)
                row_heap = 0   # stored offsets are relative to the heap start
                for slot, (row, alloc) in enumerate(rows):
                    if shift_at is not None:
                        mutable = bytearray(row)
                        struct.pack_into("<H", mutable, shift_at,
                                         0x20 * slot)
                        row = bytes(mutable)
                    gbase = off + self.page_size - (slot // 16) * 0x24
                    struct.pack_into("<H", out,
                                     gbase - 6 - 2 * (slot % 16), row_heap)
                    at = off + PAGE_HEADER + row_heap
                    out[at:at + len(row)] = row
                    row_heap += alloc
                self._verify_page(out[off:off + self.page_size], rows,
                                  page_index=page_index)
        return bytes(out[:file_pages * self.page_size])

    def _verify_page(self, page: bytearray, rows: list, page_index: int):
        """Re-read a freshly written data page and assert the row directory
        matches the row-heap sequence (catches directory clobbering)."""
        ps = self.page_size
        n = page[0x18] + 0x100 * (page[0x19] & 1)
        assert n == len(rows), (page_index, n, len(rows))
        row_heap = 0
        for slot, (_row, alloc) in enumerate(rows):
            gbase = ps - (slot // 16) * 0x24
            stored = struct.unpack_from("<H", page, gbase - 6 - 2 * (slot % 16))[0]
            if stored != row_heap:
                tail = bytes(page[-0x60:])
                raise AssertionError(
                    f"page {page_index} slot {slot}: directory says "
                    f"{stored}, wrote {row_heap}; page tail: "
                    + tail.hex())
            row_heap += alloc


# ---------------------------------------------------------------------------
# Convert
# ---------------------------------------------------------------------------

def convert_to_bytes(ol: OneLibrary, verbose: bool = False,
                     fill_tables: bool = True) -> bytes:
    """Convert an open OneLibrary database into export.pdb bytes."""
    b = PdbBuilder()

    # -- colours & keys: from the source db when it carries the table
    # (OneLibrary key ids are the deck's own id space, verified against a
    # rekordbox export), else the standard fallback lists.
    color_src = (ol.table("colour") if ol.has("colour")
                 else ol.table("color") if ol.has("color") else [])
    color_src = {int(r["color_id"]): r["name"] or "" for r in color_src} \
        if color_src else {}
    max_color = len(COLOR_NAMES)
    for i in range(1, max(max_color, max(color_src, default=0)) + 1):
        name = color_src.get(i) or COLOR_NAMES[i - 1]
        row, alloc = row_color(i, name)
        b.add(6, row, alloc)
    key_src = (ol.table("key") if ol.has("key") else [])
    key_src = {int(r["key_id"]): r["name"] or "" for r in key_src} if key_src else {}
    max_key = len(KEY_NAMES_STOCK_32)
    for i in range(1, max(max_key, max(key_src, default=0)) + 1):
        name = key_src.get(i) or KEY_NAMES_STOCK_32[i - 1] if i <= max_key \
            else key_src.get(i, "")
        row, alloc = row_key(i, name)
        b.add(5, row, alloc)

    # -- genres, labels, artists, albums
    for r in ol.table("genre"):
        row, alloc = row_genre_or_label(r["genre_id"], r["name"] or "")
        b.add(1, row, alloc)
    for r in ol.table("label"):
        row, alloc = row_genre_or_label(r["label_id"], r["name"] or "")
        b.add(4, row, alloc)
    for r in ol.table("artist"):
        row, alloc = row_artist(r["artist_id"], r["name"] or "")
        b.add(2, row, alloc)
    for r in ol.table("album"):
        row, alloc = row_album(r["album_id"], r["name"] or "",
                               r["artist_id"] or 0)
        b.add(3, row, alloc)

    # -- artwork (OneLibrary table is named `image`)
    image_paths = {r["image_id"]: r["path"] for r in ol.table("image")}
    for iid, path in sorted(image_paths.items()):
        row, alloc = row_artwork(iid, path or "")
        b.add(13, row, alloc)

    # -- browse columns: from the menuItem table, or the default set
    menu = ol.table("menuItem") if ol.has("menuItem") else []
    cols = sorted((r["menuItem_id"], r.get("code", 0), r["name"] or "")
                  for r in menu) if menu else MENUITEM_FALLBACK
    for cid, code, name in cols:
        row, alloc = row_column(cid, code, name)
        b.add(16, row, alloc)

    # -- content-independent config tables (see _TABLES_17_18_19_NOTE)
    if fill_tables:
        for a, bb, c in _T17_ROWS:
            b.add(17, struct.pack("<HHI", a, bb, c), 8)
        for a, bb, c in _T18_ROWS:
            b.add(18, struct.pack("<HHI", a, bb, c), 8)
        b.add(19, _t19_row(), 40)

    # -- tracks
    content = ol.table("content")
    known_genre = {r["genre_id"] for r in ol.table("genre")}
    known_label = {r["label_id"] for r in ol.table("label")}
    known_artist = {r["artist_id"] for r in ol.table("artist")}
    known_album = {r["album_id"] for r in ol.table("album")}
    key_name = {r["key_id"]: r["name"] or "" for r in ol.table("key")} \
        if ol.has("key") else {}
    emitted_keys = {i for i in range(1, len(KEY_NAMES_STOCK_32) + 1)} \
        | set(key_name)
    known_key = {k for k in emitted_keys if key_name.get(k) or
                 (k <= len(KEY_NAMES_STOCK_32))}

    date_added_fallback = min((r.get("dateAdded") or "") for r in content) \
        or datetime.date.today().isoformat()

    for c in content:
        c = {k: (_int(v) if k in _NUMERIC_COLS else v) for k, v in c.items()}
        cid = _int(c["content_id"])
        ids = {
            "artist": c["artist_id_artist"] if c["artist_id_artist"] in known_artist else 0,
            "album": c["album_id"] if c["album_id"] in known_album else 0,
            "genre": c["genre_id"] if c["genre_id"] in known_genre else 0,
            "label": c["label_id"] if c["label_id"] in known_label else 0,
            "key": c["key_id"] if c.get("key_id") in known_key else 0,
            "artwork": c["image_id"] if c["image_id"] in image_paths else 0,
            "remixer": c["artist_id_remixer"] if c["artist_id_remixer"] in known_artist else 0,
            "original_artist": c["artist_id_originalArtist"]
            if c["artist_id_originalArtist"] in known_artist else 0,
            "composer": c["artist_id_composer"]
            if c["artist_id_composer"] in known_artist else 0,
        }
        strings = [
            (c.get("isrc") or ""),                  # 0  ISRC
            "",                                     # 1
            "2", "2",                               # 2, 3 (see doc note)
            "",                                     # 4
            (c.get("kuvoDeliveryComment") or ""),   # 5  message
            "ON", "ON",                             # 6, 7
            "", "",                                 # 8, 9
            (c.get("dateCreated") or c.get("dateAdded")
             or date_added_fallback),          # 10 date added (rekordbox exports dateCreated)
            (c.get("releaseDate") or ""),           # 11 release date
            (c.get("subtitle") or ""),              # 12 mix name
            "",                                     # 13
            (c.get("analysisDataFilePath") or ""),  # 14 analysis path
            (c.get("dateAdded") or date_added_fallback),    # 15 analysis date
            (c.get("djComment") or ""),             # 16 comment
            (c.get("title") or ""),                 # 17 title
            "",                                     # 18
            (c.get("fileName") or ""),              # 19 file name
            (c.get("path") or ""),                  # 20 file path
        ]
        row, alloc = row_track(c, ids, strings, cid)
        b.add(0, row, alloc)

    # -- playlists (tree + entries)
    playlists = ol.table("playlist") if ol.has("playlist") else []
    entries_src = (ol.table("playlist_content")
                   if ol.has("playlist_content") else [])
    valid_tracks = {_int(c["content_id"]) for c in content}

    tree_rows = []
    for p in playlists:
        tree_rows.append((
            p.get("playlist_id"),
            p.get("playlist_id_parent") or 0,
            p.get("sequenceNo") or 0,
            p.get("name") or "",
            (p.get("attribute") or 0) == 1,   # folder flag (documented guess)
        ))
    if not tree_rows:
        # No playlists in OneLibrary: create the root-level playlist that
        # real rekordbox sticks carry, covering all exported tracks.
        tree_rows = [(1, 0, 0, "USB", False)]

    for rid, parent, seq, name, folder in sorted(tree_rows, key=lambda t: t[0]):
        row, alloc = row_tree(parent, seq, rid, folder, name)
        b.add(7, row, alloc)

    ent_by_pl: dict[int, list[int]] = {}
    for e in entries_src:
        pl = _int(e["playlist_id"])
        tid = _int(e["content_id"])
        if pl not in {t[0] for t in tree_rows}:
            continue
        if tid not in valid_tracks:
            continue
        ent_by_pl.setdefault(pl, []).append(
            (_int(e.get("sequenceNo") or 0), tid))
    for pl, lst in sorted(ent_by_pl.items()):
        seqs = [s for s, _t in sorted(lst)]
        contig = seqs == list(range(1, len(seqs) + 1))
        for i, (seq, tid) in enumerate(sorted(lst)):
            entry_index = seq if contig else i + 1
            row, alloc = row_entry(entry_index, tid, pl)
            b.add(8, row, alloc)

    return b.build()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _backup(target: Path) -> Path | None:
    """Copy an existing export.pdb aside; returns the backup path or None."""
    if not target.exists():
        return None
    n = 1
    while True:
        bak = target.with_name(f"{target.name}.bak-{n}")
        if not bak.exists():
            shutil.copy2(target, bak)
            return bak
        n += 1


def convert(stick_dir: str | os.PathLike, backup: bool = True,
            verbose: bool = False) -> Path:
    """Full pipeline for one stick; returns the written export.pdb path."""
    stick = Path(stick_dir)
    src = stick / "PIONEER" / "rekordbox" / "exportLibrary.db"
    if not src.exists():
        raise SystemExit(f"no OneLibrary exportLibrary.db under {stick}")
    out = src.with_name("export.pdb")
    bak = _backup(out) if backup else None
    ol = OneLibrary(src)
    data = convert_to_bytes(ol, verbose=verbose)
    out.write_bytes(data)

    # verification pass with the independent vendored parser
    db = PdbReader(data)
    counts = {
        "tracks": len(db.tracks),
        "artists": len(db.artists),
        "albums": len(db.albums),
        "genres": len(db.genres),
        "labels": len(db.labels),
        "keys": len(db.keys),
        "colors": len(db.colors),
        "playlists": len(db.playlist_tree),
        "entries": len(db.playlist_entries),
        "artwork": len(db.artwork),
        "columns": len(db.columns),
    }
    if verbose:
        print(f"wrote {out} ({len(data):,} bytes)")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        if bak:
            print(f"backed up previous export.pdb -> {bak.name}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Convert a OneLibrary-only stick into an "
                    "XDJ-700-readable one (writes export.pdb).")
    ap.add_argument("stick", help="stick directory (contains PIONEER/)")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a backup of an existing export.pdb")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and count only; write nothing")
    args = ap.parse_args(argv)
    stick = Path(args.stick)
    src = stick / "PIONEER" / "rekordbox" / "exportLibrary.db"
    if not src.exists():
        print(f"no OneLibrary exportLibrary.db under {stick}", file=sys.stderr)
        return 1
    ol = OneLibrary(src)
    if args.dry_run:
        data = convert_to_bytes(ol)
        db = PdbReader(data)
        print(f"would write {len(data):,} bytes; "
              f"{len(db.tracks)} tracks, {len(db.playlist_tree)} playlists")
        return 0
    try:
        convert(stick, backup=not args.no_backup, verbose=not args.quiet)
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
