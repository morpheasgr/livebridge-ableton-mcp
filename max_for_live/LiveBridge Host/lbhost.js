// LiveBridge Host — the Node for Max part of the plug-in host devices (effect and instrument).
//
// Live's API reaches at most 128 parameters of a plug-in (the ones added with Configure). These
// devices load the plug-in inside Max's own vst~ object instead, which can list and set every
// parameter. This script talks to vst~ through the patch ("vst" messages out, "vstout <outlet>
// ..." messages back) and to the LiveBridge MCP server over 127.0.0.1 (random port, random
// token, registry ~/.livebridge/engine/<instance>.json with kind "host"), like the Engine.
//
// Requests: {"id", "token", "cmd": "hello" | "load" | "params" | "set" | "editor", "args"}
// Answers:  {"id", "ok": true, "result"} or {"id", "ok": false, "error": {"type", "message"}}
//
// vst~ (with valuemode 2): "params" streams the parameter names out of outlet 2, one message
// per parameter; "get <n>" answers "<n> <normalized value> <display text>" out of outlet 3, and
// "get -4" the parameter count, "get -7" 1 for an instrument. Parameter indices are 1-based; a
// list "<n> <value>" sets one. The patch tags every message outlet ("vstout <outlet> ...").
// Internally names[] is 0-based; ids ("h<n>") are vst~'s own 1-based indices.
// LIVEBRIDGE_HOST_FAKE=1 swaps vst~ for a small simulated plug-in (tests).

"use strict";

const crypto = require("crypto");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");

let maxApi = null;
try {
  maxApi = require("max-api");
} catch (error) {
  maxApi = null;
}

const NAME = "LiveBridge Host";
const VERSION = "0.3.1";
const PROTOCOL = 1;
const MAX_LINE = 1024 * 1024;
const NAMES_OUTLET = 2;
const VALUES_OUTLET = 3;
const REPLY_MS = 60;            // how long a vst~ answer may take to arrive
const LIST_MS = 600;            // "params" on a big plug-in
const SAMPLES = 33;             // values tried when setting by display text
const MIDI_RUN = 128;           // the per-CC stand-in parameters VST3 plug-ins add
const MIDI_NAME = /^(midi\s*)?(cc\s*\d+|cc\b)|pitch\s*bend|after\s*touch|channel\s*pressure/i;

// The device's config: "@args lbhost:<hex JSON>" on node.script (LIVEBRIDGE_HOST_CONFIG in
// tests): {kind, autoload?: {file, sub, name}, bank?: [{name, index}]}.
function readConfig() {
  const token = process.argv.find((a) => String(a).startsWith("lbhost:")) ||
    process.env.LIVEBRIDGE_HOST_CONFIG || "";
  if (!token) return {};
  try {
    const hex = String(token).replace(/^lbhost:/, "");
    return JSON.parse(Buffer.from(hex, "hex").toString("utf8"));
  } catch (error) {
    return {};
  }
}
const config = readConfig();

const state = {
  instance: crypto.randomBytes(6).toString("hex"),
  token: crypto.randomBytes(24).toString("hex"),
  role: "unknown",
  track: "",
  kind: config.kind || process.env.LIVEBRIDGE_HOST_KIND || "effect",
  bank: new Map((config.bank || []).map((e) => [Number(e.index), e.name])),
  started: new Date().toISOString(),
  plugin: null,           // {file, name, synth}
  names: [],              // every parameter name, vst~ order
  hidden: null,           // [from, to) run of MIDI stand-in parameters
  port: null,
  busy: Promise.resolve(),
  snapshots: new Map(),   // name -> {index: {value, text}}
};

function registryDir() {
  return process.env.LIVEBRIDGE_ENGINE_DIR || path.join(os.homedir(), ".livebridge", "engine");
}

function registryFile() {
  return path.join(registryDir(), state.instance + ".json");
}

function log(text) {
  if (maxApi) maxApi.outlet("log", text);
  else process.stderr.write(text + "\n");
}

function status(text) {
  if (maxApi) maxApi.outlet("status", text);
}

function writeRegistry() {
  if (state.port === null) return;
  const record = {
    name: NAME, kind: "host", host_kind: state.kind, version: VERSION, protocol: PROTOCOL,
    instance: state.instance, pid: process.pid, host: "127.0.0.1", port: state.port,
    token: state.token, role: state.role, track: state.track, started: state.started,
    plugin: state.plugin ? state.plugin.name : null, bank: state.bank.size,
  };
  try {
    fs.mkdirSync(registryDir(), { recursive: true });
    const temporary = registryFile() + ".tmp";
    fs.writeFileSync(temporary, JSON.stringify(record), { mode: 0o600 });
    fs.renameSync(temporary, registryFile());
  } catch (error) {
    log("cannot write " + registryFile() + ": " + error.message);
  }
}

function removeRegistry() {
  try { fs.unlinkSync(registryFile()); } catch (error) { /* gone */ }
}

// ---------------------------------------------------------------------------- talking to vst~

// The real driver: send a message to vst~, collect what comes back for `waitMs`.
// With `until`, the answer resolves as soon as a matching message arrives (vst~ answers "get"
// at once), so reading hundreds of parameters takes seconds, not hundreds of timeouts.
let pending = null;
function vstReply(...atoms) {
  if (pending) {
    pending.collected.push(atoms);                     // [outlet, ...message]
    if (pending.until && pending.until(atoms)) {
      pending.finish();
      return;
    }
  }
  noticeChange(atoms);
}

// ---------------------------------------------------------------------------- touch log
// vst~ reports a parameter change made in the plug-in's own window as "<n> <value> <text>" out
// of the values outlet, unasked. Those are the user's touches: logged with their time, their
// value before (the last one LiveBridge saw) and after. Echoes of LiveBridge's own writes are
// left out.
const TOUCH_LIMIT = 2000;
const ECHO_MS = 400;
const touches = new Map();     // index -> {first, last, count, before, value, text}
const known = new Map();       // index -> {value, text}: the last value LiveBridge read or set
const ownWrites = new Map();   // index -> ms of LiveBridge's last write
let quietUntil = 0;            // a preset or plug-in load is reporting every value: not touches

function quiet(ms) {
  quietUntil = Math.max(quietUntil, Date.now() + ms);
}

function quietEnds() {                 // the load is done: stragglers for a moment, then listen
  quietUntil = Date.now() + 300;
}

function noticeChange(atoms) {
  if (Number(atoms[0]) !== VALUES_OUTLET || atoms.length < 3) return;
  const n = Number(atoms[1]);
  if (!Number.isInteger(n) || n < 1 || typeof atoms[2] !== "number") return;
  const index = n - 1;
  const now = Date.now();
  const value = atoms[2];
  const text = atoms.slice(3).map(String).join(" ").trim();
  const echo = now < quietUntil ||
    (ownWrites.has(index) && now - ownWrites.get(index) < ECHO_MS);
  const was = known.get(index);
  known.set(index, { value: value, text: text });
  if (echo || (was && Math.abs(was.value - value) < 1e-7 && was.text === text)) return;
  const entry = touches.get(index) || { first: now, count: 0, before: was ? was.text : null,
                                        before_value: was ? was.value : null };
  touches.delete(index);                               // keep the map in order of last touch
  touches.set(index, Object.assign(entry, { last: now, count: entry.count + 1,
                                            value: value, text: text }));
  while (touches.size > TOUCH_LIMIT) touches.delete(touches.keys().next().value);
  follow(index, value);                                // a bank dial follows the window
}

function touched(args) {
  const since = Number(args.seconds) > 0 ? Date.now() - Number(args.seconds) * 1000 : 0;
  const list = [];
  for (const [index, entry] of touches) {
    if (entry.last < since) continue;
    list.push({ id: "h" + (index + 1), name: state.names[index] || null,
                before: entry.before, after: entry.text, value: entry.value,
                times: entry.count, first: new Date(entry.first).toISOString(),
                last: new Date(entry.last).toISOString() });
  }
  list.sort((a, b) => a.first.localeCompare(b.first));
  if (args.clear) touches.clear();
  return { plugin: state.plugin ? state.plugin.name : null, touched: list.length,
           parameters: list,
           ...(args.clear ? { cleared: true } : {}) };
}

function realAsk(message, waitMs, until) {
  return new Promise((resolve) => {
    const entry = { collected: [], until: until, timer: null, finish: null };
    entry.finish = () => {
      clearTimeout(entry.timer);
      if (pending === entry) pending = null;
      resolve(entry.collected);
    };
    pending = entry;
    entry.timer = setTimeout(entry.finish, waitMs);
    maxApi.outlet("vst", ...message);
  });
}

// A simulated plug-in for tests: 3 real parameters, a block of MIDI CC stand-ins, one more.
const fake = {
  loaded: null,
  params: [],
  values: [],
  load(file) {
    this.loaded = file;
    this.params = ["Cutoff", "Resonance", "Mode"];
    for (let i = 0; i < 130; i++) this.params.push("MIDI CC " + i);
    this.params.push("Volume");
    this.values = this.params.map(() => 0.5);
  },
  text(i) {
    const v = this.values[i];
    const name = this.params[i];
    if (name === "Cutoff") return Math.round(20 * Math.pow(1000, v)) + " Hz";
    if (name === "Mode") return ["LP", "BP", "HP"][Math.min(2, Math.floor(v * 3))];
    if (name === "Volume") return (v * 60 - 54).toFixed(1) + " dB";
    return Math.round(v * 100) + " %";
  },
};

function fakeAsk(message) {
  const [head, ...rest] = message;
  const out = (...atoms) => [VALUES_OUTLET, ...atoms];
  if (head === "plug_vst3") { fake.load(String(rest[0])); return Promise.resolve([]); }
  if (head === "write") {                      // a .vstpreset with a class ID (tests)
    const cid = process.env.LIVEBRIDGE_HOST_FAKE_CID || "FA".repeat(16);
    fs.writeFileSync(String(rest[0]), Buffer.concat([Buffer.from("VST3"),
      Buffer.from([1, 0, 0, 0]), Buffer.from(cid), Buffer.from(JSON.stringify(fake.values))]));
    return Promise.resolve([]);
  }
  if (head === "read") {                       // a preset: every value moves (tests)
    if (/\.vstpreset$/i.test(String(rest[0]))) fake.values = fake.values.map(() => 0.25);
    return Promise.resolve([]);
  }
  if (head === "params") return Promise.resolve(fake.params.map((n) => [NAMES_OUTLET, n]));
  if (head === "get") {
    const n = Number(rest[0]);
    if (n === -4) return Promise.resolve([out(-4, fake.params.length)]);
    if (n === -7) return Promise.resolve([out(-7, fake.loaded && /synth/i.test(fake.loaded) ? 1 : 0)]);
    if (n === -1) return Promise.resolve(fake.loaded ? [out(-1, "Fake", "Synth")] : []);
    return Promise.resolve([out(n, fake.values[n - 1], ...fake.text(n - 1).split(" "))]);
  }
  if (typeof head === "number") { fake.values[head - 1] = Math.max(0, Math.min(1, rest[0])); }
  return Promise.resolve([]);
}

// tests: a plug-in vst~ restored from the Set before the script started
if (process.env.LIVEBRIDGE_HOST_FAKE === "1" && process.env.LIVEBRIDGE_HOST_FAKE_PRELOAD) {
  fake.load(process.env.LIVEBRIDGE_HOST_FAKE_PRELOAD);
}

function ask(message, waitMs = REPLY_MS, until = null) {
  if (process.env.LIVEBRIDGE_HOST_FAKE === "1") return fakeAsk(message);
  if (!maxApi) return Promise.resolve([]);
  return realAsk(message, waitMs, until);
}

// "<n> ..." out of the values outlet
function answers(n) {
  return (atoms) => Number(atoms[0]) === VALUES_OUTLET && Number(atoms[1]) === n;
}

// One request at a time: replies are matched by timing.
function serial(fn) {
  const run = state.busy.then(fn, fn);
  state.busy = run.catch(() => {});
  return run;
}

// ---------------------------------------------------------------------------- parameters

function hideMidiRun(names) {
  let best = null;
  let from = -1;
  for (let i = 0; i <= names.length; i++) {
    const midi = i < names.length && MIDI_NAME.test(names[i]);
    if (midi && from < 0) from = i;
    if (!midi && from >= 0) {
      if (!best || i - from > best[1] - best[0]) best = [from, i];
      from = -1;
    }
  }
  return best && best[1] - best[0] >= MIDI_RUN ? best : null;
}

// A message from outlet `outlet` (or from any outlet when that one never answered).
function fromOutlet(replies, outlet) {
  const tagged = replies.filter((r) => Number(r[0]) === outlet);
  return (tagged.length ? tagged : replies).map((r) => r.slice(1));
}

async function infoValue(code) {
  for (const atoms of fromOutlet(await ask(["get", code], REPLY_MS, answers(code)),
                                 VALUES_OUTLET)) {
    if (Number(atoms[0]) === code && atoms.length > 1) return Number(atoms[1]);
  }
  return null;
}

// Names arrive one message per parameter; should a plug-in send several names in one message,
// the count from "get -4" tells us to take every atom as a name instead.
async function listNames() {
  const count = await infoValue(-4);
  const messages = fromOutlet(await ask(["params"], LIST_MS), NAMES_OUTLET)
    .filter((atoms) => atoms.length);
  const atoms = messages.reduce((all, m) => all.concat(m), []);
  const perAtom = count && messages.length !== count && atoms.length === count;
  state.names = (perAtom ? atoms.map((a) => [a]) : messages)
    .map((m) => m.map(String).join(" ").trim());
  state.hidden = hideMidiRun(state.names);
  return state.names;
}

// Parameter `index` (0-based in names[]) is vst~'s index + 1.
async function read(index) {
  const n = index + 1;
  for (const atoms of fromOutlet(await ask(["get", n], REPLY_MS, answers(n)), VALUES_OUTLET)) {
    if (atoms.length >= 2 && Number(atoms[0]) === n && typeof atoms[1] === "number") {
      const got = { value: atoms[1], text: atoms.slice(2).map(String).join(" ").trim() };
      known.set(index, got);
      return got;
    }
  }
  return null;
}

function visible(index) {
  return !(state.hidden && index >= state.hidden[0] && index < state.hidden[1]);
}

function findIndex(key) {
  if (typeof key === "number") return key;
  const text = String(key);
  const match = /^h(\d+)$/.exec(text);
  if (match) return Number(match[1]) - 1;
  const exact = state.names.indexOf(text);
  if (exact >= 0) return exact;
  const norm = (s) => s.toLowerCase().replace(/\s+/g, "");
  const found = state.names.map((n, i) => [n, i]).filter(([n]) => norm(n) === norm(text));
  if (found.length === 1) return found[0][1];
  if (found.length > 1) throw { type: "bad_args", message: `"${text}" matches several parameters` };
  throw { type: "not_found", message: `no parameter "${text}"` };
}

function leadingNumber(text) {
  const m = /(-?\d+(?:\.\d+)?)\s*(k?)(hz|ms|s|db|%)?/i.exec(String(text).replace(",", "."));
  if (!m) return null;
  let value = Number(m[1]);
  if (m[2]) value *= 1000;
  if (m[3] && m[3].toLowerCase() === "s" && !m[2]) value *= 1000;
  return value;
}

function norm(text) {
  return String(text).toLowerCase().replace(/\s+/g, "");
}

// A bank dial follows values LiveBridge sets ("set" in the patch: no echo back to vst~).
function follow(index, value) {
  if (maxApi && state.bank.has(index + 1)) maxApi.outlet("bank", index + 1, value);
}

async function setValue(index, value) {
  ownWrites.set(index, Date.now());
  await ask([index + 1, value], 5);
  follow(index, value);
  return read(index);
}

// Find the 0..1 value whose display text reads `wanted`: try SAMPLES values for a text match,
// else bisect on the leading number between the two samples that bracket it.
async function setText(index, wanted) {
  const before = await read(index);
  const target = leadingNumber(wanted);
  const samples = [];
  for (let i = 0; i < SAMPLES; i++) {
    const value = i / (SAMPLES - 1);
    const got = await setValue(index, value);
    if (!got) break;
    const bracket = /\[([^\]]+)\]/.exec(got.text);
    if (norm(got.text) === norm(wanted) || (bracket && norm(bracket[1]) === norm(wanted))) {
      return { ok: true, value: value, text: got.text };
    }
    samples.push([value, leadingNumber(got.text)]);
  }
  if (target !== null) {
    for (let i = 0; i + 1 < samples.length; i++) {
      let [lo, a] = samples[i];
      let [hi, b] = samples[i + 1];
      if (a === null || b === null || (target - a) * (target - b) > 0) continue;
      const rising = b >= a;
      for (let step = 0; step < 20; step++) {
        const mid = (lo + hi) / 2;
        const got = await setValue(index, mid);
        const n = got ? leadingNumber(got.text) : null;
        if (n === null) break;
        if ((n < target) === rising) lo = mid; else hi = mid;
      }
      // the closest of the bracket's ends and middle wins (display steps are coarse)
      let best = null;
      for (const value of [lo, (lo + hi) / 2, hi]) {
        const got = await setValue(index, value);
        const n = got ? leadingNumber(got.text) : null;
        if (n !== null && (!best || Math.abs(n - target) < best.distance)) {
          best = { value: value, text: got.text, distance: Math.abs(n - target) };
        }
      }
      if (best) {
        const got = await setValue(index, best.value);
        return { ok: true, value: best.value, text: got ? got.text : best.text };
      }
    }
  }
  if (before) await setValue(index, before.value);
  return { ok: false, text: before ? before.text : null };
}

// ---------------------------------------------------------------------------- commands

async function load(args) {
  const file = String(args.file || "").trim();
  if (!file) throw { type: "bad_args", message: "load needs file (the VST3 file name)" };
  await ask(["valuemode", 2], 5);
  quiet(3000);
  await ask(["plug_vst3", file], 1500);
  if (args.sub) await ask(["subname", String(args.sub)], 500);
  const isSynth = (await infoValue(-7)) === 1;
  const names = await listNames();
  quietEnds();
  if (!names.length) throw { type: "not_found", message: `vst~ could not load "${file}"` };
  state.plugin = { file: file, name: String(args.name || args.sub || file), synth: isSynth };
  if (args.class_id) state.plugin.class_id = String(args.class_id);
  writeRegistry();
  status(state.plugin.name);
  // name the Live device after the plug-in: Live keeps it in the Set, adopt() reads it back
  if (maxApi && args.rename !== false) maxApi.outlet("context", "rename", state.plugin.name);
  return { plugin: state.plugin, parameters: names.length - hiddenCount(),
           hidden_midi: hiddenCount() };
}

function hiddenCount() {
  return state.hidden ? state.hidden[1] - state.hidden[0] : 0;
}

async function params(args) {
  await needPlugin();
  if (!state.names.length) await listNames();
  const search = args.search ? String(args.search).toLowerCase() : null;
  const indices = state.names.map((n, i) => i).filter((i) => visible(i) &&
    (!search || state.names[i].toLowerCase().includes(search)));
  const offset = Math.max(0, Number(args.offset) || 0);
  const limit = Math.max(1, Math.min(500, Number(args.limit) || 50));
  const page = indices.slice(offset, offset + limit);
  const items = [];
  for (const i of page) {
    const got = await read(i);
    items.push({ id: "h" + (i + 1), name: state.names[i], value: got ? got.value : null,
                 display: got ? got.text : null });
  }
  const result = { plugin: state.plugin.name, total: indices.length, offset: offset,
                   count: items.length, parameters: items };
  if (offset + limit < indices.length) result.next_offset = offset + limit;
  return result;
}

async function set(args) {
  await needPlugin();
  if (!state.names.length) await listNames();
  const values = args.values && typeof args.values === "object" ? args.values : null;
  if (!values || !Object.keys(values).length) {
    throw { type: "bad_args", message: "set needs values {name: number 0..1 | display text}" };
  }
  const results = [];
  for (const [key, wanted] of Object.entries(values)) {
    let index;
    try {
      index = findIndex(key);
    } catch (error) {
      results.push({ name: key, ok: false, error: error.message });
      continue;
    }
    if (index < 0 || index >= state.names.length) {
      results.push({ name: key, ok: false, error: "no such parameter" });
      continue;
    }
    if (typeof wanted === "number") {
      const got = await setValue(index, Math.max(0, Math.min(1, wanted)));
      results.push({ name: state.names[index], id: "h" + (index + 1), ok: true,
                     value: got ? got.value : wanted, display: got ? got.text : null });
    } else {
      const got = await setText(index, String(wanted));
      results.push(got.ok ? { name: state.names[index], id: "h" + (index + 1), ok: true,
                              value: got.value, display: got.text }
        : { name: state.names[index], id: "h" + (index + 1), ok: false,
            error: `no value displays "${wanted}"; it still reads "${got.text}"` });
    }
  }
  return { plugin: state.plugin.name, results: results,
           applied: results.filter((r) => r.ok).length };
}

// A preset file into the hosted plug-in (vst~ "read": .vstpreset for VST3). vst~ answers
// nothing, so a sample of values read before and after tells whether it took.
async function preset(args) {
  await needPlugin();
  // a host restored without its name learns it from a preset made for a known plug-in
  if (args.class_id && !state.plugin.class_id) state.plugin.class_id = String(args.class_id);
  if (args.plugin_name && state.plugin.name === "plug-in") {
    state.plugin.name = String(args.plugin_name);
    writeRegistry();
    status(state.plugin.name);
    if (maxApi) maxApi.outlet("context", "rename", state.plugin.name);
  }
  const file = String(args.path || "").replace(/\\/g, "/");
  if (!file) throw { type: "bad_args", message: "preset needs path" };
  if (!state.names.length) await listNames();
  const sample = state.names.map((n, i) => i).filter(visible).slice(0, 64);
  const before = [];
  for (const i of sample) before.push(await read(i));
  quiet(2500);
  await ask(["read", file], 1500);
  let changed = 0;
  for (let k = 0; k < sample.length; k++) {
    const got = await read(sample[k]);
    if (before[k] && got && (got.value !== before[k].value || got.text !== before[k].text)) {
      changed++;
      follow(sample[k], got.value);
    }
  }
  quietEnds();
  for (const n of state.bank.keys()) {          // bank dials outside the sample follow too
    if (!sample.includes(n - 1)) {
      const got = await read(n - 1);
      if (got) follow(n - 1, got.value);
    }
  }
  return { path: file, sampled: sample.length, changed: changed };
}

// The hosted plug-in's complete state as a preset file (vst~ "write": .vstpreset for VST3),
// so a native instance can load the same patch.
async function save(args) {
  await needPlugin();
  const file = String(args.path || "");
  if (!file) throw { type: "bad_args", message: "save needs path" };
  try { fs.unlinkSync(file); } catch (error) { /* not there yet */ }
  await ask(["write", file.replace(/\\/g, "/")], 3000,
            () => fs.existsSync(file) && fs.statSync(file).size > 0);
  for (let i = 0; i < 20 && !(fs.existsSync(file) && fs.statSync(file).size > 0); i++) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!fs.existsSync(file) || fs.statSync(file).size === 0) {
    throw { type: "invalid_state", message: "vst~ did not write " + file };
  }
  return { path: file, bytes: fs.statSync(file).size, plugin: state.plugin };
}

// What the user moved in the plug-in's window: a snapshot of every value, then the difference.
async function readAll() {
  if (!state.names.length) await listNames();
  const values = {};
  for (let i = 0; i < state.names.length; i++) {
    if (!visible(i)) continue;
    const got = await read(i);
    if (got) values[i] = got;
  }
  return values;
}

async function snapshot(args) {
  await needPlugin();
  const name = String(args.name || "default");
  state.snapshots.set(name, await readAll());
  return { snapshot: name, parameters: state.snapshots.get(name) ?
    Object.keys(state.snapshots.get(name)).length : 0 };
}

async function changes(args) {
  await needPlugin();
  const name = String(args.since || "default");
  const before = state.snapshots.get(name);
  if (!before) throw { type: "not_found", message: `no snapshot "${name}" — take one first` };
  const now = await readAll();
  const moved = [];
  for (const [key, was] of Object.entries(before)) {
    const is = now[key];
    if (is && (Math.abs(is.value - was.value) > 1e-6 || is.text !== was.text)) {
      const i = Number(key);
      moved.push({ id: "h" + (i + 1), name: state.names[i], before: was.text,
                   after: is.text, value: is.value, moved: Math.abs(is.value - was.value) });
    }
  }
  moved.sort((a, b) => b.moved - a.moved);
  return { since: name, plugin: state.plugin.name, changed: moved.length, parameters: moved };
}

// Names -> vst~ indices (for a bank), and every value at once / back (moving a sound).
async function resolve(args) {
  if (!state.names.length) await listNames();
  const out = [];
  for (const name of Array.isArray(args.names) ? args.names : []) {
    try {
      const index = findIndex(name);
      if (index < 0 || index >= state.names.length) throw { message: "no such parameter" };
      out.push({ name: state.names[index], id: "h" + (index + 1), index: index + 1 });
    } catch (error) {
      out.push({ name: String(name), error: error.message });
    }
  }
  return { parameters: out };
}

async function values() {
  await needPlugin();
  if (!state.names.length) await listNames();
  const all = [];
  for (let i = 0; i < state.names.length; i++) {
    if (!visible(i)) continue;
    const got = await read(i);
    if (got) all.push([i + 1, got.value]);
  }
  return { plugin: state.plugin, values: all };
}

async function restore(args) {
  await needPlugin();
  let count = 0;
  for (const pair of Array.isArray(args.values) ? args.values : []) {
    const n = Number(pair[0]);
    const value = Number(pair[1]);
    if (!Number.isInteger(n) || n < 1 || !Number.isFinite(value)) continue;
    ownWrites.set(n - 1, Date.now());
    await ask([n, Math.max(0, Math.min(1, value))], 0);
    follow(n - 1, value);
    count++;
  }
  return { restored: count };
}

// A bank device loads its plug-in the first time it runs; after that vst~ restores it from
// the Set (a parameter count already there means: leave it alone).
// After a Set is reopened (or the script restarted), vst~ already holds the plug-in and its
// state: adopt it — names, kind, status — without loading anything. The plug-in's name comes
// from the bank config, else from what vst~ reports for its info indices, else "plug-in".
async function adopt() {
  if (state.plugin) return true;
  const present = await infoValue(-4);
  if (!present || present <= 0) return false;
  await listNames();
  let name = config.autoload ? (config.autoload.name || config.autoload.file) : null;
  if (!name) {
    const saved = await deviceName();          // the name load() gave the device
    if (saved && !GENERIC_NAMES.includes(saved)) name = saved;
  }
  state.plugin = { file: config.autoload ? config.autoload.file : null, name: name || "plug-in",
                   synth: (await infoValue(-7)) === 1, restored: true };
  writeRegistry();
  status(state.plugin.name);
  return true;
}

// The Live device's own name (context.js reads it; see load(): it is the plug-in's name).
const GENERIC_NAMES = ["LiveBridge Host Instrument", "LiveBridge Host Effect"];
let nameWaiter = null;
function deviceName() {
  if (!maxApi) return Promise.resolve(null);
  return new Promise((resolve) => {
    nameWaiter = resolve;
    maxApi.outlet("context", "devicename");
    setTimeout(() => { if (nameWaiter === resolve) { nameWaiter = null; resolve(null); } }, 500);
  });
}

async function needPlugin() {
  if (!state.plugin && !(await adopt())) {
    throw { type: "invalid_state", message: "no plug-in loaded" };
  }
}

async function autoload() {
  if (await adopt()) return;
  if (!config.autoload) {
    status("ready — no plug-in");
    return;
  }
  try {
    await load(config.autoload);
  } catch (error) {
    status("could not load " + (config.autoload.name || config.autoload.file));
    log("autoload failed: " + (error.message || error));
  }
}

function handle(request, reply) {
  const args = request.args && typeof request.args === "object" ? request.args : {};
  const done = (promise) => promise.then((r) => reply(null, r),
    (e) => reply(e && e.type ? e : { type: "internal", message: String(e && e.message || e) }));
  if (request.cmd === "hello") {
    reply(null, { name: NAME, version: VERSION, protocol: PROTOCOL, role: state.role,
                  track: state.track, host_kind: state.kind, plugin: state.plugin,
                  bank: [...state.bank.values()],
                  started: state.started });
  } else if (request.cmd === "load") {
    done(serial(() => load(args)));
  } else if (request.cmd === "params") {
    done(serial(() => params(args)));
  } else if (request.cmd === "set") {
    done(serial(() => set(args)));
  } else if (request.cmd === "preset") {
    done(serial(() => preset(args)));
  } else if (request.cmd === "touched") {
    reply(null, touched(args));
  } else if (request.cmd === "save") {
    done(serial(() => save(args)));
  } else if (request.cmd === "snapshot") {
    done(serial(() => snapshot(args)));
  } else if (request.cmd === "changes") {
    done(serial(() => changes(args)));
  } else if (request.cmd === "resolve") {
    done(serial(() => resolve(args)));
  } else if (request.cmd === "values") {
    done(serial(values));
  } else if (request.cmd === "restore") {
    done(serial(() => restore(args)));
  } else if (request.cmd === "editor") {
    done(serial(async () => { await ask([args.open === false ? "wclose" : "open"], 5);
                              return { open: args.open !== false }; }));
  } else {
    reply({ type: "unknown_command", message: "unknown command " + JSON.stringify(request.cmd) });
  }
}

function serve(socket) {
  socket.setEncoding("utf8");
  let buffer = "";
  const reply = (payload) => { if (!socket.destroyed) socket.write(JSON.stringify(payload) + "\n"); };
  socket.on("data", (chunk) => {
    buffer += chunk;
    if (buffer.length > MAX_LINE && buffer.indexOf("\n") < 0) {
      reply({ id: null, ok: false, error: { type: "bad_request", message: "line too long" } });
      socket.destroy();
      return;
    }
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      let request;
      try {
        request = JSON.parse(line);
      } catch (error) {
        reply({ id: null, ok: false, error: { type: "bad_request", message: "not JSON" } });
        continue;
      }
      const id = request && request.id !== undefined ? request.id : null;
      if (!request || request.token !== state.token) {
        reply({ id: id, ok: false, error: { type: "auth", message: "wrong or missing token" } });
        continue;
      }
      handle(request, (error, result) => {
        reply(error ? { id: id, ok: false, error: error } : { id: id, ok: true, result: result });
      });
    }
  });
  socket.on("error", () => {});
}

function setContext(role, trackParts) {
  state.role = role === "master" ? "master" : "track";
  state.track = trackParts.join(" ");
  writeRegistry();
}

function askForContext(attempt = 0) {
  if (!maxApi || state.role !== "unknown" || attempt >= 30) return;
  maxApi.outlet("context");
  setTimeout(() => askForContext(attempt + 1), 2000).unref();
}

function start() {
  const server = net.createServer(serve);
  server.on("error", (error) => { log("server error: " + error.message); });
  server.listen(0, "127.0.0.1", () => {
    state.port = server.address().port;
    writeRegistry();
    log(NAME + " " + VERSION + " listening on 127.0.0.1:" + state.port);
    status("starting...");
    askForContext();
    // vst~ restores a saved plug-in while the device loads: look for it a moment later
    setTimeout(() => serial(autoload), 1000).unref();
  });
  const stop = () => { removeRegistry(); process.exit(0); };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  process.on("exit", removeRegistry);
  if (maxApi) {
    maxApi.addHandler("vstout", vstReply);
    maxApi.addHandler("context", (role, ...track) => setContext(String(role), track.map(String)));
    maxApi.addHandler("transport", () => {});
    maxApi.addHandler("resolved", () => {});
    maxApi.addHandler("info", () => {});
    maxApi.addHandler("devicename", (...words) => {
      const waiter = nameWaiter;
      nameWaiter = null;
      if (waiter) waiter(words.map(String).join(" ").trim());
    });
  }
  if (maxApi) {
    maxApi.outlet("ready");          // opens the patch's gate: messages may come in now
  }
}

start();

module.exports = { hideMidiRun, leadingNumber, state };
