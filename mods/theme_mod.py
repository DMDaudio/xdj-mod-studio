"""fwtool build hook. Theme chosen by the XDJ_THEME environment variable."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from themes import apply_to_block

NAME = os.environ.get('XDJ_THEME', 'purple')


def apply(buf, hdr):
    if hdr != 0x40000:
        return buf
    out, cnt = apply_to_block(buf, NAME)
    print(f"      theme {NAME}: {cnt:,} pixels recoloured")
    return out
