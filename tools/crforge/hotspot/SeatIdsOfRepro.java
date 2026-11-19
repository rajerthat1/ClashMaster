/*
 * Stand-alone reproducer for the HotSpot C2 miscompile of
 * org.crforge.env.SeatIds.of(II)I seen on a rented 6-vCPU host
 * (docs/native/16.635.1/FINDINGS.md section 3.85).
 *
 * `of` below is the crforge body verbatim, from
 * .resource/crforge/env/src/main/java/org/crforge/env/SeatIds.java at commit c4721bd9, with
 * BattleEntity.IDS_PER_KIND inlined as 1_000_000.  The class is public, the other two methods
 * (`clear`, `battleId`) are omitted, and the driver lives in this file; the body of `of` itself
 * is unchanged, so its compiled method has the recorded 194 bytes.
 *
 * Progress goes to stderr so stdout is only ``-XX:+PrintCompilation`` output.
 *
 * The crash was `segfault at c` while the method was a level-4 (C2) nmethod: the recompiled body
 * assumed `of != null` and loaded `of.length` with no test between them, so a new entity kind
 * (a slot `byKind[kind]` that is still null) faulted at offset 0xc.  On a healthy JVM this
 * program prints ids and exits 0.
 *
 * Modes:
 *   quick [rounds] [kinds]   one warm loop, then new kinds (deterministic, < 1 s)
 *   soak [seconds] [newEvery] random ids over the live kinds; a new kind every newEvery calls,
 *                            for `seconds` (the box crashed 65.1 s in, so the default is 70)
 *   phased [kinds] [n] [rounds] [settleMs]
 *                            the engine's compile sequence: warm on assigned slots, let C2
 *                            compile `of` one-sided, flip a guard, let it reinterpret and
 *                            recompile, then show the recompiled body a new kind
 *
 * Battle ids are kind * IDS_PER_KIND + n, as the engine's are: a new kind is a null slot in
 * byKind, which is the state the miscompiled body fails to test.
 */

public final class SeatIdsOfRepro {

  private static final int IDS_PER_KIND = 1_000_000;

  /** `kind * IDS_PER_KIND` has to stay inside an int, and no board is near a thousand kinds. */
  private static final int MAX_KINDS = 1000;

  /** Per seat and entity kind, by the battle id's count within its kind: the seat's id + 1. */
  private final int[][][] ids = new int[2][0][];

  /** Per seat, by the seat's id: the battle id it stands for. */
  private final int[][] battleIds = {new int[64], new int[64]};

  /** Per seat: the ids given, so the next one. */
  private final int[] given = new int[2];

  int of(int seat, int battleId) {
    int kind = battleId / IDS_PER_KIND;
    int n = battleId % IDS_PER_KIND;
    int[][] byKind = ids[seat];
    if (kind >= byKind.length) {
      ids[seat] = byKind = java.util.Arrays.copyOf(byKind, kind + 1);
    }
    int[] of = byKind[kind];
    if (of == null || n >= of.length) {
      int size = Math.max(n + 1, of == null ? 64 : of.length * 2);
      byKind[kind] = of = of == null ? new int[size] : java.util.Arrays.copyOf(of, size);
    }
    if (of[n] == 0) {
      int id = given[seat]++;
      if (id == battleIds[seat].length) {
        battleIds[seat] = java.util.Arrays.copyOf(battleIds[seat], 2 * id);
      }
      battleIds[seat][id] = battleId;
      of[n] = id + 1;
    }
    return of[n] - 1;
  }

  /** The warm loop: the live kinds exist before the method is hot, then a new one appears. */
  private static long quick(SeatIdsOfRepro ids, int rounds, int kinds) {
    long sum = 0;
    for (int round = 0; round < rounds; round++) {
      for (int kind = 0; kind < kinds; kind++) {
        for (int n = 0; n < 8; n++) {
          sum += ids.of(round & 1, kind * IDS_PER_KIND + n);
        }
      }
    }
    return sum;
  }

  /**
   * The engine's shape in miniature: entities of a few kinds are observed for a long time, then
   * a kind never observed before appears (a unit spawned for the first time on the board).
   * Ids are drawn from a caller-local xorshift so the run is deterministic and allocation-free.
   */
  private static long soak(SeatIdsOfRepro ids, int seconds, int newEvery) {
    long sum = 0;
    long calls = 0;
    int kinds = 2;
    int state = 0x9e3779b9;
    long started = System.nanoTime();
    long nextReport = started + 1_000_000_000L;
    while (System.nanoTime() - started < seconds * 1_000_000_000L) {
      state ^= state << 13;
      state ^= state >>> 17;
      state ^= state << 5;
      int seat = (state >>> 30) & 1;
      int kind = (state >>> 1) % kinds;
      int n = (state >>> 8) % 2048;
      sum += ids.of(seat, kind * IDS_PER_KIND + n);
      calls++;
      if (calls % newEvery == 0 && kinds < MAX_KINDS) {
        kinds++;
        // A new kind, read once: the slot is null and n is 0 in the first call below.
        sum += ids.of(seat, kinds * IDS_PER_KIND);
        System.err.printf("t=%.1fs kinds=%d calls=%d%n",
            (System.nanoTime() - started) / 1e9, kinds, calls);
      }
      if (System.nanoTime() > nextReport) {
        System.err.printf("t=%.1fs kinds=%d calls=%d sum=%d%n",
            (System.nanoTime() - started) / 1e9, kinds, calls, sum);
        System.err.flush();
        nextReport += 1_000_000_000L;
      }
    }
    return sum;
  }

  /**
   * The engine's compile sequence, made deterministic.
   *
   * The engine's log on the affected host shows `of` compiled by C2 at **1528
   * bytes** (all four branches one-sided, so all four become uncommon traps),
   * made not entrant by `unstable_if action='reinterpret'` when a guard finally
   * went the other way, and recompiled at **2848 bytes**; the crash is in that
   * recompiled body on the next new entity kind.  The `quick` and `soak` drivers
   * never reach that sequence: their first C2 body is a different shape (3968 /
   * 4064 bytes), so they do not exercise the recompilation this defect lives in.
   *
   * This mode builds the id tables directly (so `of` has never taken a guard
   * when C2 first compiles it), warms both seats over assigned slots (every
   * guard false), lets that compilation install, then flips `kind >=
   * byKind.length` with the first new kind.  That deopts the method; it is
   * warmed again so C2 recompiles it with the taken branch in the profile, and
   * only after that recompiled body has installed is it shown new kinds.
   */
  private static long phased(SeatIdsOfRepro ids, int kinds, int n, int rounds, long settleMs) {
    ids.seed(kinds, n);
    long sum = warm(ids, kinds, n, rounds, -1);
    System.err.printf("phased: warm done sum=%d%n", sum);
    System.err.flush();
    // let the one-sided C2 body of `of` install before a guard changes
    sleep(settleMs);
    sum += ids.of(0, kinds * IDS_PER_KIND);  // the flip: a kind with no array at all
    System.err.printf("phased: flip done%n");
    System.err.flush();
    // The trap reinterpreted `of`.  Keep it hot in short chunks so the compiler
    // thread can install C1 and then recompile at C2; one long loop here races
    // the compiler queue and often ends before the recompiled body exists.
    for (int chunk = 0; chunk < rounds; chunk++) {
      sum += warm(ids, kinds, n, 1, kinds);
      sleep(20);
    }
    System.err.printf("phased: rewarm done sum=%d%n", sum);
    System.err.flush();
    // and let the recompiled body install before a null slot is read
    sleep(settleMs);
    for (int kind = kinds + 1; kind < kinds + 5; kind++) {
      sum += ids.of(kind & 1, kind * IDS_PER_KIND);
      System.err.printf("phased: kind %d ok%n", kind);
      System.err.flush();
    }
    return sum;
  }

  /**
   * Assigned slots for `kinds` kinds, built without calling `of`: its profile
   * has never taken a guard when the warm loop starts, which is the state the
   * engine's first C2 compilation saw (1528 bytes, all guards one-sided).
   */
  private void seed(int kinds, int n) {
    for (int seat = 0; seat < 2; seat++) {
      int[][] byKind = new int[kinds][];
      for (int kind = 0; kind < kinds; kind++) {
        int[] of = new int[n];
        for (int i = 0; i < n; i++) {
          of[i] = i + 1;
        }
        byKind[kind] = of;
      }
      ids[seat] = byKind;
    }
  }

  /** Every guard false: the slots exist and are assigned. `extra` is a kind to read slot 0 of. */
  private static long warm(SeatIdsOfRepro ids, int kinds, int n, int rounds, int extra) {
    long sum = 0;
    for (int round = 0; round < rounds; round++) {
      for (int seat = 0; seat < 2; seat++) {
        for (int kind = 0; kind < kinds; kind++) {
          for (int i = 0; i < n; i++) {
            sum += ids.of(seat, kind * IDS_PER_KIND + i);
          }
        }
        if (extra >= 0) {
          sum += ids.of(seat, extra * IDS_PER_KIND);
        }
      }
    }
    return sum;
  }

  private static void sleep(long ms) {
    try {
      Thread.sleep(ms);
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
    }
  }

  public static void main(String[] args) {
    String mode = args.length > 0 ? args[0] : "quick";
    SeatIdsOfRepro ids = new SeatIdsOfRepro();
    long t0 = System.nanoTime();
    if (mode.equals("soak")) {
      int seconds = args.length > 1 ? Integer.parseInt(args[1]) : 70;
      int newEvery = args.length > 2 ? Integer.parseInt(args[2]) : 2_000_000;
      System.err.printf("soak: %d s, a new kind every %d calls%n", seconds, newEvery);
      System.err.flush();
      long sum = soak(ids, seconds, newEvery);
      System.err.printf("soak: done sum=%d in %.1f ms%n", sum, (System.nanoTime() - t0) / 1e6);
    } else if (mode.equals("phased")) {
      int kinds = args.length > 1 ? Integer.parseInt(args[1]) : 8;
      int n = args.length > 2 ? Integer.parseInt(args[2]) : 512;
      int rounds = args.length > 3 ? Integer.parseInt(args[3]) : 12;
      long settleMs = args.length > 4 ? Long.parseLong(args[4]) : 300;
      System.err.printf("phased: kinds=%d n=%d rounds=%d settle=%dms%n", kinds, n, rounds, settleMs);
      System.err.flush();
      long sum = phased(ids, kinds, n, rounds, settleMs);
      System.err.printf("phased: done sum=%d in %.1f ms%n", sum, (System.nanoTime() - t0) / 1e6);
    } else {
      int rounds = args.length > 1 ? Integer.parseInt(args[1]) : 200_000;
      int kinds = args.length > 2 ? Integer.parseInt(args[2]) : 2;
      long sum = quick(ids, rounds, kinds);
      System.err.printf("warm: kinds=%d rounds=%d sum=%d in %.1f ms%n",
          kinds, rounds, sum, (System.nanoTime() - t0) / 1e6);
      for (int kind = kinds; kind < kinds + 4; kind++) {
        System.err.printf("kind %d id %d%n", kind, ids.of((kind - kinds) & 1, kind * IDS_PER_KIND));
      }
    }
  }
}
