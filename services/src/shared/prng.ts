/**
 * Deterministic seeded PRNG.
 *
 * Fixture generation must be reproducible across machines and reruns — the whole
 * benchmark depends on every condition seeing byte-identical data. Nothing here
 * may touch Date.now(), Math.random(), or any ambient state.
 */

export type Rng = ReturnType<typeof makeRng>;

/** mulberry32 — small, fast, good enough for fixture data, fully deterministic. */
export function makeRng(seed: number) {
  let a = seed >>> 0;

  const next = (): number => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  const api = {
    next,

    /** Integer in [min, max] inclusive. */
    int(min: number, max: number): number {
      return min + Math.floor(next() * (max - min + 1));
    },

    /** Float in [min, max), rounded to `decimals`. */
    float(min: number, max: number, decimals = 2): number {
      const v = min + next() * (max - min);
      const f = 10 ** decimals;
      return Math.round(v * f) / f;
    },

    bool(trueProbability = 0.5): boolean {
      return next() < trueProbability;
    },

    pick<T>(items: readonly T[]): T {
      if (items.length === 0) throw new Error('pick() on empty array');
      return items[Math.floor(next() * items.length)]!;
    },

    /** Weighted pick. `weights` must align with `items` and sum > 0. */
    weighted<T>(items: readonly T[], weights: readonly number[]): T {
      if (items.length !== weights.length) {
        throw new Error('weighted(): items/weights length mismatch');
      }
      const total = weights.reduce((s, w) => s + w, 0);
      let r = next() * total;
      for (let i = 0; i < items.length; i++) {
        r -= weights[i]!;
        if (r <= 0) return items[i]!;
      }
      return items[items.length - 1]!;
    },

    /** `count` distinct members of `items` (Fisher–Yates on a copy). */
    sample<T>(items: readonly T[], count: number): T[] {
      const pool = [...items];
      const n = Math.min(count, pool.length);
      for (let i = 0; i < n; i++) {
        const j = i + Math.floor(next() * (pool.length - i));
        [pool[i], pool[j]] = [pool[j]!, pool[i]!];
      }
      return pool.slice(0, n);
    },

    /** Zero-padded sequential identifier, e.g. id('FL', 142, 4) -> "FL-0142". */
    id(prefix: string, n: number, width = 4): string {
      return `${prefix}-${String(n).padStart(width, '0')}`;
    },
  };

  return api;
}

/**
 * Fixed epoch for all generated timestamps. Deliberately a constant rather than
 * `new Date()` — reruns must produce identical fixtures forever.
 */
export const BASE_DATE = Date.UTC(2026, 2, 14, 0, 0, 0); // 2026-03-14T00:00:00Z

/** Minutes -> ISO-8601 UTC string, offset from BASE_DATE. */
export function utcFromBase(minutes: number): string {
  return new Date(BASE_DATE + minutes * 60_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
}
