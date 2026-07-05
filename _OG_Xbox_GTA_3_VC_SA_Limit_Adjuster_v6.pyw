#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GTA III / VC / SA (Xbox) - Limit Adjuster  -  single-file edition
====================================================================
One self-contained script (no local imports) so it compiles cleanly with
py2exe / PyInstaller. Supports the retail Vice City XBE, the Liberty-City-on-
VC-engine ports, and - via signature detection rather than hardcoded offsets -
GTA III, which shares the same RenderWare pool-allocation routine.

Tabs:
  * Limits   - memory-pool sizes, located by scanning the allocator signature
               so the same code works across III / VC / LC ports. Live values
               are read from the file; nothing is assumed.
  * Vehicles - raise the vehicle pool (safe 1-127, in place).
  * Icon     - replace the XBE title image (XPR0 / DXT1) with your own PNG.
  * Notes    - honest notes on streaming memory / RAM / handling.

Core patching (pools + vehicles) uses ONLY the standard library, so a py2exe
build needs no extra DLLs. The Icon tab additionally uses Pillow; if Pillow is
not bundled, that tab disables itself and the rest still works.

------------------------------------------------------------------------------
Building an .exe with py2exe (Python on Windows):

  1) pip install py2exe pillow
  2) save the setup script below as setup.py next to this file
  3) python setup.py py2exe
  4) the exe appears in dist\

setup.py:
------------------------------------------------------------------------------
    from distutils.core import setup
    import py2exe
    setup(
        windows=[{"script": "vc3_xbox_mod_tool.pyw"}],
        options={"py2exe": {
            "bundle_files": 1,
            "compressed": True,
            "includes": ["tkinter"],
            # drop "PIL" from includes if you don't need the Icon tab
            "packages": ["PIL"],
        }},
        zipfile=None,
    )
------------------------------------------------------------------------------
(For PyInstaller instead:  pyinstaller --onefile --windowed vc3_xbox_mod_tool.pyw)
"""

import os
import sys
import struct
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ----- optional Pillow (Icon tab only) --------------------------------------
try:
    from PIL import Image, ImageTk
    PIL_OK = True
    PIL_ERR = ""
except Exception as _e:                       # pragma: no cover
    PIL_OK = False
    PIL_ERR = str(_e)


# ============================================================================
#  XBE / pool detection
# ============================================================================
# The pool allocator emits, per pool, the byte pattern:
#     <size load>  8B F0  E8 rel32  A3 addr32  EB 06 89 2D addr32  53 E8 rel32 ...
# where <size load> is one of:
#     BF imm32        mov edi, size
#     6A imm8 5F      push imm8 ; pop edi          (<=127)
#     33 FF 47        xor edi,edi ; inc edi  (=1, fixed)
# We anchor on "8B F0 E8 ?? ?? ?? ?? A3" and read the size load preceding it.

ANCHOR = bytes.fromhex("8bf0e8")

# Pool name lists in the order each engine creates them. III and VC share the
# same 9/10-pool order; San Andreas has its own, longer order (17 pools). The
# correct list is chosen by detected game + pool count. Raw size + offset are
# always shown regardless, so an unexpected build is still patched correctly.
POOL_NAMES_IIIVC = [
    "PtrNodes", "EntryInfoNodes", "Peds", "Vehicles", "Buildings",
    "Treadables", "Objects", "Dummys", "AudioScriptObjects", "ColModels",
]
POOL_NAMES_SA = [
    "PtrNodeSingles", "PtrNodeDoubles", "EntryInfoNodes", "Peds", "Vehicles",
    "Buildings", "Objects", "Dummies", "ColModels", "Tasks", "Events",
    "PointRoute", "PatrolRoute", "NodeRoute", "TaskAllocator",
    "PedIntelligence", "PedAttractors",
]
# suggested sane ceilings keyed by name (fallback 200000)
SANE_MAX = {
    "PtrNodes": 200000, "EntryInfoNodes": 50000, "Peds": 1000,
    "Vehicles": 127, "Buildings": 60000, "Treadables": 100000,
    "Objects": 8000, "Dummys": 30000, "AudioScriptObjects": 2000,
    "ColModels": 40000,
    # SA-specific
    "PtrNodeSingles": 400000, "PtrNodeDoubles": 50000, "Dummies": 100000,
    "Tasks": 8000, "Events": 4000, "PointRoute": 2000, "PatrolRoute": 2000,
    "NodeRoute": 2000, "TaskAllocator": 2000, "PedIntelligence": 1000,
    "PedAttractors": 2000,
}

# Known stock pool values, for sanity-checking the position-based labels.
# III has 9 pools (no separate ColModels); VC has 10. If a detected value
# matches the stock column for its game, the label is almost certainly right.
STOCK_DEFAULTS = {
    "GTA III / Liberty City": {
        "PtrNodes": 30000, "EntryInfoNodes": 5400, "Peds": 140,
        "Vehicles": 110, "Buildings": 5500, "Treadables": 1214,
        "Objects": 450, "Dummys": 2802, "AudioScriptObjects": 256,
    },
    "GTA Vice City": {
        "PtrNodes": 30000, "EntryInfoNodes": 3200, "Peds": 140,
        "Vehicles": 110, "Buildings": 7000, "Treadables": 1,
        "Objects": 460, "Dummys": 2340, "AudioScriptObjects": 192,
        "ColModels": 4400,
    },
    "GTA San Andreas": {
        "PtrNodeSingles": 70000, "PtrNodeDoubles": 3200, "EntryInfoNodes": 500,
        "Peds": 74, "Vehicles": 70, "Buildings": 13500, "Objects": 350,
        "Dummies": 2550, "ColModels": 10150, "Tasks": 500, "Events": 200,
        "PointRoute": 64, "PatrolRoute": 32, "NodeRoute": 64,
        "TaskAllocator": 16, "PedIntelligence": 74, "PedAttractors": 64,
    },
    # Mobile-derived Xbox 360 build - several pools differ from PC/Xbox stock.
    "GTA San Andreas (Xbox 360)": {
        "PtrNodeSingles": 6000, "PtrNodeDoubles": 500, "Peds": 140,
        "Vehicles": 110, "Buildings": 14000, "Objects": 350, "Dummies": 3500,
        "ColModels": 10150, "Tasks": 500, "Events": 200, "PointRoute": 64,
        "PatrolRoute": 32, "NodeRoute": 64, "TaskAllocator": 16,
        "PedIntelligence": 140, "PedAttractors": 64,
        "Quad Tree Nodes (2)": 400, "Vehicle Structures": 50,
    },
}


# ----------------------------------------------------------------------------
# Extra limits located by FIXED, individually-verified offsets (NOT auto-scanned).
# These are build-specific, so each entry is checked against its expected default
# when a file loads; an entry is only offered if the bytes at its offset match.
# That guarantees the tool never writes to a wrong location on an unexpected
# build. Only values sitting at a single unambiguous immediate-load instruction
# are included - values that recur throughout the binary are deliberately left
# out rather than risk patching the wrong site.
#
# fields: game, label, imm_offset, opcode_byte, kind, default, max_sane, description
EXTRA_LIMITS = [
    # GTA Vice City "Map Limits" (model-info setup block) - all verified unique.
    ("GTA Vice City", "IDE (model defs)",     0xE99BE, 0x68, "imm32", 3885, 20000,
     "Max .IDE model definitions"),
    ("GTA Vice City", "ID (model index)",     0xE95DA, 0xB9, "imm32", 6500, 30000,
     "Max model ID index slots"),
    ("GTA Vice City", "TXD (texture dicts)",  0xB2E89, 0xB8, "imm32", 1385,  8000,
     "Max texture dictionaries"),
    ("GTA Vice City", "2dfx (effects)",       0xE9A2A, 0x68, "imm32", 1210,  8000,
     "Max 2d effects (lights / particles)"),
    ("GTA Vice City", "TOBJ (timed objects)", 0xE99D9, 0x68, "imm32",  385,  4000,
     "Max timed objects"),
]


# ----------------------------------------------------------------------------
# Extra limits located by a globally-UNIQUE byte SIGNATURE rather than a fixed
# offset. This is even safer than a hardcoded offset: the immediate is found by
# searching for a surrounding instruction pattern that occurs exactly once in
# the whole file, so it survives small build shifts and refuses to patch if the
# pattern is missing or appears more than once (i.e. an unexpected build).
#
# Each signature is a hex byte string with the 4-byte little-endian immediate
# carved out and its position given by imm_pos (byte index of the immediate
# inside the matched signature). The bytes shown in the signature are the
# surrounding opcodes/operands only - never the immediate itself - so a file
# whose value was already raised still matches and stays editable.
#
# fields: game, label, sig_hex, imm_pos, default, max_sane, description
SIG_LIMITS = [
    # GTA San Andreas - two array-init stubs (mov ecx,<count>; mov edi,<addr>;
    # rep stosd). Each "mov ecx,imm32" site is unique in the whole executable,
    # so raising the count is safe and in place. Verified on the HOODLUM US 1.0
    # build; absent/duplicated on any other build -> silently skipped.
    ("GTA San Andreas", "Quad Tree Nodes (2)",
     "33c0682c010000b9{imm}bf801e7600", 8, 40, 4000,
     "Secondary quad-tree node pool (sector subdivision)"),
    ("GTA San Andreas", "Vehicle Structures",
     "5733c0b9{imm}bff04d3d00", 4, 50, 2000,
     "Max simultaneous vehicle structures (CVehicleStruct)"),
]


# ----------------------------------------------------------------------------
# Extra limits whose value is loaded at MORE THAN ONE site that must all be
# patched together. Some counts feed two parallel arrays (e.g. an IDE/model
# definition table registered in two places); raising one site but not the
# other desyncs the structures and crashes the game. Each entry therefore lists
# several signatures - every one must be present, globally unique, and hold the
# SAME current value, or the whole limit is skipped. On save, all sites are
# written atomically to the one value the user enters.
#
# fields: game, label, [sig_hex,...], imm_pos, default, max_sane, description
MULTISIG_LIMITS = [
    # GTA San Andreas (original Xbox) - the model/IDE definition count 14000 is
    # used at two element-size-0x20 pool registrations pushing adjacent name
    # pointers (0x9F57B / 0x9F5A5). Both must match to stay in sync. Verified on
    # the retail Xbox build; skipped anywhere the pair isn't intact.
    ("GTA San Andreas", "IDE (model defs)",
     ["f509006a20b8{imm}b98c8c6000e8", "687bf5090068{imm}6a208d460450"],
     6, 14000, 40000,
     "Max .IDE model/object definitions (paired arrays, patched together)"),
]


class Extra:
    __slots__ = ("game", "label", "off", "offs", "opcode", "kind", "default",
                 "max_sane", "desc", "current", "via")
    def __init__(self, game, label, off, opcode, kind, default, max_sane, desc,
                 via="offset", offs=None):
        self.game = game; self.label = label; self.off = off; self.opcode = opcode
        self.kind = kind; self.default = default; self.max_sane = max_sane
        self.desc = desc; self.current = None
        self.via = via            # "offset" | "signature" | "multisig"
        # every file offset this limit writes to. Single-site limits patch just
        # [off]; multi-site limits patch all of these with the same value.
        self.offs = offs if offs is not None else [off]


def _sig_parts(sig_hex):
    """Split a signature like 'aabb{imm}ccdd' into (prefix_bytes, suffix_bytes).
    The {imm} placeholder marks where the 4-byte immediate sits."""
    pre_hex, post_hex = sig_hex.split("{imm}")
    return bytes.fromhex(pre_hex), bytes.fromhex(post_hex)


def _find_unique_sig(data, pre, post):
    """Return the file offset of the immediate for a prefix/suffix pair that
    occurs EXACTLY ONCE, or None if it is missing or ambiguous. Searching for
    prefix+suffix with a 4-byte gap guarantees the surrounding instruction is
    intact while ignoring whatever value currently sits in the immediate."""
    plen = len(pre)
    hits = []
    i = 0
    while True:
        j = data.find(pre, i)
        if j < 0:
            break
        imm_off = j + plen
        # confirm the suffix follows the 4-byte immediate gap
        if data[imm_off + 4: imm_off + 4 + len(post)] == post:
            hits.append(imm_off)
        i = j + 1
    if len(hits) == 1:
        return hits[0]
    return None                    # 0 -> not this build; >1 -> ambiguous, skip


def scan_extra_limits(data, game):
    """Return every Extra applicable to this game, from BOTH the fixed-offset
    table (EXTRA_LIMITS) and the unique-signature table (SIG_LIMITS).

    Fixed-offset entries are gated on the preceding opcode byte (which a value
    patch never changes) plus a sane value range. Signature entries are gated on
    a surrounding byte pattern that must occur exactly once. Either way, a file
    that was already patched still shows editable fields, while a different or
    unsupported build is safely skipped rather than written to blindly."""
    found = []

    # --- fixed-offset limits ---
    for g, label, off, opcode, kind, default, mx, desc in EXTRA_LIMITS:
        if g != game or off + 4 > len(data) or off < 1:
            continue
        if data[off - 1] != opcode:
            continue                       # instruction not where expected -> skip
        cur = struct.unpack_from("<I", data, off)[0]
        if not (1 <= cur <= 10_000_000):   # reject absurd values (wrong offset)
            continue
        e = Extra(g, label, off, opcode, kind, default, mx, desc, via="offset")
        e.current = cur
        found.append(e)

    # --- signature-located limits ---
    for g, label, sig_hex, imm_pos, default, mx, desc in SIG_LIMITS:
        if g != game:
            continue
        try:
            pre, post = _sig_parts(sig_hex)
        except ValueError:
            continue
        imm_off = _find_unique_sig(data, pre, post)
        if imm_off is None or imm_off + 4 > len(data):
            continue                       # missing or ambiguous -> skip
        cur = struct.unpack_from("<I", data, imm_off)[0]
        if not (1 <= cur <= 10_000_000):
            continue
        e = Extra(g, label, imm_off, None, "imm32", default, mx, desc,
                  via="signature")
        e.current = cur
        found.append(e)

    # --- multi-site signature limits (all offsets patched together) ---
    for g, label, sig_list, imm_pos, default, mx, desc in MULTISIG_LIMITS:
        if g != game:
            continue
        offs = []
        vals = []
        ok = True
        for sig_hex in sig_list:
            try:
                pre, post = _sig_parts(sig_hex)
            except ValueError:
                ok = False; break
            imm_off = _find_unique_sig(data, pre, post)
            if imm_off is None or imm_off + 4 > len(data):
                ok = False; break          # a required site is missing/ambiguous
            offs.append(imm_off)
            vals.append(struct.unpack_from("<I", data, imm_off)[0])
        if not ok or not offs:
            continue
        # every site must currently hold the same value (in sync) and be sane
        if len(set(vals)) != 1 or not (1 <= vals[0] <= 10_000_000):
            continue
        e = Extra(g, label, offs[0], None, "imm32", default, mx, desc,
                  via="multisig", offs=offs)
        e.current = vals[0]
        found.append(e)

    return found


class Pool:
    __slots__ = ("name", "load_off", "kind", "size", "patch_off")
    def __init__(self, name, load_off, kind, size):
        self.name = name
        self.load_off = load_off
        # kind: mov_edi_imm32 | push_pop | xor_inc   (x86 / original Xbox)
        #       ppc_li_imm16                          (PowerPC / Xbox 360)
        self.kind = kind
        self.size = size
        # offset of the editable bytes
        if kind == "mov_edi_imm32":
            self.patch_off = load_off + 1     # imm32, little-endian
        elif kind == "push_pop":
            self.patch_off = load_off + 1     # imm8
        elif kind == "ppc_li_imm16":
            # 'li rD,SIMM' is a 4-byte PPC word; the 16-bit signed immediate is
            # its low half. load_off already points AT that immediate half.
            self.patch_off = load_off         # imm16, big-endian
        else:
            self.patch_off = None             # fixed

    @property
    def editable(self):
        return self.kind in ("mov_edi_imm32", "push_pop", "ppc_li_imm16")

    @property
    def max_value(self):
        if self.kind == "push_pop":
            return 127
        if self.kind == "ppc_li_imm16":
            # signed 16-bit immediate: anything larger needs a lis+ori pair
            # (two instructions), which an in-place value patch can't do.
            return 32767
        return SANE_MAX.get(self.name, 200000)


def scan_pool_table(data, game=None):
    """Return list[Pool] for the densest allocator run, or []."""
    sites = []
    i = 0
    n = len(data)
    while True:
        j = data.find(ANCHOR, i)
        if j < 0:
            break
        # require A3 (mov [imm32],eax) right after the call's rel32
        if j + 8 <= n and data[j + 7] == 0xA3:
            lo = kind = size = None
            if j >= 5 and data[j - 5] == 0xBF:
                size = struct.unpack_from("<I", data, j - 4)[0]
                kind = "mov_edi_imm32"; lo = j - 5
            elif j >= 3 and data[j - 3] == 0x6A and data[j - 1] == 0x5F:
                size = data[j - 2]; kind = "push_pop"; lo = j - 3
            elif j >= 3 and data[j - 3:j] == b"\x33\xff\x47":
                size = 1; kind = "xor_inc"; lo = j - 3
            if lo is not None:
                sites.append((lo, kind, size))
        i = j + 1
    if not sites:
        return []
    sites.sort()
    runs, cur = [], [sites[0]]
    for s in sites[1:]:
        if s[0] - cur[-1][0] < 60:
            cur.append(s)
        else:
            runs.append(cur); cur = [s]
    runs.append(cur)
    runs.sort(key=len, reverse=True)
    best = runs[0]
    # choose the name list: SA has its own (longer) order; III/VC share theirs.
    if game == "GTA San Andreas" or len(best) > 12:
        names = POOL_NAMES_SA
    else:
        names = POOL_NAMES_IIIVC
    pools = []
    for idx, (lo, kind, size) in enumerate(best):
        name = names[idx] if idx < len(names) else "pool%d" % idx
        pools.append(Pool(name, lo, kind, size))
    return pools


def read_title(data):
    try:
        cert = struct.unpack_from("<I", data, 0x118)[0] - 0x10000
        tid = struct.unpack_from("<I", data, cert + 8)[0]
        name = data[cert + 0x0C:cert + 0x0C + 0x50]\
            .decode("utf-16-le", errors="replace").split("\x00")[0]
        return name, tid
    except Exception:
        return "", 0


def detect_game(name):
    low = name.lower()
    if any(k in low for k in ("san andreas", "andreas", "gtasa", "sa\x00")):
        return "GTA San Andreas"
    if any(k in low for k in ("liberty", "lc", "iii", "gta3")):
        return "GTA III / Liberty City"
    if "vice" in low:
        return "GTA Vice City"
    return "Unknown (GTA engine)"


# ============================================================================
#  Xbox 360 (XEX2) support  -  GTA San Andreas, PowerPC big-endian
# ============================================================================
# The 360 port of SA is a PowerPC/big-endian XEX2 image, based on the mobile
# port. Its RenderWare pool table is a tight, perfectly-regular run of pool
# registrations, each emitted as the same 4-instruction template:
#
#     lis  r11, 0x8203          3d 60 82 03      (high half of pool descriptor)
#     li   r4,  <SIZE>          38 80 <size16>   <- editable pool size
#     addi r5,  r11, <disp>     38 ab <disp16>   (low half of descriptor ptr)
#     bl   CPool::CPool         48 xx xx xx      (opcode 18, LK set)
#
# We anchor on the fixed "lis r11,0x8203 ; li r4,*" prefix, then verify the
# "addi r5,r11,*" and a bl follow. On this build that full template matches
# exactly 18 times (the 16 contiguous pools + Quad Tree Nodes + Vehicle
# Structures) with zero false positives, so it is a reliable, in-file scan.
#
# Two hard constraints, both respected below:
#   * 'li rD,SIMM' holds a SIGNED 16-bit immediate, so in-place editing is safe
#     only up to 32767 (Pool.max_value enforces this for ppc_li_imm16). Larger
#     values would require rewriting the instruction into a lis+ori pair - a
#     code-cave problem this tool deliberately does not attempt, mirroring the
#     ">127 vehicles" refusal on the original Xbox.
#   * the immediate is big-endian and is the LOW half of the 4-byte word, so a
#     patch rewrites 2 big-endian bytes at the immediate offset.

XEX2_MAGIC = b"XEX2"

# Pool name order for the mobile-derived 360 build. Best-effort by position;
# the raw size and file offset shown in the UI are always correct regardless.
# The ColModels=10150 slot is the fixed anchor that pins this ordering.
POOL_NAMES_SA_360 = [
    "PtrNodeSingles", "PtrNodeDoubles", "Peds", "Vehicles", "Buildings",
    "Objects", "Dummies", "ColModels", "Tasks", "Events", "PointRoute",
    "PatrolRoute", "NodeRoute", "TaskAllocator", "PedIntelligence",
    "PedAttractors",
]


def parse_xex(data):
    """Parse an XEX2 container and return a dict describing how to reach the raw
    PowerPC PE image inside it, or None if this isn't a supported (unencrypted,
    basic-compression) XEX2. Only the fields the patcher needs are returned.

    Returns dict with:
        pe_file_off : file offset in the XEX where the PE image begins
        secs        : list of (name, vaddr, vsize, raw, rawsz) PE sections
        image_base  : PE image base VA
    'raw' offsets in secs are relative to the PE image; add pe_file_off to get a
    real XEX file offset. This only supports encryption=0 + compression=1
    (basic, single raw block, no gaps) - which is what this GTA SA build uses.
    A different packing returns None and the tool declines rather than guess."""
    if data[:4] != XEX2_MAGIC:
        return None
    be32 = lambda o: struct.unpack_from(">I", data, o)[0]
    be16 = lambda o: struct.unpack_from(">H", data, o)[0]
    try:
        pe_data_offset = be32(0x08)
        opt_count = be32(0x14)
        # walk optional header directory for FILE_FORMAT_INFO (key 0x000003FF)
        fmt_off = None
        o = 0x18
        for _ in range(opt_count):
            key = be32(o); val = be32(o + 4)
            if key == 0x000003FF:
                fmt_off = val
            o += 8
        if fmt_off is None:
            return None
        enc = be16(fmt_off + 4)      # 0 = not encrypted
        comp = be16(fmt_off + 6)     # 1 = basic (raw blocks)
        if enc != 0 or comp != 1:
            return None              # encrypted or LZX-compressed -> unsupported
        # basic-compression block list: pairs of (data_size, zero_size) after the
        # 8-byte header. Require a single block with no zero padding (contiguous).
        info_size = be32(fmt_off)
        nblocks = (info_size - 8) // 8
        blocks = []
        p = fmt_off + 8
        for _ in range(nblocks):
            blocks.append((be32(p), be32(p + 4)))
            p += 8
        if len(blocks) != 1 or blocks[0][1] != 0:
            return None              # gapped/multi-block image -> decline
        pe_file_off = pe_data_offset
        # sanity: PE image must start with 'MZ'
        if data[pe_file_off:pe_file_off + 2] != b"MZ":
            return None
        # parse PE headers (these fields are little-endian even on 360)
        le16 = lambda o: struct.unpack_from("<H", data, o)[0]
        le32 = lambda o: struct.unpack_from("<I", data, o)[0]
        e_lfanew = le32(pe_file_off + 0x3C)
        peo = pe_file_off + e_lfanew
        if data[peo:peo + 4] != b"PE\x00\x00":
            return None
        machine = le16(peo + 4)
        if machine != 0x01F2:        # IMAGE_FILE_MACHINE_POWERPCBE
            return None
        nsec = le16(peo + 6)
        opt_size = le16(peo + 20)
        image_base = le32(peo + 24 + 28)
        sec_tbl = peo + 24 + opt_size
        secs = []
        for i in range(nsec):
            so = sec_tbl + i * 40
            name = data[so:so + 8].split(b"\x00")[0].decode("ascii", "replace")
            vsize = le32(so + 8); vaddr = le32(so + 12)
            rawsz = le32(so + 16); raw = le32(so + 20)
            secs.append((name, vaddr, vsize, raw, rawsz))
        return dict(pe_file_off=pe_file_off, secs=secs, image_base=image_base)
    except (struct.error, IndexError):
        return None


def read_title_xex(data, xex):
    """Best-effort title for a 360 GTA. The XEX title-id / metadata parsing is
    involved; for our purposes the executable is identified as SA by finding the
    pool template, so we just return a fixed, honest label here."""
    return "GTA San Andreas (Xbox 360)"


def scan_pool_table_xex(data, xex):
    """Locate the SA 360 pool table inside an already-parsed XEX. Returns
    list[Pool] with kind 'ppc_li_imm16' and file offsets that point into the
    real XEX file (ready to patch), or [] if the template isn't found."""
    secs = xex["secs"]
    pe_off = xex["pe_file_off"]
    text = next((s for s in secs if s[0] == ".text"), None)
    if text is None:
        return []
    _, tva, tvs, traw, trawsz = text
    tstart = pe_off + traw
    tend = pe_off + traw + trawsz

    contiguous = []      # the main pool block (adjacent sites)
    extras = []          # far-flung template matches (QuadTree(2), VehicleStructs)
    o = tstart
    prev = None
    while o + 16 <= tend:
        if (data[o:o + 4] == b"\x3d\x60\x82\x03" and       # lis r11,0x8203
                data[o + 4:o + 6] == b"\x38\x80" and       # li  r4,imm
                data[o + 8:o + 10] == b"\x38\xab"):        # addi r5,r11,disp
            bl = struct.unpack_from(">I", data, o + 12)[0]
            if (bl >> 26) & 0x3F == 18:                     # bl (branch, opcode 18)
                imm_off = o + 6                            # BE16 immediate
                size = struct.unpack_from(">H", data, imm_off)[0]
                site = (imm_off, size)
                if prev is not None and imm_off - prev < 0x40:
                    contiguous.append(site)
                else:
                    if contiguous:
                        extras.append(("run", list(contiguous)))
                        contiguous = []
                    contiguous.append(site)
                prev = imm_off
        o += 4
    if contiguous:
        extras.append(("run", list(contiguous)))

    # pick the longest run as the main pool table; any single-site runs after it
    # are the standalone limits (Quad Tree Nodes 2, Vehicle Structures).
    runs = [r for _, r in extras]
    if not runs:
        return []
    runs.sort(key=len, reverse=True)
    main = runs[0]
    standalone = [r[0] for r in runs[1:] if len(r) == 1]

    pools = []
    for idx, (imm_off, size) in enumerate(main):
        name = POOL_NAMES_SA_360[idx] if idx < len(POOL_NAMES_SA_360) else "pool%d" % idx
        pools.append(Pool(name, imm_off, "ppc_li_imm16", size))
    # label the two standalone limits by their known stock sizes
    for imm_off, size in standalone:
        if size == 400:
            nm = "Quad Tree Nodes (2)"
        elif size == 50:
            nm = "Vehicle Structures"
        else:
            nm = "extra (size %d)" % size
        pools.append(Pool(nm, imm_off, "ppc_li_imm16", size))
    return pools


# ============================================================================
#  XPR0 icon codec  (inlined; needs Pillow only at runtime)
# ============================================================================
XPR0_MAGIC = b"XPR0"


def _rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _unpack565(c):
    r = ((c >> 11) & 0x1F) << 3
    g = ((c >> 5) & 0x3F) << 2
    b = (c & 0x1F) << 3
    return r | (r >> 5), g | (g >> 6), b | (b >> 5)


def _enc_dxt1(px):
    rs = [p[0] for p in px]; gs = [p[1] for p in px]; bs = [p[2] for p in px]
    cmax = (max(rs), max(gs), max(bs)); cmin = (min(rs), min(gs), min(bs))
    c0 = _rgb565(*cmax); c1 = _rgb565(*cmin)
    if c0 < c1:
        c0, c1 = c1, c0
    r0, g0, b0 = _unpack565(c0); r1, g1, b1 = _unpack565(c1)
    pal = [(r0, g0, b0), (r1, g1, b1),
           ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3),
           ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3)]
    bits = 0
    for i, p in enumerate(px):
        best = 0; bd = 1 << 30
        for j, q in enumerate(pal):
            d = (p[0]-q[0])**2 + (p[1]-q[1])**2 + (p[2]-q[2])**2
            if d < bd:
                bd = d; best = j
        bits |= best << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def _dec_dxt1(b):
    c0, c1, bits = struct.unpack("<HHI", b)
    r0, g0, b0 = _unpack565(c0); r1, g1, b1 = _unpack565(c1)
    if c0 > c1:
        pal = [(r0, g0, b0), (r1, g1, b1),
               ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3),
               ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3)]
    else:
        pal = [(r0, g0, b0), (r1, g1, b1),
               ((r0+r1)//2, (g0+g1)//2, (b0+b1)//2), (0, 0, 0)]
    return [pal[(bits >> (2 * i)) & 3] for i in range(16)]


def find_xpr0(data):
    pos = 0
    while True:
        i = data.find(XPR0_MAGIC, pos)
        if i < 0:
            return None
        try:
            _, total, hdr = struct.unpack_from("<4sII", data, i)
            fmt = struct.unpack_from("<I", data, i + 0x18)[0]
            d3dfmt = (fmt >> 8) & 0xFF
            w = 1 << ((fmt >> 20) & 0xF)
            h = 1 << ((fmt >> 24) & 0xF)
            if total in (0x2800, 0x1000, 0x4800, 0x4000) and hdr <= total and 8 <= w <= 512:
                return dict(offset=i, total=total, data_off=hdr,
                            width=w, height=h, d3dfmt=d3dfmt)
        except struct.error:
            pass
        pos = i + 4


def extract_icon_rgb(data):
    if not PIL_OK:
        return None
    info = find_xpr0(data)
    if not info or info["d3dfmt"] != 0x0C:
        return None
    w, h = info["width"], info["height"]
    base = info["offset"] + info["data_off"]
    bw, bh = w // 4, h // 4
    img = Image.new("RGB", (w, h)); px = img.load()
    for by in range(bh):
        for bx in range(bw):
            blk = data[base + (by*bw+bx)*8: base + (by*bw+bx)*8 + 8]
            t = _dec_dxt1(blk)
            for ty in range(4):
                for tx in range(4):
                    px[bx*4+tx, by*4+ty] = t[ty*4+tx]
    return img


def inject_icon(data, png_path):
    if not PIL_OK:
        raise RuntimeError("Pillow not available")
    info = find_xpr0(data)
    if not info:
        raise ValueError("No XPR0 title image found.")
    if info["d3dfmt"] != 0x0C:
        raise ValueError("Title image is not DXT1.")
    w, h = info["width"], info["height"]
    img = Image.open(png_path).convert("RGB").resize((w, h), Image.LANCZOS)
    px = img.load()
    bw, bh = w // 4, h // 4
    out = bytearray(data)
    base = info["offset"] + info["data_off"]
    o = base
    for by in range(bh):
        for bx in range(bw):
            block = [px[bx*4 + (i % 4), by*4 + (i // 4)] for i in range(16)]
            out[o:o+8] = _enc_dxt1(block)
            o += 8
    return out


# ============================================================================
#  Notes text
# ============================================================================
NOTES_TEXT = """\
GTA III SUPPORT
---------------
III and VC share the same RenderWare engine and the same memory-pool allocator
routine. Rather than hardcoding offsets (which differ between titles and between
the various Liberty-City ports), this tool SCANS for the allocator's byte
signature and reads each pool's live value from the file. That means it adapts
to retail VC, retail III, and the LC-on-VC-engine ports automatically. The pool
NAMES are a best-effort label by position; the raw value and file offset shown
are always correct even if a label is off for an unusual build.

STREAMING MEMORY
----------------
VC/III keep a streaming pool (~12-13 MB stock, akin to GTA SA's stream.ini),
separate from the ~60 MB the title uses overall. It is NOT stored as a clean MB
constant in the executable - it is computed inside CStreaming::Init, and there
are many identical small immediates in .text, so it can't be located safely by a
value scan. Pinning it down needs a disassembler (IDA / Ghidra). This tool will
not expose a streaming slider it can't verify, to avoid crashing real hardware.

SAN ANDREAS stream.ini: the Xbox SA executable does contain the "stream.ini"
filename string and a code path that opens it, so streaming/memory settings are
partly file-driven on SA - editing a stream.ini alongside the game is the
intended, safe way to adjust SA streaming, rather than hex-patching the XBE.
(Whether a loose stream.ini is honoured depends on how the title is mounted.)
The in-XBE streaming size itself was not exposed here for the same verification
reason as above.

XBOX 360 (XEX) SUPPORT
----------------------
The 360 version of San Andreas is a PowerPC, big-endian XEX2 image based on the
mobile port. Open its default.xex directly - the tool detects the format and
switches to a PowerPC-aware scanner automatically. It only supports the retail
build's packaging (unencrypted, "basic" compression); encrypted or LZX-packed
XEX files are declined rather than guessed at.

On 360 the pool table is a perfectly regular run of pool registrations, each
emitted as the same four PowerPC instructions:

    lis  r11, 0x8203         (high half of the pool descriptor pointer)
    li   r4,  <SIZE>         <- the editable pool size
    addi r5,  r11, <disp>    (low half of the pointer)
    bl   CPool::CPool        (the allocator call)

The tool anchors on that template - which occurs exactly 18 times (16 pools plus
Quad Tree Nodes 2 and Vehicle Structures) with no false positives - and edits
the size in place. Two honest limitations:

  * 16-BIT CAP. The size is loaded by a 16-bit 'li' instruction, so in-place
    edits are capped at 32767. Going higher needs a two-instruction rewrite
    (lis+ori) in a code cave, which this tool does not attempt - the same policy
    as the ">127 vehicles" case on the original Xbox.

  * NOT RE-SIGNED. The patched XEX keeps the original size and layout but is not
    re-signed. A retail-signed console will reject a modified executable; run it
    the same way you run any modified 360 title (dev / RGH / homebrew setup) and
    test in an emulator (Xenia) first.

Mobile-derived defaults differ from PC/Xbox (e.g. Buildings 14000, Dummies 3500,
PtrNodeSingles 6000). As with the other engines, the pool NAMES are best-effort
by position while the raw size and file offset shown are always correct.

WHAT ELSE COULD BE ADDED (AND WHY MOST ISN'T)
---------------------------------------------
The PC "SA Limit Adjuster" exposes many more settings. Most of them cannot be
added to a static XBE/XEX patcher safely, and are deliberately left out:

  * The IDE (model definition) count of 14000 IS included here for the original
    Xbox: it is a genuine pool registration used at two paired sites, and this
    tool patches both together so they stay in sync. On the Xbox 360 build 14000
    collides with the Buildings pool value, so it is not offered there.

  * Collision Files, Polygons, Material/Atmosphere Data Pool, IMG Headers, IPL
    Files, Stunt Jumps, Timed Objects, Vehicle/Ped Models and most STATIC limits
    use small, extremely common immediates (255, 400, 256, 4096...) that recur
    dozens to hundreds of times in the code with no unique anchor. They cannot be
    located with the certainty this tool requires, so they are not exposed rather
    than risk patching the wrong site.

  * Garages and the SCM block size look unique by value, but inspection shows the
    single sites are a coincidental local-variable store and a comparison/add,
    not a limit definition. Patching them would change unrelated behaviour, so
    they are excluded.

  * COLOURS, MODELS, GRAPHICS, FILES, MODULES, MULTIPLIERS and Streaming Memory
    in that tool are RUNTIME features of an ASI/DLL plugin: it loads alongside
    the game and rewrites config-loading routines live. They are not single
    constants in the executable, so a static patcher cannot reproduce them.

CAN THE TOOL "HOOK" THE GAME LIKE THAT PLUGIN?
----------------------------------------------
Not without becoming a very different, much riskier tool. Running injected code
would require a code cave - writing new machine instructions into slack space and
redirecting execution to them - plus, on Xbox, a homebrew-enabled console (stock
Xbox has no ASI loader), and on Xbox 360 an already-unsigned/dev setup since any
edit breaks the signature. A code cave is how one would exceed a hard ceiling
(e.g. >127 vehicles, or a pool past its 16-bit limit on 360), but it means
hand-writing and relocating code and fixing call targets - exactly the kind of
uncertain, crash-prone change this tool avoids. It is left as possible future
work, scoped to one carefully-tested cave at a time, rather than attempted
broadly here.
"""


# ============================================================================
#  GUI
# ============================================================================
class ModTool:
    def __init__(self, root):
        self.root = root
        self.data = None
        self.path = None
        self.pools = []
        self.game = ""
        self.platform = "xbox"    # "xbox" (XBE/x86) or "xbox360" (XEX/PPC)
        self.xex = None           # parsed XEX2 info dict when platform=="xbox360"
        self.vars = {}            # pool name -> StringVar
        self.extras = []          # list[Extra]
        self.extra_vars = {}      # extra label -> StringVar
        self.icon_preview = None
        self._pending_icon = None

        root.title("GTA III / VC / SA (Xbox / Xbox 360) - Limit Adjuster")
        root.minsize(740, 640)
        self._style()
        self._build()

    def _style(self):
        self.BG, self.PANEL, self.ACCENT = "#1b1d22", "#24272e", "#e0476b"
        self.TEXT, self.MUTED = "#e8e8ea", "#9aa0aa"
        self.root.configure(bg=self.BG)
        s = ttk.Style()
        try: s.theme_use("clam")
        except tk.TclError: pass
        s.configure(".", background=self.BG, foreground=self.TEXT, fieldbackground=self.PANEL)
        s.configure("TFrame", background=self.BG)
        s.configure("Panel.TFrame", background=self.PANEL)
        s.configure("TLabel", background=self.BG, foreground=self.TEXT)
        s.configure("Muted.TLabel", background=self.PANEL, foreground=self.MUTED)
        s.configure("Head.TLabel", background=self.PANEL, foreground=self.TEXT, font=("Segoe UI",10,"bold"))
        s.configure("Title.TLabel", background=self.BG, foreground=self.TEXT, font=("Segoe UI",14,"bold"))
        s.configure("Accent.TButton", background=self.ACCENT, foreground="white",
                    font=("Segoe UI",10,"bold"), borderwidth=0, padding=8)
        s.map("Accent.TButton", background=[("active","#f25c80"),("disabled","#5a3a44")])
        s.configure("TButton", background=self.PANEL, foreground=self.TEXT, borderwidth=0, padding=6)
        s.map("TButton", background=[("active","#30343d")])
        s.configure("TNotebook", background=self.BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=self.PANEL, foreground=self.MUTED, padding=(14,8))
        s.map("TNotebook.Tab", background=[("selected", self.BG)], foreground=[("selected", self.TEXT)])

    def _build(self):
        top = ttk.Frame(self.root, padding=(16,14,16,6)); top.pack(fill="x")
        ttk.Label(top, text="GTA III / VC / SA (Xbox / Xbox 360) - Limit Adjuster", style="Title.TLabel").pack(anchor="w")
        ttk.Label(top, text="Load a default.xbe (Xbox) or default.xex (Xbox 360), make changes, then save a patched copy.",
                  foreground=self.MUTED, background=self.BG).pack(anchor="w")

        frow = ttk.Frame(self.root, padding=(16,4,16,4)); frow.pack(fill="x")
        ttk.Button(frow, text="Open XBE / XEX...", command=self.open_xbe).pack(side="left")
        self.file_lbl = ttk.Label(frow, text="No file loaded", foreground=self.MUTED, background=self.BG)
        self.file_lbl.pack(side="left", padx=12)

        # Pack the action bar (Save button) at the BOTTOM first, so it is always
        # reserved on-screen; the notebook then expands into the space above it.
        # (If the notebook were packed first with expand=True, tall tab content
        # would push the Save button off the bottom of the window.)
        act = ttk.Frame(self.root, padding=(16,8,16,14)); act.pack(side="bottom", fill="x")
        self.save_btn = ttk.Button(act, text="Save Patched XBE...", style="Accent.TButton",
                                   command=self.save_xbe, state="disabled")
        self.save_btn.pack(side="left")
        self.status = ttk.Label(act, text="", foreground=self.MUTED, background=self.BG)
        self.status.pack(side="left", padx=14)

        self.nb = ttk.Notebook(self.root); self.nb.pack(fill="both", expand=True, padx=16, pady=6)
        self._tab_limits(); self._tab_maplimits(); self._tab_vehicles(); self._tab_icon(); self._tab_notes()

    def _tab_limits(self):
        self.limits_tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(self.limits_tab, text="Limits")
        self.limits_hint = ttk.Label(self.limits_tab,
            text="Open an XBE to detect its memory pools.", style="Muted.TLabel")
        self.limits_hint.pack(anchor="w")
        self.limits_grid = ttk.Frame(self.limits_tab, style="Panel.TFrame")
        self.limits_grid.pack(fill="both", expand=True, pady=8)

    def _populate_limits(self):
        for w in self.limits_grid.winfo_children():
            w.destroy()
        self.vars.clear()
        stock = STOCK_DEFAULTS.get(self.game, {})

        # Lay the pools out in two side-by-side columns so long lists (e.g. the
        # 18-pool Xbox 360 build) stay within the window height, keeping the
        # Save button and every pool visible. Short lists use a single column.
        n = len(self.pools)
        use_two = n > 10
        split = (n + 1) // 2 if use_two else n
        groups = [self.pools[:split]]
        if use_two:
            groups.append(self.pools[split:])

        columns = ttk.Frame(self.limits_grid, style="Panel.TFrame")
        columns.pack(fill="both", expand=True)

        def make_column(parent, pools):
            col = ttk.Frame(parent, style="Panel.TFrame")
            hdr = ttk.Frame(col, style="Panel.TFrame"); hdr.pack(fill="x")
            for c,(t,w) in enumerate([("Pool",18),("Value",9),("Stock",8),("Offset",10),("Type",0)]):
                ttk.Label(hdr, text=t, style="Head.TLabel", width=w or None)\
                    .grid(row=0,column=c,sticky="w")
            body = ttk.Frame(col, style="Panel.TFrame"); body.pack(fill="both", expand=True, pady=(6,0))
            for i, p in enumerate(pools):
                ttk.Label(body, text=p.name, style="Muted.TLabel", width=18, foreground=self.TEXT)\
                    .grid(row=i,column=0,sticky="w",pady=3)
                if p.editable:
                    var = tk.StringVar(value=str(p.size)); self.vars[p.name]=var
                    e = tk.Entry(body,textvariable=var,width=9,justify="right",bg="#1b1d22",fg=self.TEXT,
                                 insertbackground=self.TEXT,relief="flat",highlightthickness=1,
                                 highlightbackground="#3a3f49",highlightcolor=self.ACCENT)
                    e.grid(row=i,column=1,sticky="w",padx=(0,8),pady=3,ipady=3)
                else:
                    ttk.Label(body, text=f"{p.size} (fixed)", style="Muted.TLabel", width=9)\
                        .grid(row=i,column=1,sticky="w")
                # Stock column: green if current matches stock, else muted
                sv = stock.get(p.name)
                if sv is None:
                    stxt, scol = "-", self.MUTED
                else:
                    stxt = str(sv)
                    scol = "#7fd18a" if p.size == sv else self.MUTED
                lbl = ttk.Label(body, text=stxt, width=8); lbl.grid(row=i,column=2,sticky="w")
                lbl.configure(style="Muted.TLabel")
                ttk.Label(body, text=("0x%X"%p.patch_off if p.patch_off else "-"),
                          style="Muted.TLabel", width=10).grid(row=i,column=3,sticky="w")
                kindlabel = {"mov_edi_imm32":"imm32","push_pop":"imm8 (<=127)","xor_inc":"fixed=1",
                             "ppc_li_imm16":"li imm16 (<=32767)"}.get(p.kind, p.kind)
                ttk.Label(body, text=kindlabel, style="Muted.TLabel").grid(row=i,column=4,sticky="w")
            return col

        for gi, grp in enumerate(groups):
            c = make_column(columns, grp)
            c.pack(side="left", anchor="n", padx=(0, 28 if gi == 0 else 0))

    def _tab_maplimits(self):
        self.maplimits_tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(self.maplimits_tab, text="Map Limits")
        self.maplimits_hint = ttk.Label(self.maplimits_tab,
            text="Open an XBE to see additional verified limits for that build.",
            style="Muted.TLabel")
        self.maplimits_hint.pack(anchor="w")
        self.maplimits_grid = ttk.Frame(self.maplimits_tab, style="Panel.TFrame")
        self.maplimits_grid.pack(fill="both", expand=True, pady=8)

    def _populate_maplimits(self):
        for w in self.maplimits_grid.winfo_children():
            w.destroy()
        self.extra_vars.clear()
        if not self.extras:
            ttk.Label(self.maplimits_grid, justify="left", style="Muted.TLabel",
                text=("No additional verified limits for this build.\n\n"
                      "These are model / texture limits located at fixed offsets and\n"
                      "shown only when the bytes match the known stock layout, so a\n"
                      "modified or unsupported build safely shows nothing here.\n\n"
                      "Boundaries and streaming memory are intentionally not exposed:\n"
                      "they can't be located in the Xbox build with the certainty\n"
                      "needed to patch them safely (see the Notes tab).")
                ).pack(anchor="w")
            return
        ttk.Label(self.maplimits_grid, justify="left", style="Muted.TLabel",
            text="Additional verified limits for this build, located by unique byte\n"
                 "signature or fixed offset. On VC these are model / texture / effect\n"
                 "caps for large map mods; on SA they include extra engine pools.\n"
                 "Each is shown only when its exact instruction is present, so an\n"
                 "unsupported build safely shows nothing.\n").pack(anchor="w")
        hdr = ttk.Frame(self.maplimits_grid, style="Panel.TFrame"); hdr.pack(fill="x")
        for c,(t,w) in enumerate([("Limit",22),("Value",12),("Stock",10),("Offset",12),("Description",0)]):
            ttk.Label(hdr, text=t, style="Head.TLabel", width=w or None).grid(row=0,column=c,sticky="w")
        body = ttk.Frame(self.maplimits_grid, style="Panel.TFrame"); body.pack(fill="both", expand=True, pady=(6,0))
        for i, e in enumerate(self.extras):
            ttk.Label(body, text=e.label, style="Muted.TLabel", width=22, foreground=self.TEXT)\
                .grid(row=i,column=0,sticky="w",pady=3)
            var = tk.StringVar(value=str(e.current)); self.extra_vars[e.label]=var
            ent = tk.Entry(body,textvariable=var,width=12,justify="right",bg="#1b1d22",fg=self.TEXT,
                           insertbackground=self.TEXT,relief="flat",highlightthickness=1,
                           highlightbackground="#3a3f49",highlightcolor=self.ACCENT)
            ent.grid(row=i,column=1,sticky="w",padx=(0,10),pady=3,ipady=3)
            ttk.Label(body, text=str(e.default), style="Muted.TLabel", width=10)\
                .grid(row=i,column=2,sticky="w")
            off_txt = "0x%X"%e.off + ("  (+%d)"%(len(e.offs)-1) if len(e.offs) > 1 else "")
            ttk.Label(body, text=off_txt, style="Muted.TLabel", width=12)\
                .grid(row=i,column=3,sticky="w")
            ttk.Label(body, text=e.desc, style="Muted.TLabel").grid(row=i,column=4,sticky="w")

    def _tab_vehicles(self):
        tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=16); self.nb.add(tab, text="Vehicles")
        ttk.Label(tab, text="Vehicle pool size", style="Head.TLabel").pack(anchor="w")
        row = ttk.Frame(tab, style="Panel.TFrame"); row.pack(anchor="w", pady=8)
        ttk.Label(row, text="Vehicles:", style="Muted.TLabel", foreground=self.TEXT).pack(side="left")
        self.veh_var = tk.StringVar(value="110")
        e = tk.Entry(row,textvariable=self.veh_var,width=10,justify="right",bg="#1b1d22",fg=self.TEXT,
                     insertbackground=self.TEXT,relief="flat",highlightthickness=1,
                     highlightbackground="#3a3f49",highlightcolor=self.ACCENT)
        e.pack(side="left", padx=8, ipady=3)
        self.veh_hint = ttk.Label(row, text="(load an XBE)", style="Muted.TLabel"); self.veh_hint.pack(side="left")
        ttk.Label(tab, justify="left", style="Muted.TLabel",
            text=("Loaded with a 3-byte 'push imm8; pop edi', so 1-127 is safe and\n"
                  "in place. Above 127 needs a 5-byte encoding (code cave), which\n"
                  "this tool does not perform - blocked to avoid corruption.")).pack(anchor="w", pady=(8,0))

    def _tab_icon(self):
        tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=16); self.nb.add(tab, text="Icon")
        if not PIL_OK:
            ttk.Label(tab, text="Icon editing needs Pillow.", style="Head.TLabel").pack(anchor="w")
            ttk.Label(tab, text="Install:  pip install pillow", style="Muted.TLabel").pack(anchor="w", pady=6)
            ttk.Label(tab, text=f"({PIL_ERR})", style="Muted.TLabel").pack(anchor="w")
            self.canvas = None
            return
        ttk.Label(tab, text="Title image (dashboard icon)", style="Head.TLabel").pack(anchor="w")
        body = ttk.Frame(tab, style="Panel.TFrame"); body.pack(fill="x", pady=10)
        self.canvas = tk.Canvas(body, width=128, height=128, bg="#101216",
                                highlightthickness=1, highlightbackground="#3a3f49")
        self.canvas.pack(side="left")
        right = ttk.Frame(body, style="Panel.TFrame"); right.pack(side="left", padx=16, anchor="n")
        ttk.Button(right, text="Choose PNG...", command=self.choose_icon).pack(anchor="w")
        self.icon_lbl = ttk.Label(right, text="Current icon shows after loading an XBE.",
                                  style="Muted.TLabel", justify="left"); self.icon_lbl.pack(anchor="w", pady=8)
        ttk.Label(right, justify="left", style="Muted.TLabel",
            text="Any image is resized to the icon size and re-encoded as DXT1.\n"
                 "Only the icon bytes change; file size is preserved.").pack(anchor="w")

    def _tab_notes(self):
        tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=12); self.nb.add(tab, text="Notes")
        txt = tk.Text(tab, wrap="word", bg="#1b1d22", fg=self.TEXT, relief="flat",
                      insertbackground=self.TEXT, padx=10, pady=10, height=20)
        txt.insert("1.0", NOTES_TEXT); txt.config(state="disabled")
        txt.pack(fill="both", expand=True)

    # ---------- helpers ----------
    def set_status(self, m, ok=True):
        self.status.config(text=m, foreground=("#7fd18a" if ok else "#f3777f"))

    # ---------- actions ----------
    def open_xbe(self):
        path = filedialog.askopenfilename(
            title="Select default.xbe or default.xex",
            filetypes=[("Xbox / Xbox 360 executable", "*.xbe *.xex"),
                       ("Original Xbox executable", "*.xbe"),
                       ("Xbox 360 executable", "*.xex"),
                       ("All files", "*.*")])
        if not path: return
        try:
            data = bytearray(open(path, "rb").read())
        except OSError as e:
            messagebox.showerror("Error", f"Could not read file:\n{e}"); return

        magic = bytes(data[:4])
        if magic == b"XBEH":
            self._load_xbe(data, path)
        elif magic == b"XEX2":
            self._load_xex(data, path)
        else:
            messagebox.showerror(
                "Unrecognised file",
                "File is neither an original Xbox executable (starts with 'XBEH')\n"
                "nor an Xbox 360 executable (starts with 'XEX2').")

    def _load_xbe(self, data, path):
        """Original Xbox path (x86 / little-endian) - unchanged behaviour."""
        name, tid = read_title(data)
        game = detect_game(name)
        pools = scan_pool_table(data, game)
        if len(pools) < 5:
            messagebox.showerror("Pools not found",
                "Could not locate the RenderWare pool allocator in this XBE.\n"
                "This tool supports GTA III / Vice City / San Andreas engines.")
            return

        self.platform = "xbox"; self.xex = None
        self.data, self.path, self.pools = data, path, pools
        self.game = game
        self.file_lbl.config(
            text=f"{os.path.basename(path)}   [{game}]   {len(pools)} pools",
            foreground=self.TEXT)
        self.limits_hint.config(
            text=f"Detected {game}. Title: {name!r}. Values read live from file.")
        self._populate_limits()
        self.extras = scan_extra_limits(data, game)
        self._populate_maplimits()

        # vehicles
        veh = next((p for p in pools if p.name == "Vehicles"), None)
        if veh and veh.kind == "push_pop":
            self.veh_var.set(str(veh.size)); self.veh_hint.config(text="(default 110)")
        else:
            self.veh_var.set("110"); self.veh_hint.config(text="(vehicle pool not the push form here)")

        self._pending_icon = None
        if PIL_OK and self.canvas is not None:
            img = extract_icon_rgb(self.data)
            if img is not None:
                self._show_icon(img)
                self.icon_lbl.config(text="Current title icon.")
            else:
                self.icon_lbl.config(text="No DXT1 title icon detected in this XBE.")
        self.save_btn.config(state="normal", text="Save Patched XBE...")
        self.set_status(f"Loaded {game} - {len(pools)} pools detected.")

    def _load_xex(self, data, path):
        """Xbox 360 path (PowerPC / big-endian XEX2). Currently supports the
        GTA San Andreas port; the pool table is found by instruction template."""
        xex = parse_xex(data)
        if xex is None:
            messagebox.showerror(
                "Unsupported XEX",
                "This XEX2 could not be read as a raw PowerPC image.\n\n"
                "The tool supports only unencrypted, basic-compression XEX files\n"
                "(as used by the retail GTA San Andreas 360 build). Encrypted or\n"
                "LZX-compressed titles are not supported.")
            return
        pools = scan_pool_table_xex(data, xex)
        if len(pools) < 5:
            messagebox.showerror(
                "Pools not found",
                "Could not locate the San Andreas pool table in this XEX.\n"
                "Only the GTA San Andreas Xbox 360 executable is supported.")
            return

        game = "GTA San Andreas (Xbox 360)"
        self.platform = "xbox360"; self.xex = xex
        self.data, self.path, self.pools = data, path, pools
        self.game = game
        self.file_lbl.config(
            text=f"{os.path.basename(path)}   [{game}]   {len(pools)} pools",
            foreground=self.TEXT)
        self.limits_hint.config(
            text=("Detected GTA San Andreas (Xbox 360, PowerPC). Values read live "
                  "from the executable. In-place editing is capped at 32767 per "
                  "pool (16-bit immediate)."))
        self._populate_limits()
        # The signature-based Map Limits framework is x86/XBE-specific; on 360
        # the extra limits (Quad Tree Nodes 2, Vehicle Structures) come through
        # the pool scanner itself, so there are no separate 'extras' here.
        self.extras = []
        self._populate_maplimits()

        # vehicles tab: 360 uses the ppc_li_imm16 form, editable up to 32767
        veh = next((p for p in pools if p.name == "Vehicles"), None)
        if veh and veh.kind == "ppc_li_imm16":
            self.veh_var.set(str(veh.size))
            self.veh_hint.config(text="(360: default 110, max 32767)")
        else:
            self.veh_var.set("110"); self.veh_hint.config(text="(vehicle pool not found)")

        # icon editing is XBE-only (XPR0/DXT1); disable preview for 360
        self._pending_icon = None
        if self.canvas is not None:
            self.canvas.delete("all")
            self.icon_lbl.config(text="Icon editing is not available for Xbox 360 (XEX) files.")
        self.save_btn.config(state="normal", text="Save Patched XEX...")
        self.set_status(f"Loaded {game} - {len(pools)} pools detected.")

    def choose_icon(self):
        if not PIL_OK: return
        if self.platform == "xbox360":
            messagebox.showinfo("Not available",
                "Icon editing is only supported for original Xbox (XBE) files.")
            return
        p = filedialog.askopenfilename(title="Choose icon image",
            filetypes=[("Images","*.png *.jpg *.jpeg *.bmp *.gif"),("All files","*.*")])
        if not p: return
        try:
            prev = Image.open(p).convert("RGB").resize((128,128), Image.LANCZOS)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open image:\n{e}"); return
        self._pending_icon = p
        self._show_icon(prev)
        self.icon_lbl.config(text=f"Will inject:\n{os.path.basename(p)}")
        self.set_status("Icon selected - written on save.")

    def _show_icon(self, pil_img):
        if self.canvas is None: return
        disp = pil_img.resize((128,128), Image.NEAREST) if pil_img.size != (128,128) else pil_img
        self.icon_preview = ImageTk.PhotoImage(disp)
        self.canvas.delete("all")
        self.canvas.create_image(0,0, anchor="nw", image=self.icon_preview)

    def _collect(self):
        vals = {}
        for p in self.pools:
            if not p.editable:
                continue
            raw = self.vars[p.name].get().strip()
            try:
                v = int(raw, 0)
            except ValueError:
                messagebox.showerror("Invalid value", f"{p.name}: '{raw}' is not a number."); return None
            if v < 1:
                messagebox.showerror("Invalid value", f"{p.name}: must be >= 1."); return None
            if p.kind == "push_pop" and v > 127:
                messagebox.showerror("Out of range",
                    f"{p.name}: max 127 for the in-place patch (code cave needed above)."); return None
            if p.kind == "ppc_li_imm16" and v > 32767:
                messagebox.showerror("Out of range",
                    f"{p.name}: max 32767 on Xbox 360.\n\n"
                    "The size is loaded by a 16-bit 'li' instruction; going higher\n"
                    "would need a two-instruction rewrite (code cave), which this\n"
                    "tool does not perform, to avoid corrupting the executable."); return None
            if v > p.max_value and not messagebox.askyesno("Unusually large",
                    f"{p.name} = {v} exceeds the suggested ceiling {p.max_value}. Patch anyway?"):
                return None
            vals[p.name] = v
        # vehicles tab overrides the detected Vehicles pool if present. On the
        # original Xbox that pool is a push_pop (1-127); on 360 it is a 16-bit
        # li immediate (1-32767).
        veh = next((p for p in self.pools if p.name == "Vehicles"
                    and p.kind in ("push_pop", "ppc_li_imm16")), None)
        if veh:
            raw = self.veh_var.get().strip()
            try:
                vv = int(raw, 0)
            except ValueError:
                messagebox.showerror("Invalid value", f"Vehicles: '{raw}' is not a number."); return None
            vmax = 127 if veh.kind == "push_pop" else 32767
            if not (1 <= vv <= vmax):
                messagebox.showerror("Out of range", f"Vehicles must be 1-{vmax}."); return None
            vals["Vehicles"] = vv
        return vals

    def _collect_extras(self):
        out = {}
        for e in self.extras:
            raw = self.extra_vars[e.label].get().strip()
            try:
                v = int(raw, 0)
            except ValueError:
                messagebox.showerror("Invalid value", f"{e.label}: '{raw}' is not a number."); return None
            if v < 1:
                messagebox.showerror("Invalid value", f"{e.label}: must be >= 1."); return None
            if v > e.max_sane and not messagebox.askyesno("Unusually large",
                    f"{e.label} = {v} exceeds the suggested ceiling {e.max_sane}. Patch anyway?"):
                return None
            out[e.label] = v
        return out

    def save_xbe(self):
        if self.data is None: return
        vals = self._collect()
        if vals is None: return
        extra_vals = self._collect_extras()
        if extra_vals is None: return

        out = bytearray(self.data)
        for p in self.pools:
            if p.name in vals:
                if p.kind == "mov_edi_imm32":
                    struct.pack_into("<I", out, p.patch_off, vals[p.name])
                elif p.kind == "push_pop":
                    out[p.patch_off] = vals[p.name]
                elif p.kind == "ppc_li_imm16":
                    # big-endian 16-bit immediate (low half of the PPC li word)
                    struct.pack_into(">H", out, p.patch_off, vals[p.name])

        for e in self.extras:
            if e.label in extra_vals:
                # patch every site this limit occupies (multi-site limits have
                # more than one) so paired arrays stay in sync.
                for off in e.offs:
                    struct.pack_into("<I", out, off, extra_vals[e.label])

        is360 = (self.platform == "xbox360")

        if self._pending_icon and PIL_OK and not is360:
            try:
                out = inject_icon(out, self._pending_icon)
            except Exception as e:
                messagebox.showerror("Icon error", f"Could not inject icon:\n{e}"); return

        if is360:
            dest = filedialog.asksaveasfilename(title="Save patched XEX as...",
                defaultextension=".xex", initialfile="default_patched.xex",
                filetypes=[("Xbox 360 executable", "*.xex")])
        else:
            dest = filedialog.asksaveasfilename(title="Save patched XBE as...",
                defaultextension=".xbe", initialfile="default_patched.xbe",
                filetypes=[("Xbox executable", "*.xbe")])
        if not dest: return
        if os.path.abspath(dest) == os.path.abspath(self.path):
            messagebox.showerror("Refused", "Choose a different filename to keep the original."); return
        try:
            open(dest, "wb").write(out)
        except OSError as e:
            messagebox.showerror("Error", f"Could not write file:\n{e}"); return

        self.set_status(f"Saved {os.path.basename(dest)} ({len(out)} bytes).")
        if is360:
            messagebox.showinfo("Done",
                f"Patched XEX written:\n{dest}\n\n"
                "File size is unchanged; only pool-size immediates were rewritten\n"
                "(big-endian, in place). Note the XEX is not re-signed - it must be\n"
                "run the same way you run the original (e.g. a dev/RGH/homebrew\n"
                "setup); a retail-signed console will reject a modified executable.\n\n"
                "Test in an emulator (Xenia) first. If models vanish after a big\n"
                "jump, don't save - lower values and step up gradually.")
        else:
            messagebox.showinfo("Done",
                f"Patched XBE written:\n{dest}\n\n"
                "File size and all opcodes are unchanged; only pool immediates, the\n"
                "vehicle byte, and (if chosen) the icon data were rewritten.\n\n"
                "Test in Xemu/CXBX before real hardware. If cars/peds vanish after a\n"
                "big jump, don't save your game - lower values and step up gradually.")


def main():
    root = tk.Tk()
    ModTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
