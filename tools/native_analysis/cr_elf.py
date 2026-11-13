#!/usr/bin/env python3
"""Static analysis helpers for Clash Royale libg.so builds.

The shipped libg.so has its section headers stripped, so every address in this
tool is a *virtual address* resolved through the ELF program headers.  For the
16.635.1 build the first RX LOAD segment has p_offset == p_vaddr, which is why
the community offset convention (`libg + 0x...`) works.

Subcommands
-----------
info                 segments, hashes, build id
strings              search strings, printing VA + nearest xrefs
xrefs                find adrp/add, adrp/ldr-literal and adr references to a VA
imm                  find instructions that materialise/compare an immediate
disasm               disassemble a VA range
refs                 find literal pointers to a VA in data
funcs                heuristic function starts from known prologues
symbols              dump the dynamic symbol table (JNI exports survive)

Examples
--------
python3 tools/native_analysis/cr_elf.py info .resource/native/16.635.1/reference/libg.so
python3 tools/native_analysis/cr_elf.py strings libg.so "Dropped BattleEventMessage"
python3 tools/native_analysis/cr_elf.py xrefs libg.so 0x2d128e
python3 tools/native_analysis/cr_elf.py disasm libg.so 0x156f790 0x156f800
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs

PT_LOAD = 1
PF_X = 0x1
PF_W = 0x2


@dataclass
class Segment:
    offset: int
    vaddr: int
    filesz: int
    memsz: int
    flags: int

    @property
    def executable(self) -> bool:
        return bool(self.flags & PF_X)

    @property
    def writable(self) -> bool:
        return bool(self.flags & PF_W)

    @property
    def end(self) -> int:
        return self.vaddr + self.filesz


class Binary:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.segments = self._parse_segments()
        self._dis = None
        self._instructions = None

    def _parse_segments(self) -> list[Segment]:
        data = self.data
        if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
            raise SystemExit("expected a little-endian ELF64 file")
        (e_phoff,) = struct.unpack_from("<Q", data, 0x20)
        (e_phentsize,) = struct.unpack_from("<H", data, 0x36)
        (e_phnum,) = struct.unpack_from("<H", data, 0x38)
        segments: list[Segment] = []
        for index in range(e_phnum):
            base = e_phoff + index * e_phentsize
            p_type, p_flags, p_offset, p_vaddr, _, p_filesz, p_memsz, _ = struct.unpack_from(
                "<IIQQQQQQ", data, base
            )
            if p_type != PT_LOAD:
                continue
            segments.append(Segment(p_offset, p_vaddr, p_filesz, p_memsz, p_flags))
        return segments

    def segment_for_va(self, va: int) -> Segment | None:
        for segment in self.segments:
            if segment.vaddr <= va < segment.end:
                return segment
        return None

    def offset_to_va(self, offset: int) -> int | None:
        for segment in self.segments:
            if segment.offset <= offset < segment.offset + segment.filesz:
                return segment.vaddr + offset - segment.offset
        return None

    def va_to_offset(self, va: int) -> int | None:
        segment = self.segment_for_va(va)
        if segment is None:
            return None
        return segment.offset + va - segment.vaddr

    def read(self, va: int, size: int) -> bytes:
        offset = self.va_to_offset(va)
        if offset is None:
            raise SystemExit(f"0x{va:x} is not mapped")
        return self.data[offset : offset + size]

    @property
    def dis(self) -> Cs:
        if self._dis is None:
            self._dis = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
            self._dis.skipdata = True
            self._dis.detail = True
        return self._dis

    def instructions(self, start: int | None = None, end: int | None = None):
        """Yield every disassembled instruction in the executable segments."""
        if self._instructions is None:
            rows = []
            for segment in self.segments:
                if not segment.executable:
                    continue
                code = self.data[segment.offset : segment.offset + segment.filesz]
                rows.extend(self.dis.disasm(code, segment.vaddr))
            self._instructions = rows
        for ins in self._instructions:
            if start is not None and ins.address < start:
                continue
            if end is not None and ins.address >= end:
                break
            yield ins

    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def build_id(self) -> str:
        # .note.gnu.build-id is the only NOTE program header on this build; walk
        # program headers for PT_NOTE instead of relying on sections.
        (e_phoff,) = struct.unpack_from("<Q", self.data, 0x20)
        (e_phentsize,) = struct.unpack_from("<H", self.data, 0x36)
        (e_phnum,) = struct.unpack_from("<H", self.data, 0x38)
        for index in range(e_phnum):
            base = e_phoff + index * e_phentsize
            p_type, _, p_offset, _, _, p_filesz = struct.unpack_from("<IIQQQQ", self.data, base)
            if p_type != 4:  # PT_NOTE
                continue
            note = self.data[p_offset : p_offset + p_filesz]
            cursor = 0
            while cursor + 12 <= len(note):
                namesz, descsz, ntype = struct.unpack_from("<III", note, cursor)
                cursor += 12
                name = note[cursor : cursor + namesz].rstrip(b"\0")
                cursor += (namesz + 3) & ~3
                desc = note[cursor : cursor + descsz]
                cursor += (descsz + 3) & ~3
                if name == b"GNU" and ntype == 3:
                    return desc.hex()
        return ""

    def symbols(self) -> list[tuple[int, int, str]]:
        """Parse DT_SYMTAB/DT_STRTAB manually (section headers are stripped)."""
        dynamic = None
        for segment in self.segments:
            if segment.writable and segment.filesz and segment.vaddr <= 0x1C0D488 < segment.end:
                dynamic = segment
                break
        if dynamic is None:
            return []
        # Locate .dynamic via the DT_* table address referenced by PT_DYNAMIC.
        (e_phoff,) = struct.unpack_from("<Q", self.data, 0x20)
        (e_phentsize,) = struct.unpack_from("<H", self.data, 0x36)
        (e_phnum,) = struct.unpack_from("<H", self.data, 0x38)
        dyn_off = dyn_size = 0
        for index in range(e_phnum):
            base = e_phoff + index * e_phentsize
            p_type, _, p_offset, _, _, p_filesz = struct.unpack_from("<IIQQQQ", self.data, base)
            if p_type == 2:  # PT_DYNAMIC
                dyn_off, dyn_size = p_offset, p_filesz
        symtab = strtab = strsz = syment = 0
        for cursor in range(dyn_off, dyn_off + dyn_size, 16):
            tag, value = struct.unpack_from("<QQ", self.data, cursor)
            if tag == 0:
                break
            if tag == 6:  # DT_SYMTAB
                symtab = value
            elif tag == 5:  # DT_STRTAB
                strtab = value
            elif tag == 10:
                strsz = value
            elif tag == 11:
                syment = value
        if not symtab or not strtab or not syment:
            return []
        sym_off = self.va_to_offset(symtab)
        str_off = self.va_to_offset(strtab)
        if sym_off is None or str_off is None:
            return []
        result = []
        index = 0
        while True:
            base = sym_off + index * syment
            if base + syment > len(self.data):
                break
            name, _info, _other, _shndx, value, _size = struct.unpack_from("<IBBHQQ", self.data, base)
            if index and name == 0 and value == 0:
                break
            if name >= strsz:
                break
            end = self.data.find(b"\0", str_off + name)
            text = self.data[str_off + name : end].decode("latin-1")
            if text:
                result.append((value, _size, text))
            index += 1
        return result


# --- instruction decoding helpers -------------------------------------------

def _sext(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def _adrp_page(ins_word: int, pc: int) -> int | None:
    if (ins_word & 0x9F000000) != 0x90000000:
        return None
    immlo = (ins_word >> 29) & 0x3
    immhi = (ins_word >> 5) & 0x7FFFF
    imm = _sext((immhi << 2) | immlo, 21)
    return (pc & ~0xFFF) + (imm << 12)


def _add_imm(ins_word: int) -> tuple[int, int, bool] | None:
    """Return (rd, imm, is64) for ADD (immediate)."""
    if (ins_word & 0x7F000000) not in (0x11000000, 0x91000000):
        return None
    rd = ins_word & 0x1F
    rn = (ins_word >> 5) & 0x1F
    imm = ((ins_word >> 10) & 0xFFF) << (12 if ins_word & 0x400000 else 0)
    return rd, rn, imm, bool(ins_word & 0x80000000)


def _ldr_literal(ins_word: int, pc: int) -> int | None:
    if (ins_word & 0x3B000000) != 0x18000000:
        return None
    imm = _sext((ins_word >> 5) & 0x7FFFF, 19) << 2
    return pc + imm


def _word_arrays(binary: Binary):
    import numpy as np

    for segment in binary.segments:
        if not segment.executable or segment.filesz < 4:
            continue
        words = np.frombuffer(
            binary.data, dtype="<u4", count=segment.filesz // 4, offset=segment.offset
        )
        yield segment.vaddr, words


def xrefs(binary: Binary, target: int, window: int = 16) -> list[tuple[int, str]]:
    """Return (site_va, kind) references to an absolute target VA.

    Fast path: vectorised ADRP filter + scalar confirmation of the next few
    instructions, so it stays usable on the 25 MB library.
    """
    import numpy as np

    hits: list[tuple[int, str]] = []
    page = target & ~0xFFF
    for vaddr, words in _word_arrays(binary):
        # adr
        adr_mask = (words & 0x9F000000) == 0x10000000
        for index in np.nonzero(adr_mask)[0]:
            word = int(words[index])
            imm = _sext(((word >> 5) & 0x7FFFF) << 2 | ((word >> 29) & 0x3), 21)
            if vaddr + index * 4 + imm == target:
                hits.append((vaddr + index * 4, "adr"))
        adrp_mask = (words & 0x9F000000) == 0x90000000
        candidates = np.nonzero(adrp_mask)[0]
        if candidates.size == 0:
            continue
        candidate_words = words[candidates].astype(np.int64)
        pcs = vaddr + candidates.astype(np.int64) * 4
        immlo = (candidate_words >> 29) & 0x3
        immhi = (candidate_words >> 5) & 0x7FFFF
        rel = (immhi << 2) | immlo
        rel = np.where(rel & 0x100000, rel - 0x200000, rel)
        pages = (pcs & ~0xFFF) + (rel << 12)
        keep = candidates[pages == page]
        for index in keep:
            index = int(index)
            rd = int(words[index]) & 0x1F
            for step in range(1, window + 1):
                cursor = index + step
                if cursor >= len(words):
                    break
                other_word = int(words[cursor])
                add = _add_imm(other_word)
                if add is not None:
                    _rd, rn, imm, _is64 = add
                    if rn == rd:
                        if page + imm == target:
                            hits.append((vaddr + cursor * 4, "adrp+add"))
                        break
                # adrp + ldr/str (unsigned offset) to a data address
                if (other_word & 0x3B000000) == 0x39000000:
                    rn = (other_word >> 5) & 0x1F
                    if rn == rd:
                        scale = other_word >> 30
                        imm = ((other_word >> 10) & 0xFFF) << scale
                        if page + imm == target:
                            kind = "adrp+ldr" if other_word & 0x00400000 else "adrp+str"
                            hits.append((vaddr + cursor * 4, kind))
                            break
                if (other_word & 0x9F000000) == 0x90000000:
                    break
                if (other_word & 0xFFFFFC1F) in (0xD63F0000, 0xD61F0000):
                    break
    return sorted(set(hits))


def callers(binary: Binary, target: int) -> list[tuple[int, str]]:
    """Return direct B/BL call sites to a target VA."""
    import numpy as np

    hits: list[tuple[int, str]] = []
    for vaddr, words in _word_arrays(binary):
        for mask, opcode, kind in ((0xFC000000, 0x94000000, "bl"), (0xFC000000, 0x14000000, "b")):
            sites = np.nonzero((words & mask) == opcode)[0]
            if sites.size == 0:
                continue
            raw = words[sites].astype(np.int64)
            offsets = (raw & 0x3FFFFFF).astype(np.int64)
            offsets = np.where(offsets & 0x2000000, offsets - 0x4000000, offsets)
            dests = vaddr + sites.astype(np.int64) * 4 + (offsets << 2)
            for site in sites[dests == target]:
                hits.append((vaddr + int(site) * 4, kind))
    return sorted(set(hits))


def immediate_hits(binary: Binary, value: int, limit: int = 200) -> list[tuple[int, str]]:
    """Find MOVZ/MOVN/MOVK/CMP/ADD/SUB immediate materialisations."""
    hits: list[tuple[int, str]] = []
    for ins in binary.instructions():
        if len(ins.bytes) != 4:
            continue
        word = struct.unpack_from("<I", ins.bytes)[0]
        op = word & 0x7F800000
        if op in (0x52800000, 0x12800000, 0x72800000, 0xD2800000, 0x92800000, 0xF2800000):
            imm = (word >> 5) & 0xFFFF
            shift = 16 if (word >> 21) & 0x3 else 0
            if op in (0x52800000, 0xD2800000):
                got = imm << shift
            elif op in (0x12800000, 0x92800000):
                got = (~imm & 0xFFFF) << shift
            else:
                continue
            if got == value:
                hits.append((ins.address, ins.mnemonic + " " + ins.op_str))
        elif (word & 0x7F000000) in (0x11000000, 0x51000000, 0x71000000, 0x31000000):
            # ADD/SUB/CMP immediate (12-bit)
            imm = (word >> 10) & 0xFFF
            if (word >> 22) & 1:
                imm <<= 12
            if imm == value:
                hits.append((ins.address, ins.mnemonic + " " + ins.op_str))
        if len(hits) >= limit:
            break
    return hits


# --- CLI --------------------------------------------------------------------

def _print_info(binary: Binary) -> None:
    print(f"path        {binary.path}")
    print(f"size        {len(binary.data)} bytes")
    print(f"sha256      {binary.sha256()}")
    print(f"build id    {binary.build_id()}")
    for segment in binary.segments:
        flags = ("R" if not segment.writable else "RW") + ("X" if segment.executable else "")
        print(
            f"LOAD off=0x{segment.offset:08x} va=0x{segment.vaddr:08x} "
            f"filesz=0x{segment.filesz:08x} memsz=0x{segment.memsz:08x} {flags}"
        )


def _print_strings(binary: Binary, patterns: list[str], do_xrefs: bool) -> None:
    needles = [pattern.encode() for pattern in patterns]
    for needle in needles:
        print(f"\n== {needle.decode('latin-1')!r}")
        start = 0
        shown = 0
        while shown < 40:
            index = binary.data.find(needle, start)
            if index < 0:
                break
            start = index + 1
            va = binary.offset_to_va(index)
            if va is None:
                continue
            end = binary.data.find(b"\0", index)
            text = binary.data[index:end].decode("latin-1", "replace")
            print(f"0x{va:08x}  {text[:160]}")
            if do_xrefs:
                for site, kind in xrefs(binary, va)[:10]:
                    print(f"    xref 0x{site:08x} {kind}")
            shown += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_elf(name: str, help_text: str = ""):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("elf")
        return command

    add_elf("info")
    p_strings = add_elf("strings")
    p_strings.add_argument("patterns", nargs="+")
    p_strings.add_argument("--xrefs", action="store_true")

    p_xrefs = add_elf("xrefs")
    p_xrefs.add_argument("targets", nargs="+", type=lambda s: int(s, 0))

    p_imm = add_elf("imm")
    p_imm.add_argument("value", type=lambda s: int(s, 0))
    p_imm.add_argument("--limit", type=int, default=200)

    p_disasm = add_elf("disasm")
    p_disasm.add_argument("start", type=lambda s: int(s, 0))
    p_disasm.add_argument("end", type=lambda s: int(s, 0))

    p_calls = add_elf("calls")
    p_calls.add_argument("targets", nargs="+", type=lambda s: int(s, 0))

    p_refs = add_elf("refs")
    p_refs.add_argument("targets", nargs="+", type=lambda s: int(s, 0))

    p_syms = add_elf("symbols")
    p_syms.add_argument("--grep", default="")

    args = parser.parse_args(argv)
    binary = Binary(args.elf)

    if args.command == "info":
        _print_info(binary)
    elif args.command == "strings":
        _print_strings(binary, args.patterns, args.xrefs)
    elif args.command == "xrefs":
        for target in args.targets:
            print(f"\n== xrefs to 0x{target:08x}")
            for site, kind in xrefs(binary, target):
                print(f"0x{site:08x}  {kind}")
    elif args.command == "imm":
        for site, text in immediate_hits(binary, args.value, args.limit):
            print(f"0x{site:08x}  {text}")
    elif args.command == "disasm":
        for ins in binary.dis.disasm(binary.read(args.start, args.end - args.start), args.start):
            print(f"0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}")
    elif args.command == "calls":
        for target in args.targets:
            print(f"\n== callers of 0x{target:08x}")
            for site, kind in callers(binary, target):
                print(f"0x{site:08x}  {kind}")
    elif args.command == "refs":
        for target in args.targets:
            print(f"\n== data pointers to 0x{target:08x}")
            needle = struct.pack("<Q", target)
            start = 0
            while True:
                index = binary.data.find(needle, start)
                if index < 0:
                    break
                start = index + 8
                va = binary.offset_to_va(index)
                if va is not None:
                    print(f"0x{va:08x}")
    elif args.command == "symbols":
        for value, size, name in binary.symbols():
            if args.grep and args.grep.lower() not in name.lower():
                continue
            print(f"0x{value:08x} size=0x{size:<6x} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
