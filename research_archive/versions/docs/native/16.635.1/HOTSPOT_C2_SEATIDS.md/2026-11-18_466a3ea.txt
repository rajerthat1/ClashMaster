# HotSpot C2 miscompile of `org.crforge.env.SeatIds.of(II)I` — report and reproducer

Status: **reproduced on a second host, with the fault mapped into the C2 nmethod
and its assembly; the stand-alone reproducer is a false negative.**  Session 49
found the defect on a rented 6-vCPU A6000; session 51 rented the same class again
and the engine crashed **3/3** with the same signature, at the *same offset*
(`0x277`) in the level-4 nmethod of `SeatIds.of` (2848 bytes, installed 62–65 s
in), and `-XX:CompileCommand=print` gives the faulting instruction: a length load
of a null `int[]`.  The stand-alone driver (`SeatIdsOfRepro`) compiles `of` at
level 4 on that same host and does **not** crash: its compiled body is 3968 bytes
where the engine's is 2848, so it does not exercise the engine's compilation
shape.  Session 50's "the trigger is the host CPU" is therefore withdrawn — the
second host it tested was almost certainly affected and only the probe was
negative.  This file is the text for a JDK bug report, the engine reproduction
that is the working reproducer, and the probe that is not one.

- Producer: `tools/crforge/hotspot_repro.py` (probe — see §6.3, it is not the
  reproducer)
- Reproducer: the engine run in §3.2; the method under test is
  `tools/crforge/hotspot/SeatIdsOfRepro.java`
- Finding: [`FINDINGS.md`](FINDINGS.md) §3.85 (the crash) and §3.86 (this reproducer)
- Workaround in the tree: `--jvm-arg=-XX:TieredStopAtLevel=1` on `self_play.py` and
  the harnesses that forward it

## 1. Summary

`org.crforge.env.SeatIds.of(int, int)` is a small (194-byte) method that grows an
`int[][]` with `Arrays.copyOf` and then indexes the new slot:

```java
int[][] byKind = ids[seat];
if (kind >= byKind.length) {
  ids[seat] = byKind = Arrays.copyOf(byKind, kind + 1);
}
int[] of = byKind[kind];
if (of == null || n >= of.length) {      // <- bci 52 .. 62
  ...
}
...
return of[n] - 1;
```

On the affected host, once the method is a **level-4 (C2) nmethod**, the compiled
body compares `n` against the *previous* `byKind.length` instead of `of.length`
and dereferences a **null** `of` when a new entity kind appears (the slot
`byKind[kind]` that `Arrays.copyOf` just created). The JVM takes `SIGSEGV` with
`segfault at c` — offset 12, the array-length slot of a null `int[]`.

The bytecode is correct (see §5); the interpreted and C1 code are correct. This is
a C2 defect, not a crforge bug.

## 2. Environment

Session 49's host (first observation):

| Field | Value |
| --- | --- |
| CPU host | Thunder `base`, 6 vCPU, kernel `6.12.115-thunder-sandbox`, x86_64 |
| GPU | 1× RTX A6000 48 GB (not involved: the crash is on the CPU half) |
| JDK A | Ubuntu `OpenJDK 21.0.13.0.3` (`21.0.13.0.3+1-1-22.04.1-Ubuntu`) |
| JDK B | `Temurin 25.0.5` (`25.0.5+1`) |
| JVM flags | defaults (the engine starts via JPype with only `-Djava.awt.headless=true`) |
| Frequency | C2 **6/6** runs; `-Xint` 0/1; `-XX:TieredStopAtLevel=1` 0/3 |

Session 51's host (second observation, 3/3):

| Field | Value |
| --- | --- |
| CPU host | Thunder `base`, 6 vCPU, kernel `6.12.115-thunder-sandbox`, Ubuntu 22.04.5 |
| CPU | `Intel(R) Xeon(R) Processor @ 2.10GHz`, microcode `0x1`, flags `sse…avx2`, **no AVX-512** |
| GPU | 1× RTX A6000 48 GB (not involved) |
| JDK | Ubuntu `OpenJDK 21.0.13.1+2` (`21.0.13.1+2-1-22.04.1-Ubuntu`) — a *later* patch than session 49's, so the defect is not fixed in `.1` |
| JVM flags | defaults; workaround arm adds only `-XX:TieredStopAtLevel=1` |
| Frequency | C2 **3/3** runs (exit 139); C1 **1/1** clean |

## 3. Reproducing it

### 3.1 The engine run (the working reproducer)

The defect is reached through crforge's engine, on the CPU producer path, after
`of` has been recompiled at level 4 the second time and a new entity kind appears:

```bash
# crforge checkout with .resource/ (engine jars, tables, B-general-v275.pt)
export OMP_NUM_THREADS=1 PYTHONPATH=.resource/crforge/python
python3 tools/crforge/self_play.py \
    --count 4 --segments 3 --decisions-per-segment 24 \
    --device cpu --ppo-update \
    --jvm-arg=-XX:+UnlockDiagnosticVMOptions \
    --jvm-arg=-XX:+LogCompilation \
    --jvm-arg=-XX:LogFile=runs/hotspot-%p.log \
    --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
    --save-checkpoint ck.pt --output runs/cpu-producer.json
# exit 139, `segfault at c`, no hs_err
```

`-XX:CompileCommand=print,org.crforge.env.SeatIds::of` additionally prints the
nmethod's assembly when `hsdis` is installed at `$JAVA_HOME/lib/hsdis-amd64.so`
(Debian's `libhsdis0-fcml` package provides it).

### 3.2 The stand-alone driver (a probe, not a reproducer)

`tools/crforge/hotspot/SeatIdsOfRepro.java` is the crforge body verbatim with
`BattleEntity.IDS_PER_KIND` inlined as `1_000_000`, a public class, and the driver
in the same file. Its `of` instruction stream is checked against the built engine
jar on every run: same 117 instruction slots, same opcodes (only constant-pool
indices differ). The driver has two modes:

- `quick [rounds] [kinds]` — kinds `0..kinds-1` are warm for `rounds`, then new
  kinds are read for the first time;
- `soak [seconds] [newEvery]` — random ids over the live kinds for `seconds`, a
  new kind every `newEvery` calls (the affected host crashed 65.1 s in, so the
  default is 70 s); the kind count is capped at 1,000 because the id is
  `kind * 1_000_000` in an `int`.

`tools/crforge/hotspot_repro.py` compiles it with each JDK and runs three arms,
every one with `-XX:+PrintCompilation`:

| arm | flags | expectation |
| --- | --- | --- |
| `c2` | none | crashes on a host with the defect — **it does not** (see §6.3) |
| `c1` | `-XX:TieredStopAtLevel=1` | passes |
| `interpreter` | `-Xint` | passes |

```bash
.venv/bin/python tools/crforge/hotspot_repro.py \
    --jdks .resource/jdk21/jdk-21.0.13+9 .resource/jdk25/jdk-25.0.5+8 \
    --mode soak --seconds 70 --repeats 2 \
    --output runs/crforge-c4/hotspot-seatids-soak.json
```

Exit status: **0** reproduced, **1** every arm passed, **2** inconclusive. An arm
is only counted as a pass if its `PrintCompilation` output shows the level-4
nmethod of `of`. The artifact (`crforge-hotspot-seatids.v1`) records the host
(model, microcode and the full CPU flag list as of session 51), the JDK banners,
the bytecode check and every arm's exit status and tail.

A minimal hand-run:

```bash
JDK=.resource/jdk21/jdk-21.0.13+9
$JDK/bin/javac -d /tmp/seatids tools/crforge/hotspot/SeatIdsOfRepro.java
$JDK/bin/java -XX:+PrintCompilation -cp /tmp/seatids SeatIdsOfRepro soak 70 2000000
```

## 4. Evidence from the affected hosts

`dmesg`:

```
python3[11304]: segfault at c ip 00007ff522e60407 sp 00007ffdafd41b90 error 4 likely on CPU 1 (core 1, socket 0)
```

`-XX:+UnlockDiagnosticVMOptions -XX:+LogCompilation` matched that PC to:

```
<nmethod compile_id='2987' compiler='c2' level='4' entry='0x00007f8216e60d40' size='2128'
         address='0x00007f8216e60b90' relocation_offset='344' insts_offset='432'
         stub_offset='1352' scopes_data_offset='1392' scopes_pcs_offset='1792'
         dependencies_offset='2064' nul_chk_table_offset='2072' oops_offset='1376'
         metadata_offset='1384' method='org.crforge.env.SeatIds of (II)I' bytes='194'
         count='14406' iicount='14406' decompiles='1' unstable_if_traps='1' stamp='65.111'/>
```

The nmethod was installed 65.1 s into the run; the crash followed the next new
entity kind, i.e. the first execution that met `of == null`.

What does **not** clear it (all on the affected host):

| flag | result |
| --- | --- |
| `-Xint` | passes |
| `-XX:TieredStopAtLevel=1` | passes (the workaround) |
| `-XX:-UseSuperWord` | 3/3 crash |
| `-XX:UseAVX=2` | 3/3 crash |
| `-XX:CompileCommand=exclude,org.crforge.env.SeatIds::of` | 2/2 crash (session 49 read this as an inlined copy in the caller; session 51's fault is inside `of`'s **own** nmethod at `0x277`, so this arm needs re-running before the report leans on it) |
| Temurin 25.0.5 instead of 21.0.13 | crashes like 21.0.13 |

### 4.2 Session 51: the fault offset, and the assembly of the miscompiled body

A fresh Thunder `base` of the same class (Ubuntu 22.04.5, JDK `21.0.13.1+2`)
crashed **3/3** in the engine run of §3.1, `exit 139`, no `hs_err`:

```
[  211.102642] python3[6496]: segfault at c ip 00007f3d7847bc87 sp 00007ffeeffdcbb0 error 4 likely on CPU 3
[  715.166173] python3[7047]: segfault at c ip 00007fe328054307 sp 00007fff64807d70 error 4 likely on CPU 0
[  810.780436] python3[7215]: segfault at c ip 00007ff7e605ed87 sp 00007fff88556a40 error 4 likely on CPU 3
```

Both logged runs compile `of` at level 4 **twice** — a 1528-byte body at ~25 s
(no crash) and a 2848-byte body at 62–65 s (the crash). The fault PC lands at
the **same offset `0x277`** in the second:

| run | compile_id | level | size | count | installed | fault PC | offset |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 51-A | 3001 | 4 | 2848 | 15093 | 65.488 s | `0x00007fe328054307` | `0x277` |
| 51-B | 2922 | 4 | 2848 | 13864 | 62.065 s | `0x00007ff7e605ed87` | `0x277` |

`-XX:CompileCommand=print,org.crforge.env.SeatIds::of` (with `hsdis`) gives the
body. The faulting instruction is at `0x00007ff7e605ed84+3`:

```
  0x00007ff7e605ed64: 8b6c fc0c | 3bcd 0f8d | 3802 0000 | 3bcd 0f83 | c401 0000 | 4c63 ce49 | 2bc1 4d8d | 0cfc 458b
  0x00007ff7e605ed84: 4c81 1043 | 8b6c cc0c | 443b dd0f | 8d3b 0200 | 0044 3bdd | 0f83 d601 | 0000 4b8d | 34cc 428b
```

Byte `43 8b 6c cc 0c` = `mov 0xc(%r12,%r9,8),%ebp`: the **`.length` load of a
null `int[]`** (heap base `r12`, null narrow-oop in `r9`, so the faulting address
is `0xc` — `segfault at c`). The null check the bytecode has at bci 54 is not
honoured on this path.

A C1 arm (`-XX:TieredStopAtLevel=1`) completes all 3 segments on the same host:
gate pass, 563 eligible lane-decisions, 28 ACT (4.97 %), **0 illegal, 0
rejected**; so the workaround is confirmed on the second host as well.

## 5. The bytecode is correct

`javap -p -c` on the built engine jar (`.resource/crforge/env/build/install/env/lib/env-1.0-SNAPSHOT.jar`):

```
      46: aload         5          // byKind
      48: iload_3                  // kind
      49: aaload                   // byKind[kind]
      50: astore        6          // of
      52: aload         6
      54: ifnull        65          // of == null -> grow
      57: iload         4          // n
      59: aload         6
      61: arraylength              // of.length
      62: if_icmplt     115         // n >= of.length -> grow
      ...
     115: aload         6
     117: iload         4
     119: iaload                   // of[n]
     120: ifne          186
```

Both guards are present and both load from `of`. In the compiled body the
`n >= …` comparison used the value the compiler had loaded for
`kind >= byKind.length`, and the null check was not honoured on that path — so a
new kind (`of` just created, `n == 0`, previous `byKind.length >= 1`) falls through
to `of.length`/`of[0]` on a null reference. Reading the length of a null `int[]`
faults at offset 12, which is `segfault at c`.

## 6. Hosts the *driver* did not crash — and why that is not evidence

**A. This laptop.** Same JVMs, same 194-byte method, same machine check — no crash:

| Field | Value |
| --- | --- |
| CPU | 12th Gen Intel Core i5-1235U (Alder Lake), 12 threads, AVX2 |
| Kernel | `7.0.0-43-generic` |
| JDKs | `21.0.13` and `25.0.5` (the `.resource` Temurin trees) |
| `of` bytecode vs engine jar | identical (117 instruction slots, opcodes match) |
| `of` compiled at level 4 | 6/6 `c2` arms |
| `c2` arms (quick, 3 per JDK) | 0 crashes |
| control arms (`c1`, `-Xint`) | 0 crashes |
| soak, 70 s per arm | 0 crashes (artifact `runs/crforge-c4/hotspot-seatids-soak.json`) |

**B. A fresh Thunder `base` (2026-11-18).** 6 vCPU, 1×A6000 (unused), kernel
`6.12.115-thunder-sandbox`, Ubuntu 22.04.5, **the exact JDK build of the
affected host** (`21.0.13.0.3+1-1-22.04.1-Ubuntu`):

| Field | Value |
| --- | --- |
| CPU | Intel Xeon @ 2.10 GHz, 6 vCPU, AVX2 **without** AVX-512 |
| `of` compiled at level 4 | yes, in every arm (`PrintCompilation` grep) |
| quick (200k rounds, 2 kinds) | 6/6 runs clean |
| soak 70 s (a new kind every 2M calls) | 2/2 clean |
| soak 70 s, **1000 new kinds** (`--new-every 20000`) | 2/2 clean, inlined and `-XX:CompileCommand=dontinline` |
| C2-only (`-Xbatch -XX:-TieredCompilation`) | clean |

Evidence: `runs/crforge-c4/s50-thunder/` (`host.txt`, `D.*`, `E.*`, `soak1.*`).

**What B rules out — and what session 51 overturned.** B's bytes, kernel, tier and
trigger shape are ruled out for the *driver*; but session 51 ran the driver on a
host whose engine crashes 3/3 and the driver was clean there too (2/2 C2 arms at
level 4, bytecode identical). So B's negative says nothing about the host. The
driver's own level-4 body is **3968 bytes**, the engine's is **2848** — a
different compilation of the same method — so the driver does not reach the
compilation shape that is miscompiled. **The trigger is not the host CPU.**

One tooling defect surfaced while doing this: the soak driver's
`kind * IDS_PER_KIND` is an `int`, so 2,147 new kinds overflowed it and threw an
`ArrayIndexOutOfBoundsException` before the bug could be reached. The driver now
caps at 1,000 kinds; the throw was the driver's, not the JVM's.

**C. The session-51 host, driven by the probe (a false negative).** Same box as
§4.2, `hotspot_repro.py --mode soak --seconds 70 --new-every 20000 --repeats 2`:
2/2 C2 arms reached level 4, 0 crashes, bytecode identical, controls clean —
while the engine crashed 3/3 on that same box. Evidence:
`runs/crforge-c4/s51-a6000/hotspot-repro.json`.

## 7. What would make the report filable — and what session 51 closed

1. ~~Find a host that has the defect.~~ **Done:** session 51's Thunder A6000,
   3/3 engine crashes (§4.2).
2. ~~Record `lscpu`.~~ **Done:** `Intel Xeon @ 2.10 GHz`, microcode `0x1`,
   AVX2, no AVX-512; the tool now writes model, microcode and flags into every
   artifact.
3. ~~Attach `-XX:+LogCompilation` and an `hsdis` disassembly.~~ **Done:** fault
   PC → level-4 nmethod at offset `0x277`, and the faulting instruction
   (`mov 0xc(%r12,%r9,8),%ebp`, a null `int[]` length) (§4.2).
4. **Still open — a stand-alone reproducer.** The engine run is the reproducer
   today; a self-contained one would have to reach the engine's 2848-byte
   compilation of `of`, and the current driver compiles a 3968-byte body and
   stays clean. Until that is closed, file with §3.1 as the reproduction, the
   `of` listing in §5 and the assembly in §4.2, and state the driver negative
   explicitly so a triager does not read it as "cannot reproduce".
5. File at <https://bugreport.java.com> with the summary in §1, the environment in
   §2, the reproduction in §3.1 and the evidence in §4.

Do **not** read "the probe is clean" as "the host is clean": the probe is a false
negative on a host that crashes the engine 3/3. The flag stays on for every CPU
producer that meets a new entity kind after `of` has been recompiled at level 4,
and the host class is not narrowed.

