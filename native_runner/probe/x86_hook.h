// x86_64 inline-hook support for the FirstLight probe (replaces the AArch64 trampoline code).
//
// A hook overwrites the first >= 14 bytes of the target with `jmp qword ptr [rip+0]; .quad dst`.
// The overwritten instructions are copied verbatim into an executable trampoline followed by
// an absolute jump back. Only position-independent instructions are accepted: a RIP-relative
// operand or a relative branch inside the stolen range makes the install fail (logged), so a
// wrong address can never silently corrupt execution.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace x86hook {

constexpr std::size_t kAbsJumpSize = 14;

inline void emit_abs_jump(void *destination, const void *target) {
  auto *p = static_cast<std::uint8_t *>(destination);
  p[0] = 0xff;
  p[1] = 0x25;
  std::memset(p + 2, 0, 4);
  std::memcpy(p + 6, &target, sizeof(target));
}

// Length of the ModRM(+SIB+disp) tail; sets *rip_relative. Returns -1 if out of range.
inline int modrm_tail(const std::uint8_t *p, bool *rip_relative) {
  const std::uint8_t modrm = p[0];
  const int mod = modrm >> 6, rm = modrm & 7;
  int n = 1;
  if (mod != 3 && rm == 4) {
    const std::uint8_t sib = p[1];
    n += 1;
    if (mod == 0 && (sib & 7) == 5) n += 4;
  }
  if (mod == 0 && rm == 5) {
    *rip_relative = true;
    n += 4;
  } else if (mod == 1) {
    n += 1;
  } else if (mod == 2) {
    n += 4;
  }
  return n;
}

// Returns the instruction length, or -1 if unsupported / not position independent.
inline int insn_length(const std::uint8_t *p) {
  const std::uint8_t *start = p;
  bool opsize = false, rex_w = false;
  for (;;) {
    if (*p == 0x66) { opsize = true; ++p; continue; }
    if (*p == 0xf2 || *p == 0xf3) { ++p; continue; }
    break;
  }
  if ((*p & 0xf0) == 0x40) { rex_w = (*p & 8) != 0; ++p; }
  const std::uint8_t op = *p++;
  bool rip = false;
  int tail = 0;
  const int imm_z = opsize ? 2 : 4;
  switch (op) {
  case 0x50: case 0x51: case 0x52: case 0x53: case 0x54: case 0x55: case 0x56: case 0x57:
  case 0x58: case 0x59: case 0x5a: case 0x5b: case 0x5c: case 0x5d: case 0x5e: case 0x5f:
  case 0x90: case 0xc3: case 0xcc: case 0x99: case 0x98:
    break;
  case 0x6a: tail = 1; break;
  case 0x68: tail = 4; break;
  case 0xb8: case 0xb9: case 0xba: case 0xbb: case 0xbc: case 0xbd: case 0xbe: case 0xbf:
    tail = rex_w ? 8 : imm_z; break;
  case 0xb0: case 0xb1: case 0xb2: case 0xb3: case 0xb4: case 0xb5: case 0xb6: case 0xb7:
    tail = 1; break;
  case 0x01: case 0x03: case 0x09: case 0x0b: case 0x21: case 0x23: case 0x29: case 0x2b:
  case 0x31: case 0x33: case 0x39: case 0x3b: case 0x85: case 0x87: case 0x88: case 0x89:
  case 0x8a: case 0x8b: case 0x8d: case 0x63: case 0x00: case 0x02: case 0x08: case 0x0a:
  case 0x20: case 0x22: case 0x28: case 0x2a: case 0x30: case 0x32: case 0x38: case 0x3a: case 0x84:
    tail = modrm_tail(p, &rip); break;
  case 0x80: case 0x83: case 0xc0: case 0xc1: case 0xc6:
    tail = modrm_tail(p, &rip) + 1; break;
  case 0x81: case 0xc7: case 0x69:
    tail = modrm_tail(p, &rip) + imm_z; break;
  case 0x6b:
    tail = modrm_tail(p, &rip) + 1; break;
  case 0xd1: case 0xd3: case 0xff:
    // ff /2 (call) and ff /4 (jmp) through a register or a non-RIP memory operand are position
    // independent; a RIP-relative operand is still rejected below via `rip`.
    tail = modrm_tail(p, &rip); break;
  case 0xf6: tail = modrm_tail(p, &rip) + ((((p[0] >> 3) & 7) <= 1) ? 1 : 0); break;
  case 0xf7: tail = modrm_tail(p, &rip) + ((((p[0] >> 3) & 7) <= 1) ? imm_z : 0); break;
  case 0x0f: {
    const std::uint8_t op2 = *p++;
    switch (op2) {
    case 0x1e: if (p[0] == 0xfa || p[0] == 0xfb) { tail = 1; break; } return -1;  // endbr64/32
    case 0x1f: case 0x10: case 0x11: case 0x28: case 0x29: case 0x57: case 0x6f: case 0x7f:
    case 0xb6: case 0xb7: case 0xbe: case 0xbf: case 0xaf: case 0x45: case 0x44: case 0x4c:
    case 0x4d: case 0x4e: case 0x4f: case 0x47: case 0x42: case 0x43: case 0x46: case 0x48:
    case 0x40: case 0x41: case 0x49: case 0x4a: case 0x4b: case 0x2e: case 0x2f: case 0x5a:
    case 0x58: case 0x59: case 0x5c: case 0x5e: case 0x54: case 0x2a: case 0x2c: case 0x2d:
    case 0xd6: case 0xef: case 0xc6:
      tail = modrm_tail(p, &rip) + (op2 == 0xc6 ? 1 : 0); break;
    default:
      return -1;  // includes 0f 8x (relative jcc)
    }
    break;
  }
  default:
    return -1;  // relative branches (e8/e9/eb/7x/e3), and anything not decoded above
  }
  if (rip) return -1;
  return static_cast<int>((p - start) + tail);
}

// Number of bytes (whole instructions, >= kAbsJumpSize) to steal at `target`, or -1.
inline int steal_length(const std::uint8_t *target) {
  int total = 0;
  while (total < static_cast<int>(kAbsJumpSize)) {
    const int n = insn_length(target + total);
    if (n <= 0) return -1;
    total += n;
  }
  return total;
}

// Relocating trampoline builder: like steal_length, but also accepts short/near conditional jumps
// (70-7f rel8, 0f 80-8f rel32) and jmp rel8/rel32 inside the stolen range. Each is re-emitted as a
// short branch to a 14-byte absolute-jump stub placed after the jump back. Returns the number of
// stolen bytes and writes the trampoline (needs <= 160 bytes), or -1.
inline int build_trampoline(const std::uint8_t *target, std::uint8_t *tramp) {
  struct Fix { int at; const std::uint8_t *dest; };
  Fix fixes[6];
  int nfix = 0, src = 0, out = 0;
  int src_at[16], out_at[16], ninsn = 0;  // instruction start offsets: original -> trampoline
  while (src < static_cast<int>(kAbsJumpSize)) {
    const std::uint8_t *p = target + src;
    if (ninsn < 16) { src_at[ninsn] = src; out_at[ninsn] = out; ++ninsn; }
    // call rel32 -> call *[rip+2]; jmp +8; .quad dest   (16 bytes, return lands on the jmp)
    if (p[0] == 0xe8) {
      std::int32_t r;
      std::memcpy(&r, p + 1, 4);
      const std::uint8_t *dest = p + 5 + r;
      const std::uint8_t seq[8] = {0xff, 0x15, 0x02, 0x00, 0x00, 0x00, 0xeb, 0x08};
      std::memcpy(tramp + out, seq, 8);
      std::memcpy(tramp + out + 8, &dest, 8);
      out += 16;
      src += 5;
      continue;
    }
    // lea disp32(%rip), reg  ->  movabs $abs, reg
    {
      int q = 0;
      std::uint8_t rex = 0;
      if ((p[0] & 0xf0) == 0x40) { rex = p[0]; q = 1; }
      if (p[q] == 0x8d && (p[q + 1] & 0xc7) == 0x05 && (rex & 8)) {
        std::int32_t disp;
        std::memcpy(&disp, p + q + 2, 4);
        const int len = q + 6;
        const std::uint64_t abs = reinterpret_cast<std::uint64_t>(p + len + disp);
        const int reg = ((p[q + 1] >> 3) & 7) | ((rex & 4) ? 8 : 0);
        tramp[out++] = static_cast<std::uint8_t>(0x48 | ((reg & 8) ? 1 : 0));
        tramp[out++] = static_cast<std::uint8_t>(0xb8 | (reg & 7));
        std::memcpy(tramp + out, &abs, 8);
        out += 8;
        src += len;
        continue;
      }
    }
    int n = insn_length(p);
    if (n > 0) {
      std::memcpy(tramp + out, p, static_cast<std::size_t>(n));
      out += n;
      src += n;
      continue;
    }
    const std::uint8_t *dest = nullptr;
    std::uint8_t cc = 0xff;  // 0xff = unconditional
    if (p[0] >= 0x70 && p[0] <= 0x7f) { cc = p[0] & 0x0f; dest = p + 2 + static_cast<std::int8_t>(p[1]); n = 2; }
    else if (p[0] == 0x0f && p[1] >= 0x80 && p[1] <= 0x8f) {
      std::int32_t r; std::memcpy(&r, p + 2, 4); cc = p[1] & 0x0f; dest = p + 6 + r; n = 6; }
    else if (p[0] == 0xeb) { dest = p + 2 + static_cast<std::int8_t>(p[1]); n = 2; }
    else if (p[0] == 0xe9) { std::int32_t r; std::memcpy(&r, p + 1, 4); dest = p + 5 + r; n = 5; }
    else return -1;
    if (nfix >= 6) return -1;
    // short branch, displacement patched below once stub positions are known
    tramp[out] = (cc == 0xff) ? 0xeb : static_cast<std::uint8_t>(0x70 | cc);
    tramp[out + 1] = 0;
    fixes[nfix++] = {out, dest};
    out += 2;
    src += n;
  }
  emit_abs_jump(tramp + out, target + src);
  out += static_cast<int>(kAbsJumpSize);
  for (int i = 0; i < nfix; ++i) {
    const std::ptrdiff_t rel = fixes[i].dest - target;
    if (rel >= 0 && rel < src) {
      // Branch into the stolen range: those bytes are overwritten by the hook jump, so the branch
      // must land on the relocated copy inside the trampoline.
      int k = 0;
      while (k < ninsn && src_at[k] != rel) ++k;
      if (k == ninsn) return -1;  // lands mid-instruction: refuse
      const int disp = out_at[k] - (fixes[i].at + 2);
      if (disp < -128 || disp > 127) return -1;
      tramp[fixes[i].at + 1] = static_cast<std::uint8_t>(static_cast<std::int8_t>(disp));
      continue;
    }
    const int disp = out - (fixes[i].at + 2);
    if (disp > 127) return -1;
    tramp[fixes[i].at + 1] = static_cast<std::uint8_t>(disp);
    emit_abs_jump(tramp + out, fixes[i].dest);
    out += static_cast<int>(kAbsJumpSize);
  }
  return src;
}

}  // namespace x86hook
