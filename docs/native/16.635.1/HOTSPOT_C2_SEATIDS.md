# HotSpot C2 miscompile of `org.crforge.env.SeatIds.of(II)I` — report and reproducer

Status: **report drafted, reproducer written, affected host not re-run yet.**
Evidence for the defect is one rented 6-vCPU host (2026-11-18, session 49); every
other host tried, including this laptop, compiles the method correctly. This file
is the text for a JDK bug report, the reproducer that has to be run on an affected
host to make it filed, and the negative already recorded here.

- Producer: `tools/crforge/hotspot_repro.py`
- Reproducer: `tools/crforge/hotspot/SeatIdsOfRepro.java`
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

## 2. Environment on the affected host

| Field | Value |
| --- | --- |
| CPU host | Thunder `base`, 6 vCPU, kernel `6.12.115-thunder-sandbox`, x86_64 |
| GPU | 1× RTX A6000 48 GB (not involved: the crash is on the CPU half) |
| JDK A | Ubuntu `OpenJDK 21.0.13.0.3` (`21.0.13.0.3+1-1-22.04.1-Ubuntu`) |
| JDK B | `Temurin 25.0.5` (`25.0.5+1`) |
| JVM flags | defaults (the engine starts via JPype with only `-Djava.awt.headless=true`) |
| Frequency | C2 **6/6** runs; `-Xint` 0/1; `-XX:TieredStopAtLevel=1` 0/3 |

## 3. Reproducer

`tools/crforge/hotspot/SeatIdsOfRepro.java` is the crforge body verbatim with
`BattleEntity.IDS_PER_KIND` inlined as `1_000_000`, a public class, and the driver
in the same file. Its `of` instruction stream is checked against the built engine
jar on every run: same 117 instruction slots, same opcodes (only constant-pool
indices differ). The driver has two modes:

- `quick [rounds] [kinds]` — kinds `0..kinds-1` are warm for `rounds`, then new
  kinds are read for the first time;
- `soak [seconds] [newEvery]` — random ids over the live kinds for `seconds`, a
  new kind every `newEvery` calls (the affected host crashed 65.1 s in, so the
  default is 70 s).

`tools/crforge/hotspot_repro.py` compiles it with each JDK and runs three arms,
every one with `-XX:+PrintCompilation`:

| arm | flags | expectation on an affected host |
| --- | --- | --- |
| `c2` | none | crashes |
| `c1` | `-XX:TieredStopAtLevel=1` | passes |
| `interpreter` | `-Xint` | passes |

```bash
# on the affected host: this is the command that turns the finding into a filed report
.venv/bin/python tools/crforge/hotspot_repro.py \
    --jdks .resource/jdk21/jdk-21.0.13+9 .resource/jdk25/jdk-25.0.5+8 \
    --mode soak --seconds 70 --repeats 2 \
    --output runs/crforge-c4/hotspot-seatids-soak.json
```

Exit status: **0** reproduced, **1** every arm passed (a negative for that host),
**2** inconclusive. An arm is only counted as a pass if its `PrintCompilation`
output shows the level-4 nmethod of `of`; a run that never compiled the method is
`inconclusive`, not clean. The artifact (`crforge-hotspot-seatids.v1`) records the
host, the JDK banners, the bytecode check and every arm's exit status and tail.

A minimal hand-run is also enough:

```bash
JDK=.resource/jdk21/jdk-21.0.13+9
$JDK/bin/javac -d /tmp/seatids tools/crforge/hotspot/SeatIdsOfRepro.java
$JDK/bin/java -XX:+PrintCompilation -cp /tmp/seatids SeatIdsOfRepro soak 70 2000000
```

## 4. Evidence from the affected host

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
| `-XX:CompileCommand=exclude,org.crforge.env.SeatIds::of` | 2/2 crash (the call is inlined; excluding the callee leaves the miscompiled copy in the caller) |
| Temurin 25.0.5 instead of 21.0.13 | crashes like 21.0.13 |

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

## 6. Negative on the development host (recorded)

Same JVMs, same 194-byte method, same machine check — no crash:

| Field | Value |
| --- | --- |
| CPU | 12th Gen Intel Core i5-1235U (Alder Lake), 12 threads |
| Kernel | `7.0.0-43-generic` |
| JDKs | `21.0.13` and `25.0.5` (the `.resource` Temurin trees) |
| `of` bytecode vs engine jar | identical (117 instruction slots, opcodes match) |
| `of` compiled at level 4 | 6/6 `c2` arms |
| `c2` arms (quick, 3 per JDK) | 0 crashes |
| control arms (`c1`, `-Xint`) | 0 crashes |
| soak, 70 s per arm | 0 crashes (artifact `runs/crforge-c4/hotspot-seatids-soak.json`) |

So the defect is **host- or build-specific**, not universal: the same JDK major
version, the same bytecode, the same tier level. It looks like an ISA or
code-generation difference between the affected x86 host and this Alder Lake
laptop, or a build difference (`Temurin 25.0.5` crashed there — for the record,
both 21 and 25 crash *there* and neither crashes *here*).

## 7. What would make the report filable

1. Re-run `hotspot_repro.py --mode soak` on a host that has the defect (the same
   Thunder class), and get exit 0 with the crash captured in the artifact.
2. Attach `-XX:+LogCompilation` and, if possible, `-XX:CompileCommand=print,org.crforge.env.SeatIds::of`
   (or `hsdis` disassembly) from that run: the report needs the C2 IR or the
   assembly of the miscompiled body, not just the fault address.
3. File at <https://bugreport.java.com> with the summary in §1, the environment in
   §2, the reproducer in §3 and the evidence in §4.

Do **not** read "not reproduced here" as "the flag is unnecessary": the workaround
in `self_play.py` is kept until the defect is fixed upstream, because the affected
host is the one the production card runs on.
