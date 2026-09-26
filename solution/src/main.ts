// Pressor titration audit: reads the interface engine's week and prints one JSON line with, per
// order, the micrograms the patient got, the seconds above and below the ordered range, and the
// highest dose. Everything is exact: doses, weights and rates are kept as scaled integers.
import * as net from "net";

const HOST = process.env.ENGINE_HOST || "127.0.0.1";
const PORT = Number(process.env.ENGINE_PORT || "7710");
const PER_CONNECTION = 20;

// ── the engine ───────────────────────────────────────────────────────────────────────────────

class Engine {
  private sock: net.Socket | null = null;
  private buf = "";
  private lines: string[] = [];
  private waiter: (() => void) | null = null;
  private closed = true;
  private answers = 0;

  private async open(): Promise<void> {
    this.buf = "";
    this.lines = [];
    this.answers = 0;
    await new Promise<void>((resolve, reject) => {
      const s = net.createConnection({ host: HOST, port: PORT }, () => resolve());
      s.setEncoding("latin1");
      s.on("data", (d: string) => {
        if (this.sock !== s) return;
        this.buf += d;
        let i;
        while ((i = this.buf.indexOf("\n")) >= 0) {
          this.lines.push(this.buf.slice(0, i).replace(/\r$/, ""));
          this.buf = this.buf.slice(i + 1);
        }
        this.wake();
      });
      s.on("close", () => { if (this.sock === s) { this.closed = true; this.wake(); } });
      s.on("error", (e) => { reject(e); if (this.sock === s) { this.closed = true; this.wake(); } });
      this.sock = s;
      this.closed = false;
    });
  }

  private wake() { const w = this.waiter; this.waiter = null; if (w) w(); }

  private async line(): Promise<string | null> {
    while (this.lines.length === 0) {
      if (this.closed) return null;
      await new Promise<void>((r) => { this.waiter = r; });
    }
    return this.lines.shift()!;
  }

  // One request, answered in full; a connection that closes under it is reopened and the
  // request asked again, since no answer was taken from it.
  async ask(req: string): Promise<string[]> {
    for (let attempt = 0; attempt < 50; attempt++) {
      if (this.closed || this.answers >= PER_CONNECTION) {
        if (this.sock) this.sock.destroy();
        try { await this.open(); } catch { await sleep(200); continue; }
      }
      this.sock!.write(req + "\n");
      const head = await this.line();
      if (head === null) { this.closed = true; continue; }
      const out = [head];
      const m = /^PAGE (\d+) (\S+)$/.exec(head);
      if (m) {
        let ok = true;
        for (let k = 0; k < Number(m[1]); k++) {
          const l = await this.line();
          if (l === null) { ok = false; break; }
          out.push(l);
        }
        if (!ok) { this.closed = true; continue; }
      }
      this.answers++;
      return out;
    }
    throw new Error("engine unreachable");
  }

  close() { if (this.sock) { this.sock.write("QUIT\n"); this.sock.end(); } }
}

function sleep(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

async function readFeed(): Promise<string[]> {
  const eng = new Engine();
  const all: string[] = [];
  let token: string | null = null;
  for (;;) {
    const req = token === null ? "FEED" : `FEED ${token}`;
    const ans = await eng.ask(req);
    const head = ans[0];
    const busy = /^BUSY (\d+)$/.exec(head);
    if (busy) { await sleep(Number(busy[1]) + 5); continue; }
    const page = /^PAGE (\d+) (\S+)$/.exec(head);
    if (!page) throw new Error("engine said: " + head);
    all.push(...ans.slice(1));
    if (page[2] === "END") break;
    token = page[2];
  }
  eng.close();
  return all;
}

// ── numbers ──────────────────────────────────────────────────────────────────────────────────

function dec(s: string, places: number): bigint {
  const [w, f = ""] = s.split(".");
  if (f.length > places) throw new Error("too many places: " + s);
  return BigInt(w || "0") * 10n ** BigInt(places) + BigInt((f + "0".repeat(places)).slice(0, places) || "0");
}

function secs(s: string): number {
  return Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10), +s.slice(10, 12), +s.slice(12, 14)) / 1000;
}

// ── the week ─────────────────────────────────────────────────────────────────────────────────

interface Weight { stay: string; kind: string; taken: number; firstFiled: number; latestFiled: number; dk: bigint; }
interface Version { signed: number; min: bigint; max: bigint; }
interface Order { stay: string; unit: string; versions: Version[]; }
interface Program { order: string; unit: string; dm: bigint; wd: bigint; c: bigint; on: boolean; }

const SCALE = new Set(["BED", "STAND", "HOIST"]);

function rate10(p: Program): bigint {
  const [n, q] = p.unit === "mcg/kg/min" ? [6n * p.dm * p.wd, 100n * p.c] : [6n * p.dm, 10n * p.c];
  return (2n * n + q) / (2n * q);     // nearest tenth of a mL/h
}

async function main() {
  const feed = await readFeed();
  const weights = new Map<string, Weight>();
  const orders = new Map<string, Order>();
  const pump: { t: number; i: number; ch: string; ev: string; a: string[] }[] = [];
  feed.forEach((line, i) => {
    const f = line.split("|");
    if (f[0] === "W") {
      const filed = secs(f[1]);
      const w = weights.get(f[3]);
      if (!w) weights.set(f[3], { stay: f[2], kind: f[5], taken: secs(f[4]), firstFiled: filed, latestFiled: filed, dk: dec(f[6], 1) });
      else {
        if (filed < w.firstFiled) w.firstFiled = filed;
        if (filed >= w.latestFiled) { w.latestFiled = filed; w.dk = dec(f[6], 1); }
      }
    } else if (f[0] === "O") {
      let o = orders.get(f[3]);
      if (!o) { o = { stay: f[2], unit: f[6], versions: [] }; orders.set(f[3], o); }
      o.versions.push({ signed: secs(f[1]), min: dec(f[7], 3), max: dec(f[8], 3) });
    } else if (f[0] === "P") {
      pump.push({ t: secs(f[1]), i, ch: f[2], ev: f[3], a: f.slice(4) });
    }
  });
  for (const o of orders.values()) o.versions.sort((a, b) => a.signed - b.signed);

  // The weight pharmacy verified a version against: the scale weight taken last among those
  // already on the chart when it signed, else the stated weight; its figure as last corrected.
  function dosingWeight(stay: string, signed: number): bigint {
    let best: Weight | null = null;
    let stated: Weight | null = null;
    for (const w of weights.values()) {
      if (w.stay !== stay) continue;
      if (w.kind === "STATED") stated = w;
      if (SCALE.has(w.kind) && w.firstFiled <= signed && (!best || w.taken > best.taken)) best = w;
    }
    return (best || stated)!.dk;
  }

  // Every channel's programs, replayed in time order.
  pump.sort((a, b) => a.t - b.t || a.i - b.i);
  const live = new Map<string, Program>();
  const changes = new Map<string, { t: number; ch: string; p: Program | null }[]>();
  for (const e of pump) {
    let p = live.get(e.ch);
    if (e.ev === "START") {
      // a new program on a channel ends what that channel was pushing for its order
      if (p) changes.get(p.order)!.push({ t: e.t, ch: e.ch, p: null });
      p = { order: e.a[0], unit: e.a[1], dm: dec(e.a[2], 3), wd: e.a[3] === "-" ? 0n : dec(e.a[3], 1), c: BigInt(e.a[4]), on: true };
      live.set(e.ch, p);
    } else if (!p) continue;
    else if (e.ev === "DOSE") p.dm = dec(e.a[0], 3);
    else if (e.ev === "WEIGHT") p.wd = dec(e.a[0], 1);
    else if (e.ev === "BAG") p.c = BigInt(e.a[0]);
    else if (e.ev === "ALARM") p.on = false;
    else if (e.ev === "RESUME") p.on = true;
    else if (e.ev === "STOP") { p.on = false; live.delete(e.ch); }
    if (!changes.has(p.order)) changes.set(p.order, []);
    changes.get(p.order)!.push({ t: e.t, ch: e.ch, p: p.on ? { ...p } : null });
  }

  // Rows are written out as text from the exact integers, keyed by the order id as a JSON string:
  // no figure passes through a float and no id passes through an object property.
  const rows: string[] = [];
  for (const [oid, o] of [...orders.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
    const ch = changes.get(oid) || [];
    const wds = o.versions.map((v) => dosingWeight(o.stay, v.signed));
    const bounds = [...new Set([...ch.map((c) => c.t), ...o.versions.map((v) => v.signed)])].sort((a, b) => a - b);
    const state = new Map<string, Program>();
    let total = 0n, over = 0n, under = 0n;
    let peakN = -1n, peakD = 1n;
    let ci = 0;
    for (let k = 0; k + 1 < bounds.length; k++) {
      const a = bounds[k], b = bounds[k + 1];
      while (ci < ch.length && ch[ci].t <= a) {
        const c = ch[ci++];
        if (c.p) state.set(c.ch, c.p); else state.delete(c.ch);
      }
      if (state.size === 0) continue;
      let s = 0n;                                   // rate10 x conc, summed; mcg/min = s / 600
      for (const p of state.values()) s += rate10(p) * p.c;
      if (s === 0n) continue;                       // every rate rounds to zero: nothing is pushing
      total += s * BigInt(b - a);
      let vi = 0;
      for (let j = 0; j < o.versions.length; j++) if (o.versions[j].signed <= a) vi = j;
      const v = o.versions[vi];
      // dose = s / den in the order's unit
      const den = o.unit === "mcg/kg/min" ? 60n * wds[vi] : 600n;
      if (1000n * s > v.max * den) over += BigInt(b - a);
      if (1000n * s < v.min * den) under += BigInt(b - a);
      if (peakN < 0n || s * peakD > peakN * den) { peakN = s; peakD = den; }
    }
    const peak = peakN < 0n ? 0n : (1000n * peakN) / peakD;
    rows.push(`${JSON.stringify(oid)}:{"delivered_mcg":${total / 36000n},"over_s":${over},"under_s":${under},"peak":${peak}}`);
  }
  process.stdout.write(`{"orders":{${rows.join(",")}}}\n`);
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
