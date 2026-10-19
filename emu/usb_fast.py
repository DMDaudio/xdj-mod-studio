"""USB fast path: coalesced multi-sector transfers + batched MMIO reads.

Gated by env XDJ_USB_FAST=1 (default off). When off, every helper returns
None / falls through so the old byte-wise transport runs bit-identically.
When on, helpers try bulk moves that are byte-identical by construction;
on ANY precondition mismatch or exception they return None / raise to let
the caller fall back to the slow path. No protocol semantics change: the
firmware sees the same bytes, same CSW/status, same final TE/DEI/BRDY
state -- just fewer emulated transactions per multi-sector run.

What is coalesced:
  * disk reads: ImageDisk single pread; FolderDisk contiguous file runs
    in one host read (overlay hits fall back to the slow per-sector loop).
  * DMA data phases: bulk-IN (stick.tx -> RAM) and bulk-OUT (RAM ->
    stick.rx) moved as whole runs per pump call instead of 64 B packets.
  * MMIO reads: single dev.read(a,n) instead of N byte reads (writes stay
    byte-wise: DMAC CHCR/USB FIFO triggers are not trivially safe).

New code lives here; hooks in usbhost/usbdisk/sh7724/fastcpu are 2-3 lines.
Never touches machine.py block ops or ports.
"""
import os

stats = {
    "disk_fast_calls": 0,
    "disk_fast_bytes": 0,
    "disk_slow_fallback": 0,
    "dma_fast_moves": 0,
    "dma_fast_bytes": 0,
    "dma_slow_fallback": 0,
    "io_fast_reads": 0,
    "io_fast_bytes": 0,
}


def reset_stats():
    for k in stats:
        stats[k] = 0


def is_fast():
    return os.environ.get("XDJ_USB_FAST") == "1"


def _verify():
    return os.environ.get("XDJ_USB_FAST_VERIFY") == "1"


# -- disk reads ---------------------------------------------------------------
def coalesced_read(disk, lba, cnt):
    """Return cnt sectors starting at lba, identical bytes to _Overlay.read.

    Returns bytes on success; raises on any precondition miss so the caller
    falls back to the slow per-sector loop. Overlay hits (deck writes) fall
    back -- writes are rare/small, correctness first.
    """
    from emu.usbdisk import SECTOR
    # Any overlay hit in range -> slow path (keeps write-visibility exact).
    written = getattr(disk, "written", None)
    if written:
        for i in range(cnt):
            if (lba + i) in written:
                stats["disk_slow_fallback"] += 1
                raise _Fallback()
    # ImageDisk: one contiguous pread.
    if type(disk).__name__ == "ImageDisk":
        try:
            disk.f.seek(lba * SECTOR)
            data = disk.f.read(cnt * SECTOR)
            if len(data) < cnt * SECTOR:
                data = data.ljust(cnt * SECTOR, b"\0")
            stats["disk_fast_calls"] += 1
            stats["disk_fast_bytes"] += len(data)
            if _verify():
                slow = _slow_read(disk, lba, cnt)
                if slow != data:
                    stats["disk_slow_fallback"] += 1
                    raise _Fallback("verify mismatch")
            return data
        except _Fallback:
            raise
        except Exception:
            stats["disk_slow_fallback"] += 1
            raise _Fallback()
    # FolderDisk: group contiguous file-backed sectors into single reads.
    if type(disk).__name__ == "FolderDisk":
        try:
            return _folder_read_fast(disk, lba, cnt)
        except _Fallback:
            raise
        except Exception:
            stats["disk_slow_fallback"] += 1
            raise _Fallback()
    stats["disk_slow_fallback"] += 1
    raise _Fallback()


class _Fallback(Exception):
    pass


def _slow_read(disk, lba, cnt):
    out = bytearray()
    for i in range(cnt):
        s = disk.written.get(lba + i)
        out += s if s is not None else disk._read1(lba + i)
    return bytes(out)


def _folder_read_fast(disk, lba, cnt):
    """Batch contiguous file runs; synthesize non-file sectors as before.

    Logs disk.access entries per sector in order (same as _read1) so the
    access-log track-ID path is unaffected.
    """
    from emu.usbdisk import SECTOR, PART_LBA, RESERVED
    # Fast precondition: resolve each sector's source without I/O.
    # Reuse the same mapping as _read1/_cluster by peeking extents.
    # To guarantee identical bytes, we read each run with one file pread
    # but verify length; any surprise -> fallback.
    out = bytearray()
    i = 0
    # Local refs for speed.
    written = disk.written
    # Collect runs: list of (kind, payload) where kind=file-run or single.
    # We walk sectors, grouping file sectors that are contiguous in the
    # same host file at contiguous offsets.
    import bisect
    runs = []  # (start_idx, n, item, file_pos) or ("single", idx)
    idx = 0
    while idx < cnt:
        cur = lba + idx
        # Overlay already excluded above.
        # Mirror _read1 mapping to find the source.
        if cur < PART_LBA:
            runs.append(("single", idx))
            idx += 1
            continue
        s = cur - PART_LBA
        if s < RESERVED or s < disk.data_lba:
            # MBR/boot/fsinfo/reserved/FAT: single synthesized sectors.
            runs.append(("single", idx))
            idx += 1
            continue
        c, off = divmod(s - disk.data_lba, disk.spc)
        clus = c + 2
        j = bisect.bisect_right(disk._starts, clus) - 1
        if j < 0:
            runs.append(("single", idx))
            idx += 1
            continue
        c0, ncl, it = disk.extents[j]
        if clus >= c0 + ncl:
            runs.append(("single", idx))
            idx += 1
            continue
        if it["kind"] != "file":
            runs.append(("single", idx))
            idx += 1
            continue
        # File run: extend while same file + contiguous pos.
        pos0 = (clus - c0) * disk.clus + off * SECTOR
        # off is sector offset within cluster (0..spc-1); pos must advance
        # by SECTOR per sector and stay within the file's cluster extent.
        # Also must stay within the same cluster run (no extent hop).
        run_n = 1
        while idx + run_n < cnt:
            nxt = lba + idx + run_n
            ns = nxt - PART_LBA
            nc, noff = divmod(ns - disk.data_lba, disk.spc)
            nclus = nc + 2
            # Must be same extent and contiguous file offset.
            if nclus < c0 or nclus >= c0 + ncl:
                break
            npos = (nclus - c0) * disk.clus + noff * SECTOR
            if npos != pos0 + run_n * SECTOR:
                break
            # Also must not cross into overlay (already excluded globally,
            # but double-check per sector for safety).
            if (nxt) in written:
                break
            run_n += 1
            # Cap runs to avoid huge allocs (64 KB is plenty).
            if run_n * SECTOR >= 65536:
                break
        runs.append(("file", idx, run_n, it, pos0))
        idx += run_n
    # Execute runs.
    for r in runs:
        if r[0] == "single":
            out += disk._read1(lba + r[1])
        else:
            _, start, nsec, it, pos0 = r
            # Single host read for the whole run.
            f = disk._fh.get(it["path"])
            if f is None:
                if len(disk._fh) > 32:
                    for h in disk._fh.values():
                        try:
                            h.close()
                        except Exception:
                            pass
                    disk._fh.clear()
                f = disk._fh[it["path"]] = open(it["path"], "rb")
            f.seek(pos0)
            chunk = f.read(nsec * SECTOR)
            if len(chunk) < nsec * SECTOR:
                chunk = chunk.ljust(nsec * SECTOR, b"\0")
            # Monotonic op count (the access ring logs stay identical).
            disk.ops += nsec
            # Access log per sector (same order/content as _read1).
            for k in range(nsec):
                if len(disk.access) > 21000:
                    del disk.access[:1000]
                disk.access.append((it["path"], pos0 + k * SECTOR))
            out += chunk
    data = bytes(out)
    stats["disk_fast_calls"] += 1
    stats["disk_fast_bytes"] += len(data)
    if _verify():
        slow = _slow_read(disk, lba, cnt)
        if slow != data:
            stats["disk_slow_fallback"] += 1
            raise _Fallback("verify mismatch")
    return data


# -- DMA data-phase coalescing -------------------------------------------------
def fast_pump(dev, usb):
    """Bulk-move eligible DMA channels. Leaves the rest armed for slow.

    Returns number of channels fast-completed. Never raises: on any error
    the channel is left armed for the slow per-packet path.
    """
    if not is_fast():
        return 0
    done = 0
    try:
        for base, ch in list(getattr(usb, "dma_armed", ())):
            try:
                if _fast_one(dev, usb, base, ch):
                    done += 1
            except Exception:
                stats["dma_slow_fallback"] += 1
                continue
    except Exception:
        pass
    return done


def _fast_one(dev, usb, base, ch):
    """Try one channel in bulk. True = completed via fast, False = leave."""
    from emu.usbhost import USBHost
    st = dev.dma[base]["ch"][ch]
    chcr = st["chcr"]
    if not chcr & 1 or chcr & 2:
        return False
    ts4 = (((chcr >> 20) & 3) << 2) | ((chcr >> 3) & 3)
    unit = {0: 1, 1: 2, 2: 4, 3: 16, 4: 32, 7: 8, 0xB: 16, 0xC: 32}.get(ts4)
    if unit is None:
        return False
    sm, dm = (chcr >> 12) & 3, (chcr >> 14) & 3
    src, dst = st["sar"] & 0x1FFFFFFF, st["dar"] & 0x1FFFFFFF
    n = (st["tcr"] & 0xFFFFFF) * unit
    if n <= 0 or n > (1 << 24):
        return False
    stick = getattr(usb, "stick", None)
    if stick is None or not getattr(usb, "attached", False):
        return False
    mem = dev.mem
    # IN: USB FIFO -> RAM (device sends data).
    if USBHost.owns(src):
        # Only bulk-IN data phases with a decent run; CSW (13 B) + CBW
        # handling + PIO odd sizes stay on the slow path.
        if dm != 1:
            return False
        # If a packet is already staged in the FIFO buffer, let the slow
        # path consume it first (else we would reorder buf ahead of tx).
        try:
            _sel, _pipe = usb._dma_pipe(src)
            if _pipe is None:
                return False
            if usb.valid.get(_pipe, False) or usb.off.get(_pipe, 0):
                return False
            # Also require the staging buffer itself to be empty.
            _b = usb.buf.get(_pipe, b"")
            if len(_b) - usb.off.get(_pipe, 0) > 0:
                return False
        except Exception:
            return False
        tx = getattr(stick, "tx", None)
        if not tx or len(tx) < 512 or n < 512:
            return False
        # Precondition: DMA length must not exceed buffered data (else the
        # transfer spans data+CSW phases -> slow path handles the tail).
        if n > len(tx):
            return False
        want = n
        # TRE/TRN packet counter: the firmware counts 64 B packets (e.g.
        # TRN=32 for a 2048 B read). The slow path increments trcnt once
        # per packet in _receive; a bulk move must advance it by the same
        # number of packets or the firmware waits forever (mount breaks).
        try:
            _mx = usb._mxps(_pipe) if _pipe is not None else 64
            _packets = (want + _mx - 1) // _mx if _mx else 0
            _t = usb.tre.get(_pipe) if _pipe is not None else None
            if _t and (_t[0] & 0x0200):
                _trn = _t[1]
                _cnt = usb.trcnt.get(_pipe, 0)
                if _cnt >= _trn:
                    return False  # counter done: slow would NAK
                if _cnt + _packets > _trn:
                    return False  # spans beyond this transfer: slow
        except Exception:
            return False
        data = bytes(tx[:want])
        # Write first; only mutate stick state on success (fallback safe).
        ok = mem.w_block(dst, data)
        if not ok:
            # w_block False = unmapped part: let slow do fault-and-stop.
            return False
        del tx[:want]
        # Advance the packet counter exactly like N slow _receive calls.
        try:
            if _t and (_t[0] & 0x0200):
                usb.trcnt[_pipe] = usb.trcnt.get(_pipe, 0) + _packets
        except Exception:
            pass
        st["dar"] = (st["dar"] + want) & 0xFFFFFFFF
        left = n - want
        st["tcr"] = -(-left // unit) if left > 0 else 0
        # Mirror slow-path flags: data was available (BRDY) on this pipe.
        try:
            sel, pipe = usb._dma_pipe(src)
            if pipe is not None:
                usb._set(usbhost_BRDYSTS(), 1 << pipe)
        except Exception:
            pass
        stats["dma_fast_moves"] += 1
        stats["dma_fast_bytes"] += want
        if st["tcr"] & 0xFFFFFF == 0:
            usb.dma_armed.discard((base, ch))
            st["chcr"] |= 0x02
            if st["chcr"] & 0x04:
                dev.raise_irq(dev._dei(ch))
        return True
    # OUT: RAM -> USB FIFO (host sends data, e.g. WRITE10 payload).
    # DISABLED by default (returns False): a single 4064 B OUT move broke
    # mount/browse (short-packet/BVAL + TCR/rx_need mismatch hunt needed).
    # Track loads are bulk-IN (reads); writes stay on the slow path.
    # Opt-in via XDJ_USB_FAST_OUT=1 for debugging only.
    if USBHost.owns(dst):
        if os.environ.get("XDJ_USB_FAST_OUT") != "1":
            return False
        if sm != 1:
            return False
        # Staged OUT packet pending -> let slow _send it first (order).
        try:
            _sel2, _pipe2 = usb._dma_pipe(dst)
            if _pipe2 is None:
                return False
            if usb.valid.get(_pipe2, False) or usb.off.get(_pipe2, 0):
                return False
            _b2 = usb.buf.get(_pipe2, b"")
            if len(_b2) - usb.off.get(_pipe2, 0) > 0:
                return False
        except Exception:
            return False
        rx_need = getattr(stick, "rx_need", 0)
        if not rx_need or rx_need < 512 or n < 512:
            return False
        if n > rx_need:
            # DMA longer than the WRITE payload (spans CBW tail?) -> slow.
            return False
        want = n
        blk = mem.r_block(src, want)
        if blk is None:
            return False
        data = bytes(blk)
        # Mutate stick state exactly like bulk_out would for this chunk.
        stick.rx += data
        stick.rx_need -= len(data)
        if stick.rx_need <= 0:
            lba = stick.rx_cmd
            try:
                stick.disk.write(lba, bytes(stick.rx))
            except Exception as e:
                stick.log.append(("disk-exc", "write", lba,
                                  len(stick.rx), repr(e)[:160]))
                stick.sense = (4, 0x44, 0)
                stick._status(1, stick.dlen)
                stick.rx = bytearray()
            else:
                stick.rx = bytearray()
                stick._status(0)
        st["sar"] = (st["sar"] + want) & 0xFFFFFFFF
        left = n - want
        st["tcr"] = -(-left // unit) if left > 0 else 0
        try:
            sel, pipe = usb._dma_pipe(dst)
            if pipe is not None:
                usb._set(usbhost_BEMPSTS(), 1 << pipe)
                usb._set(usbhost_BRDYSTS(), 1 << pipe)
        except Exception:
            pass
        stats["dma_fast_moves"] += 1
        stats["dma_fast_bytes"] += want
        if st["tcr"] & 0xFFFFFF == 0:
            usb.dma_armed.discard((base, ch))
            st["chcr"] |= 0x02
            if st["chcr"] & 0x04:
                dev.raise_irq(dev._dei(ch))
        return True
    return False


def usbhost_BRDYSTS():
    return 0x46


def usbhost_BEMPSTS():
    return 0x4A


# -- word-sized MMIO reads ------------------------------------------------------
def fast_io_read(dev, addr, n):
    """Single dev.read(a,n) when trivially safe, else None -> slow loop.

    Safe: USB + generic stub + pure-status registers. SCIF data/TMU/DMA
    side-effect reads stay on the slow byte path (caller decides by addr,
    but this helper is conservative: it only batches when the whole range
    is USB or generic stub; everything else returns None).
    """
    if not is_fast() or n <= 1:
        return None
    try:
        a = addr & 0xFFFFFFFF
        phys = a & 0x1FFFFFFF
        # USB0 window: bulk FIFO + control regs (reads are pop/pure).
        if 0x04D80000 <= phys < 0x04D80200:
            v = dev.read(a, n)
            stats["io_fast_reads"] += 1
            stats["io_fast_bytes"] += n
            return v & ((1 << (n * 8)) - 1)
        # Generic stub range check: batch only if NONE of the bytes hit a
        # special device (SCIF/TMU/CMT/DMAC/CS6/2DG/IIC/LCDC/INTC).
        if _is_generic(dev, a, n):
            v = dev.read(a, n)
            stats["io_fast_reads"] += 1
            stats["io_fast_bytes"] += n
            return v & ((1 << (n * 8)) - 1)
    except Exception:
        pass
    return None


def _is_generic(dev, a, n):
    phys = a & 0x1FFFFFFF
    # Special windows (must stay byte-wise):
    specials = (
        (0x04680000, 0x04690000),  # 2DG
        (0x04D80000, 0x04D80200),  # USB (handled above, keep here too)
        (0x04470000, 0x04470020),  # IIC
        (0xFFE00000, 0xFFE20028),  # SCIF0/1/2
        (0xFFD80000, 0xFFD80030),  # TMU0
        (0xFFD90000, 0xFFD90030),  # TMU1
        (0xA44A0000, 0xA44A0070),  # CMT
        (0xFE008000, 0xFE008200),  # DMAC0
        (0xFDC08000, 0xFDC08200),  # DMAC1
        (0xFE009000, 0xFE009200),  # DMAC2?
        (0x18000000, 0x18100000),  # CS6
        (0xFE940000, 0xFE960000),  # LCDC
        (0xA4080080, 0xA40800F4),  # INTC
        (0x04080080, 0x040800F4),  # INTC alias
    )
    for i in range(n):
        p = (a + i) & 0x1FFFFFFF
        va = (a + i) & 0xFFFFFFFF
        # P2 INTC range also special.
        if 0xA4080080 <= va < 0xA40800F4:
            return False
        for lo, hi in specials:
            if lo <= p < hi:
                return False
    return True


# -- word-sized MMIO writes (generic stub only) ---------------------------------
# DMAC CHCR/USB FIFO/SCIF etc. trigger on partial writes, so writes stay
# byte-wise everywhere except the generic stub (plain byte storage, no
# side effects). Returns True if handled (single dev.write), False/None
# to let the caller do the slow per-byte loop.
def fast_io_write(dev, addr, val, n):
    if not is_fast() or n <= 1:
        return None
    try:
        a = addr & 0xFFFFFFFF
        if _is_generic(dev, a, n):
            dev.write(a, n, val & ((1 << (n * 8)) - 1))
            return True
    except Exception:
        pass
    return None
