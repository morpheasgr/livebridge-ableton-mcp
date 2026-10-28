// LiveBridge Engine — the Node for Max part of the device.
//
// The patch measures the audio passing through the device (gen~, lb_engine.gendsp) and sends
// a "frame" of 18 meter values ten times a second; this script keeps the last minute of
// frames and answers the LiveBridge MCP server over a local TCP connection (127.0.0.1 only,
// JSON lines, a random token). It announces itself with a small registry file in
// ~/.livebridge/engine/<instance>.json (port, token, role, track), removed when the device
// goes away, so the MCP server finds every Engine without fixed ports.
//
// It also records the audio passing through it to a WAV file on request ("capture": the
// patch's sfrecord~, driven through the "capture" outlet), starting at a given wall-clock
// time so several devices record the same moment.
//
// And it modulates Live parameters ("modulate" / "unmodulate"): up to 8 at a time, each through
// one of the patch's live.remote~ objects (no automation, no undo steps, nothing saved with the
// set), with LFO shapes or step sequences synced to Live's song position. context.js turns a
// parameter's path into the id live.remote~ needs and reports the transport.
//
// And it plays follow actions ("follow" / "unfollow"): every N beats of Live's song position,
// a rule picks the next clip of a track (or the next scene) by weighted actions — next,
// previous, first, last, any, other, again, stop, jump:N — and fires it just before the
// boundary, so Live's launch quantization lands it on the grid. Live's Python API has no follow
// actions; these run here and are not saved with the set.
//
// Finally it is the fast writer and reader of LiveBridge's arrangement automation passes:
// "automate" — while the Remote Script runs a recording pass (Arrangement Record, playback,
// often at a raised tempo) it moves each lane's parameter along its curve 50 times a second,
// five times the Remote Script's tick, with begin/end gesture so Live records one touch;
// "sample" — while the Remote Script plays a range without recording, it reads the
// parameters 50 times a second (their arrangement automation playing back).
//
// Requests: {"id": 1, "token": "...", "cmd": "hello" | "frames" | "capture" | "modulate" |
//            "unmodulate" | "follow" | "unfollow" | "automate", "args": {...}}
// Answers:  {"id": 1, "ok": true, "result": {...}} or {"id": 1, "ok": false,
//           "error": {"type": "bad_args", "message": "..."}}

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
  maxApi = null; // run outside Max (tests): no patch to talk to
}

const NAME = "LiveBridge Engine";
const VERSION = "1.5.2";
const PROTOCOL = 1;
// The 18 values of a frame, in the order of the gen~ outlets (mean squares are linear power,
// smoothed over ~100 ms; msk over ~400 ms; peaks decay over ~300 ms).
const FRAME_FIELDS = [
  "ms_left", "ms_right", "peak_left", "peak_right", "ms_k",
  "band_sub", "band_bass", "band_low_mid", "band_mud", "band_mid", "band_presence",
  "band_high", "band_air", "ms_mid", "ms_side", "mean_lr", "ms_mid_low", "ms_side_low",
];
const HISTORY_SECONDS = 60;
const MAX_WAIT_SECONDS = 30;
const MAX_LINE = 64 * 1024;
const MAX_CAPTURE_SECONDS = 60;
const MAX_CAPTURE_DELAY_MS = 5000;
const CAPTURE_NAME = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,119}\.wav$/;
const MOD_SLOTS = 8;                  // live.remote~ objects in the patch
// Unmodulate: let go of a parameter this long after sending its original value (several
// audio vectors even at large buffer sizes), then write the original once more.
const RELEASE_DELAY_MS = 80;
const RESTORE_WRITE_MS = 30;
const MOD_TICK_MS = 20;               // 50 updates a second, each glided over by line~
const MOD_RESOLVE_MS = 3000;
const MAX_PATH = /^live_set( [a-z_]+( \d+)?){1,12}$/;
const MAX_AUTOMATE_POINTS = 100000;
const MAX_LANES = 64;
const AUTOMATE_TIMEOUT_MS = 15 * 60 * 1000;
const FOLLOW_ACTIONS = ["next", "previous", "first", "last", "any", "other", "again", "stop"];
const MAX_RULES = 16;
const FOLLOW_QUERY_MS = 1000;
const FOLLOW_HISTORY = 16;
const SHAPES = ["sine", "triangle", "saw", "ramp_down", "square", "random", "smooth_random",
  "steps"];

const state = {
  instance: crypto.randomBytes(6).toString("hex"),
  token: crypto.randomBytes(24).toString("hex"),
  role: "unknown",
  track: "",
  started: new Date().toISOString(),
  frames: [], // [timestamp_ms, ...values]
  waiters: new Set(),
  port: null,
  capturing: false,
  mods: new Map(),                    // slot -> modulation
  resolving: new Map(),               // slot -> callback waiting for context.js
  transport: null,                    // {beat, tempo, playing, at}
  rules: new Map(),                   // follow-action target path -> rule
  queries: new Map(),                 // query id -> callback waiting for context.js
  automation: null,                   // the running "automate" pass
  lastAutomation: null,
  sampling: null,                     // the running or finished "sample" pass
  nextQuery: 1,
  ticker: null,
};

function registryDir() {
  return process.env.LIVEBRIDGE_ENGINE_DIR || path.join(os.homedir(), ".livebridge", "engine");
}

// Captures land here; the MCP server reads them from the same folder.
function captureDir() {
  return process.env.LIVEBRIDGE_CAPTURE_DIR || path.join(os.homedir(), ".livebridge", "captures");
}

function registryFile() {
  return path.join(registryDir(), state.instance + ".json");
}

function log(text) {
  if (maxApi) {
    maxApi.outlet("log", text);
  } else {
    process.stderr.write(text + "\n");
  }
}

function status(text) {
  if (maxApi) {
    maxApi.outlet("status", text);
  }
}

function writeRegistry() {
  if (state.port === null) {
    return;
  }
  const record = {
    name: NAME, version: VERSION, protocol: PROTOCOL, instance: state.instance,
    pid: process.pid, host: "127.0.0.1", port: state.port, token: state.token,
    role: state.role, track: state.track, started: state.started,
  };
  try {
    fs.mkdirSync(registryDir(), { recursive: true });
    const temp = registryFile() + ".tmp";
    fs.writeFileSync(temp, JSON.stringify(record, null, 1), { mode: 0o600 });
    fs.renameSync(temp, registryFile());
  } catch (error) {
    log("could not write " + registryFile() + ": " + error.message);
  }
}

function removeRegistry() {
  try {
    fs.unlinkSync(registryFile());
  } catch (error) {
    // already gone
  }
}

// ---------------------------------------------------------------------------- frames

function addFrame(values) {
  if (values.length !== FRAME_FIELDS.length) {
    return;
  }
  const numbers = values.map((v) => (Number.isFinite(Number(v)) ? Number(v) : 0));
  const frame = [Date.now()].concat(numbers);
  state.frames.push(frame);
  const oldest = frame[0] - HISTORY_SECONDS * 1000;
  while (state.frames.length && state.frames[0][0] < oldest) {
    state.frames.shift();
  }
  for (const waiter of state.waiters) {
    if (frame[0] >= waiter.from) {
      waiter.collected.push(frame);
    }
  }
}

function frameInterval() {
  const recent = state.frames.slice(-11);
  if (recent.length < 2) {
    return null;
  }
  return Math.round((recent[recent.length - 1][0] - recent[0][0]) / (recent.length - 1));
}

function setContext(role, trackParts) {
  state.role = role === "master" ? "master" : "track";
  state.track = trackParts.join(" ");
  status(state.role === "master" ? "master" : "track: " + state.track);
  writeRegistry();
}

// ---------------------------------------------------------------------------- requests

function hello() {
  return {
    name: NAME, version: VERSION, protocol: PROTOCOL, instance: state.instance,
    role: state.role, track: state.track, started: state.started, fields: FRAME_FIELDS,
    frames_buffered: state.frames.length, frame_interval_ms: frameInterval(),
    history_seconds: HISTORY_SECONDS, capture: Boolean(maxApi), capturing: state.capturing,
    modulations: [...state.mods.values()].map(describeMod), mod_slots: MOD_SLOTS,
    follow: [...state.rules.values()].map(describeRule),
    automation: describeAutomation(state.automation || state.lastAutomation),
    sampling: state.sampling ? { phase: state.sampling.phase,
                                 reads: state.sampling.reads } : null,
  };
}

// Record `seconds` of the device's audio to <captureDir>/<name>, starting at `at` (epoch ms,
// default now). Answers once the file is closed.
function capture(args, reply) {
  const seconds = Number(args.seconds);
  const name = String(args.name || "");
  const at = args.at === undefined ? Date.now() : Number(args.at);
  if (!Number.isFinite(seconds) || seconds <= 0 || seconds > MAX_CAPTURE_SECONDS) {
    reply({ type: "bad_args", message: "seconds must be > 0 and <= " + MAX_CAPTURE_SECONDS });
    return;
  }
  if (!CAPTURE_NAME.test(name)) {
    reply({ type: "bad_args", message: "name must be a plain file name ending in .wav" });
    return;
  }
  const delay = at - Date.now();
  if (!Number.isFinite(delay) || delay > MAX_CAPTURE_DELAY_MS) {
    reply({ type: "bad_args", message: "at must be within " + MAX_CAPTURE_DELAY_MS + " ms" });
    return;
  }
  if (!maxApi) {
    reply({ type: "unsupported", message: "capturing needs the device running in Live" });
    return;
  }
  if (state.capturing) {
    reply({ type: "busy", message: "this device is already capturing" });
    return;
  }
  const folder = captureDir();
  try {
    fs.mkdirSync(folder, { recursive: true });
  } catch (error) {
    reply({ type: "io", message: "cannot create " + folder + ": " + error.message });
    return;
  }
  const file = path.join(folder, name);
  state.capturing = true;
  // Max takes forward slashes on Windows too ("C:/Users/...")
  maxApi.outlet("capture", "open", file.split(path.sep).join("/"));
  let started = null;
  setTimeout(() => {
    started = Date.now();
    maxApi.outlet("capture", 1);
    setTimeout(() => {
      maxApi.outlet("capture", 0);
      const stopped = Date.now();
      waitForFile(file, 40, () => {
        state.capturing = false;
        let size = 0;
        try {
          size = fs.statSync(file).size;
        } catch (error) {
          size = 0;
        }
        if (size <= 44) {
          reply({ type: "capture_failed", message: "the device wrote no audio to " + file });
          return;
        }
        reply(null, {
          file_path: file, started: started, seconds: (stopped - started) / 1000,
          role: state.role, track: state.track, bytes: size,
        });
      });
    }, seconds * 1000);
  }, Math.max(0, delay));
}

// ---------------------------------------------------------------------------- modulation

function describeMod(mod) {
  return {
    slot: mod.slot, path: mod.path, name: mod.name, shape: mod.shape, beats: mod.beats,
    hz: mod.hz, low: mod.low, high: mod.high, phase: mod.phase, min: mod.min, max: mod.max,
    original: mod.original, started: mod.started,
  };
}

function setTransport(beat, tempo, playing) {
  state.transport = {
    beat: Number(beat) || 0, tempo: Number(tempo) || 120, playing: Number(playing) === 1,
    at: Date.now(),
  };
}

// Song position in beats: Live's while playing (extrapolated between reports), otherwise a
// free-running clock at the song tempo from when the modulation started.
function beatsFor(mod, now) {
  const t = state.transport;
  const tempo = t ? t.tempo : 120;
  if (t && t.playing) {
    return t.beat + (now - t.at) / 60000 * tempo;
  }
  return (now - mod.started) / 60000 * tempo;
}

// A repeatable pseudo-random value in 0..1 for cycle n.
function noise(seed, n) {
  let x = (Math.imul(n | 0, 2654435761) ^ Math.imul(seed | 0, 1597334677)) >>> 0;
  x = Math.imul(x ^ (x >>> 15), 2246822519) >>> 0;
  x = Math.imul(x ^ (x >>> 13), 3266489917) >>> 0;
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}

// The shape's value (0..1) at a position measured in cycles.
function shapeAt(mod, cycles) {
  const n = Math.floor(cycles);
  const phase = cycles - n;
  switch (mod.shape) {
    case "sine": return 0.5 + 0.5 * Math.sin(2 * Math.PI * phase);
    case "triangle": return 1 - Math.abs(2 * phase - 1);
    case "saw": return phase;
    case "ramp_down": return 1 - phase;
    case "square": return phase < 0.5 ? 1 : 0;
    case "random": return noise(mod.seed, n);
    case "smooth_random": {
      const a = noise(mod.seed, n);
      const b = noise(mod.seed, n + 1);
      return a + (b - a) * (0.5 - 0.5 * Math.cos(Math.PI * phase));
    }
    case "steps": return mod.steps[Math.min(mod.steps.length - 1,
      Math.floor(phase * mod.steps.length))];
    default: return 0;
  }
}

function modValue(mod, now) {
  const cycles = (mod.hz ? (now - mod.started) / 1000 * mod.hz
    : beatsFor(mod, now) / mod.beats) + mod.phase;
  const amount = mod.low + (mod.high - mod.low) * shapeAt(mod, cycles);
  return mod.min + (mod.max - mod.min) * amount;
}

function tick() {
  const now = Date.now();
  for (const rule of state.rules.values()) {
    followTick(rule, now);
  }
  if (state.automation) {
    automateTick(state.automation, now);
  }
  if (state.sampling && state.sampling.phase !== "done") {
    sampleTick(state.sampling, now);
  }
  for (const mod of state.mods.values()) {
    const value = modValue(mod, now);
    if (mod.last === null || Math.abs(value - mod.last) > (mod.max - mod.min) * 1e-5) {
      mod.last = value;
      maxApi.outlet("mod", mod.slot, "value", value, MOD_TICK_MS);   // line~ glides there
    }
  }
}

function startTicker() {
  if (state.ticker === null && (state.mods.size || state.rules.size || state.automation ||
                                (state.sampling && state.sampling.phase !== "done"))) {
    state.ticker = setInterval(tick, MOD_TICK_MS);
  }
}

function stopTickerIfIdle() {
  if (state.ticker !== null && !state.mods.size && !state.rules.size && !state.automation &&
      !(state.sampling && state.sampling.phase !== "done")) {
    clearInterval(state.ticker);
    state.ticker = null;
  }
}

function release(mod) {
  // Back to the value the parameter had, then let go of it. live.remote~ reads its signal
  // once per audio vector, so letting go at once would leave the LFO's last value: send the
  // original, release a few vectors later, then write the original through the LiveAPI too
  // (the knob is a normal parameter again by then).
  state.mods.delete(mod.slot);
  stopTickerIfIdle();
  maxApi.outlet("mod", mod.slot, "value", mod.original);
  setTimeout(() => {
    if (!state.mods.has(mod.slot)) {
      maxApi.outlet("mod", mod.slot, "id", 0);
    }
    setTimeout(() => {
      maxApi.outlet("setvalue", mod.original, ...atoms(mod.path));
    }, RESTORE_WRITE_MS);
  }, RELEASE_DELAY_MS);
}

function parseModulation(args) {
  const shape = String(args.shape || "sine");
  if (SHAPES.indexOf(shape) < 0) {
    return { error: "shape must be one of " + SHAPES.join(", ") };
  }
  const beats = args.beats === undefined || args.beats === null ? null : Number(args.beats);
  const hz = args.hz === undefined || args.hz === null ? null : Number(args.hz);
  if ((beats === null) === (hz === null)) {
    return { error: "give beats or hz" };
  }
  if (beats !== null && !(beats >= 1 / 64 && beats <= 256)) {
    return { error: "beats must be 1/64..256" };
  }
  if (hz !== null && !(hz > 0 && hz <= 40)) {
    return { error: "hz must be > 0 and <= 40" };
  }
  const low = Number(args.low === undefined ? 0 : args.low);
  const high = Number(args.high === undefined ? 1 : args.high);
  if (!(low >= 0 && low <= 1 && high >= 0 && high <= 1)) {
    return { error: "low and high must be 0..1" };
  }
  const phase = Number(args.phase || 0);
  if (!(phase >= 0 && phase < 1)) {
    return { error: "phase must be 0..1" };
  }
  let steps = null;
  if (shape === "steps") {
    steps = Array.isArray(args.steps) ? args.steps.map(Number) : [];
    if (!steps.length || steps.length > 64 || steps.some((v) => !(v >= 0 && v <= 1))) {
      return { error: "steps: 1..64 values 0..1" };
    }
  }
  return {
    shape: shape, beats: beats, hz: hz, low: low, high: high, phase: phase, steps: steps,
    seed: Number(args.seed || 0) | 0,
  };
}

function modulate(args, reply) {
  const target = String(args.path || "");
  if (!MAX_PATH.test(target)) {
    reply({ type: "bad_args", message: "path must be a Live object path like 'live_set tracks 0 "
      + "devices 1 parameters 3'" });
    return;
  }
  const spec = parseModulation(args);
  if (spec.error) {
    reply({ type: "bad_args", message: spec.error });
    return;
  }
  if (!maxApi) {
    reply({ type: "unsupported", message: "modulating needs the device running in Live" });
    return;
  }
  let slot = null;
  for (const mod of state.mods.values()) {
    if (mod.path === target) {
      slot = mod.slot;                    // the same parameter again: replace its modulation
    }
  }
  if (slot === null) {
    for (let i = 0; i < MOD_SLOTS && slot === null; i++) {
      if (!state.mods.has(i) && !state.resolving.has(i)) {
        slot = i;
      }
    }
  }
  if (slot === null) {
    reply({ type: "busy", message: "all " + MOD_SLOTS + " modulation slots of this device are "
      + "in use — stop one, or use another LiveBridge Engine" });
    return;
  }
  const existing = state.mods.get(slot);
  if (existing) {
    Object.assign(existing, spec, { started: Date.now(), last: null });
    reply(null, describeMod(existing));
    return;
  }
  const timer = setTimeout(() => {
    state.resolving.delete(slot);
    reply({ type: "timeout", message: "Live did not answer for " + target });
  }, MOD_RESOLVE_MS);
  state.resolving.set(slot, (found) => {
    clearTimeout(timer);
    state.resolving.delete(slot);
    if (!found.id) {
      reply({ type: "not_found", message: "no parameter at " + target });
      return;
    }
    const mod = Object.assign({
      slot: slot, path: target, id: found.id, name: found.name, min: found.min,
      max: found.max, original: found.value, started: Date.now(), last: null,
    }, spec);
    state.mods.set(slot, mod);
    // jump line~ to the first value before live.remote~ takes the parameter over
    mod.last = modValue(mod, Date.now());
    maxApi.outlet("mod", slot, "value", mod.last);
    maxApi.outlet("mod", slot, "id", mod.id);
    startTicker();
    reply(null, describeMod(mod));
  });
  maxApi.outlet("resolve", slot, ...target.split(" ").map((part) =>
    (/^\d+$/.test(part) ? Number(part) : part)));
}

// context.js answer: resolved <slot> <id> <min> <max> <value> <name...>
function resolved(slot, id, min, max, value, ...name) {
  const waiting = state.resolving.get(Number(slot));
  if (waiting) {
    waiting({
      id: Number(id) || 0, min: Number(min), max: Number(max), value: Number(value),
      name: name.join(" "),
    });
  }
}

function unmodulate(args, reply) {
  const stopped = [];
  for (const mod of [...state.mods.values()]) {
    if (args.all || mod.path === args.path) {
      stopped.push(describeMod(mod));
      if (maxApi) {
        release(mod);
      } else {
        state.mods.delete(mod.slot);
      }
    }
  }
  stopTickerIfIdle();
  reply(null, { stopped: stopped });
}

// ---------------------------------------------------------------------------- automation pass

function describeAutomation(pass) {
  if (!pass) {
    return null;
  }
  return {
    paths: pass.lanes.map((lane) => lane.path), start: pass.start, end: pass.end,
    phase: pass.phase, writes: pass.lanes.reduce((sum, lane) => sum + lane.writes, 0),
    first_beat: pass.first, last_beat: pass.last, error: pass.error,
  };
}

// The curve's value at `beat` (points sorted by time; before / after them: the end values;
// at a time with two points the later one wins).
function curveAt(points, mode, beat) {
  if (beat <= points[0][0]) {
    return points[0][1];
  }
  const last = points[points.length - 1];
  if (beat >= last[0]) {
    return last[1];
  }
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] <= beat) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  const [t0, v0] = points[lo];
  const [t1, v1] = points[hi];
  if (mode === "step" || t1 <= t0) {
    return v0;
  }
  return v0 + (v1 - v0) * (beat - t0) / (t1 - t0);
}

function finishAutomation(pass, phase, error) {
  for (const lane of pass.lanes) {
    if (lane.gesture) {
      maxApi.outlet("call", "end_gesture", ...atoms(lane.path));
      lane.gesture = false;
    }
  }
  pass.phase = phase;
  pass.error = error || null;
  state.lastAutomation = pass;
  state.automation = null;
  stopTickerIfIdle();
}

function automateTick(pass, now) {
  if (now - pass.created > AUTOMATE_TIMEOUT_MS) {
    finishAutomation(pass, "failed", "the pass did not reach its end in time");
    return;
  }
  const beat = songBeat(now);
  if (beat === null) {
    if (pass.phase === "writing") {      // stopped in the middle: the Remote Script aborts
      finishAutomation(pass, "aborted", "the transport stopped");
    }
    return;
  }
  if (beat < pass.start - 1e-6) {
    return;
  }
  const at = Math.min(beat, pass.end);
  if (pass.phase !== "writing") {
    pass.phase = "writing";
    pass.first = at;
  }
  for (const lane of pass.lanes) {
    if (!lane.gesture) {
      maxApi.outlet("call", "begin_gesture", ...atoms(lane.path));
      lane.gesture = true;
    }
    const value = curveAt(lane.points, lane.mode, at);
    if (lane.lastValue === null || Math.abs(value - lane.lastValue) > 1e-9) {
      maxApi.outlet("setvalue", value, ...atoms(lane.path));
      lane.lastValue = value;
      lane.writes += 1;
    }
  }
  pass.last = at;
  if (beat >= pass.end) {
    finishAutomation(pass, "done");
  }
}

function parseLane(spec) {
  const target = String(spec && spec.path || "");
  if (!MAX_PATH.test(target)) {
    return { error: "path must be a Live object path" };
  }
  const points = Array.isArray(spec.points) ? spec.points.map((p) => [Number(p[0]),
    Number(p[1])]) : [];
  if (!points.length || points.length > MAX_AUTOMATE_POINTS ||
      points.some((p) => !Number.isFinite(p[0]) || !Number.isFinite(p[1]))) {
    return { error: "points: 1.." + MAX_AUTOMATE_POINTS + " [beat, value]" };
  }
  points.sort((a, b) => a[0] - b[0]);
  return {
    path: target, points: points, mode: spec.mode === "step" ? "step" : "linear",
    writes: 0, lastValue: null, gesture: false,
  };
}

function automate(args, reply) {
  const specs = Array.isArray(args.lanes) ? args.lanes
    : [{ path: args.path, points: args.points, mode: args.mode }];
  if (!specs.length || specs.length > MAX_LANES) {
    reply({ type: "bad_args", message: "lanes: 1.." + MAX_LANES });
    return;
  }
  const lanes = [];
  for (const spec of specs) {
    const lane = parseLane(spec);
    if (lane.error) {
      reply({ type: "bad_args", message: lane.error });
      return;
    }
    lanes.push(lane);
  }
  const start = Number(args.start);
  const end = Number(args.end);
  if (!(Number.isFinite(start) && Number.isFinite(end) && end > start)) {
    reply({ type: "bad_args", message: "start and end (beats) with end > start" });
    return;
  }
  if (!maxApi) {
    reply({ type: "unsupported", message: "writing needs the device running in Live" });
    return;
  }
  if (state.automation) {
    reply({ type: "busy", message: "an automation pass is already running" });
    return;
  }
  state.automation = {
    lanes: lanes, start: start, end: end, created: Date.now(), phase: "waiting",
    first: null, last: null, error: null,
  };
  startTicker();
  reply(null, describeAutomation(state.automation));
}

function cancelAutomation(reply) {
  const pass = state.automation;
  if (pass) {
    finishAutomation(pass, "aborted", "stopped");
  }
  reply(null, { stopped: describeAutomation(pass) });
}

// ---------------------------------------------------------------------------- sampling pass

function sampleTick(pass, now) {
  if (now - pass.created > AUTOMATE_TIMEOUT_MS) {
    pass.phase = "done";
    pass.error = "the pass did not reach its end in time";
    stopTickerIfIdle();
    return;
  }
  const beat = songBeat(now);
  if (beat === null || beat < pass.start - 1e-6 || pass.pending) {
    return;
  }
  const at = Math.min(beat, pass.end);
  pass.pending = true;
  pass.phase = "reading";
  const id = state.nextQuery++;
  const timer = setTimeout(() => {
    state.queries.delete(id);
    pass.pending = false;
  }, FOLLOW_QUERY_MS);
  state.queries.set(id, (found) => {
    clearTimeout(timer);
    state.queries.delete(id);
    pass.pending = false;
    pass.reads += 1;
    found.values.forEach((value, index) => {
      const lane = pass.samples[index];
      if (lane && Number.isFinite(value) && (!lane.length ||
          Math.abs(lane[lane.length - 1][1] - value) > 1e-9 ||
          at - lane[lane.length - 1][0] >= 1)) {
        lane.push([at, value]);
      }
    });
    if (at >= pass.end) {
      pass.phase = "done";
      stopTickerIfIdle();
    }
  });
  maxApi.outlet("getvalues", id, pass.paths.join("|"));
}

// lbcontext answer: values <id> <value per path...>
function values(id, ...numbers) {
  const waiting = state.queries.get(Number(id));
  if (waiting) {
    waiting({ values: numbers.map(Number) });
  }
}

function sample(args, reply) {
  if (args.result) {
    const pass = state.sampling;
    reply(null, pass ? { phase: pass.phase, reads: pass.reads, error: pass.error || null,
                         paths: pass.paths, samples: pass.samples } : null);
    return;
  }
  const paths = Array.isArray(args.paths) ? args.paths.map(String) : [];
  if (!paths.length || paths.length > MAX_LANES || paths.some((p) => !MAX_PATH.test(p))) {
    reply({ type: "bad_args", message: "paths: 1.." + MAX_LANES + " Live object paths" });
    return;
  }
  const start = Number(args.start);
  const end = Number(args.end);
  if (!(Number.isFinite(start) && Number.isFinite(end) && end > start)) {
    reply({ type: "bad_args", message: "start and end (beats) with end > start" });
    return;
  }
  if (!maxApi) {
    reply({ type: "unsupported", message: "sampling needs the device running in Live" });
    return;
  }
  if (state.sampling && state.sampling.phase !== "done") {
    reply({ type: "busy", message: "a sampling pass is already running" });
    return;
  }
  state.sampling = {
    paths: paths, start: start, end: end, created: Date.now(), phase: "waiting",
    pending: false, reads: 0, samples: paths.map(() => []), error: null,
  };
  startTicker();
  reply(null, { phase: "waiting", paths: paths });
}

// ---------------------------------------------------------------------------- follow actions

function describeRule(rule) {
  return {
    target: rule.target, kind: rule.kind, every_beats: rule.every, lead_beats: rule.lead,
    actions: rule.actions, chance: rule.chance, slots: rule.slots, seed: rule.seed,
    current: rule.current, next_boundary: rule.boundary, history: rule.history,
  };
}

function songBeat(now) {
  const t = state.transport;
  return t && t.playing ? t.beat + (now - t.at) / 60000 * t.tempo : null;
}

function followTick(rule, now) {
  const beat = songBeat(now);
  if (beat === null) {
    rule.boundary = null;                // stopped: start again from the next boundary
    return;
  }
  if (rule.boundary === null || beat > rule.boundary + rule.every) {
    rule.boundary = (Math.floor(beat / rule.every + 1e-9) + 1) * rule.every;
  }
  if (beat >= rule.boundary - rule.lead && rule.acted !== rule.boundary) {
    rule.acted = rule.boundary;
    const boundary = rule.boundary;
    rule.boundary += rule.every;
    act(rule, boundary);
  }
}

function random(rule) {
  rule.draws += 1;
  return noise(rule.seed, rule.draws);
}

// One weighted pick from rule.actions ([[name, weight], ...]).
function pickAction(rule) {
  const total = rule.actions.reduce((sum, item) => sum + item[1], 0);
  let left = random(rule) * total;
  for (const [name, weight] of rule.actions) {
    left -= weight;
    if (left < 0) {
      return name;
    }
  }
  return rule.actions[rule.actions.length - 1][0];
}

// Which index an action leads to, from `current` among `candidates` (sorted indices that can
// be fired); null = nothing to do.
function targetIndex(rule, action, current, candidates) {
  if (action.startsWith("jump:")) {
    const index = Number(action.slice(5));
    return candidates.indexOf(index) >= 0 ? index : null;
  }
  if (!candidates.length) {
    return null;
  }
  const at = candidates.indexOf(current);
  const pick = (list) => (list.length ? list[Math.floor(random(rule) * list.length)] : null);
  switch (action) {
    case "again": return at >= 0 ? current : candidates[0];
    case "first": return candidates[0];
    case "last": return candidates[candidates.length - 1];
    case "any": return pick(candidates);
    case "other": {
      const others = candidates.filter((i) => i !== current);
      return others.length ? pick(others) : candidates[0];
    }
    case "previous":
      if (at < 0) {
        const before = candidates.filter((i) => i < current);
        return before.length ? before[before.length - 1] : candidates[candidates.length - 1];
      }
      return candidates[(at - 1 + candidates.length) % candidates.length];
    default: {                           // next
      if (at < 0) {
        const after = candidates.filter((i) => i > current);
        return after.length ? after[0] : candidates[0];
      }
      return candidates[(at + 1) % candidates.length];
    }
  }
}

function remember(rule, entry) {
  rule.history.push(entry);
  if (rule.history.length > FOLLOW_HISTORY) {
    rule.history.shift();
  }
}

function atoms(text) {
  return text.split(" ").map((part) => (/^\d+$/.test(part) ? Number(part) : part));
}

function ask(kind, target, done) {
  const id = state.nextQuery++;
  const timer = setTimeout(() => {
    state.queries.delete(id);
    done(null);
  }, FOLLOW_QUERY_MS);
  state.queries.set(id, (info) => {
    clearTimeout(timer);
    state.queries.delete(id);
    done(info);
  });
  maxApi.outlet("query", id, kind, ...atoms(target));
}

// context.js answer: info <id> <playing index> <fired index> <has clip 0/1 per slot...>
function info(id, playing, fired, ...flags) {
  const waiting = state.queries.get(Number(id));
  if (waiting) {
    waiting({ playing: Number(playing), fired: Number(fired), flags: flags.map(Number) });
  }
}

function act(rule, boundary) {
  if (rule.chance < 1 && random(rule) >= rule.chance) {
    remember(rule, { beat: boundary, action: "stay" });
    return;
  }
  const action = pickAction(rule);
  if (action === "stop") {
    maxApi.outlet("call", "stop_all_clips", ...atoms(rule.target));
    rule.current = -1;
    remember(rule, { beat: boundary, action: action });
    return;
  }
  ask(rule.kind, rule.target, (found) => {
    if (found === null) {
      remember(rule, { beat: boundary, action: action, error: "Live did not answer" });
      return;
    }
    // the clip Live plays (or is about to) is the starting point; scenes: the last one fired
    let current = rule.current;
    if (rule.kind === "track") {
      current = found.fired >= 0 ? found.fired : found.playing >= 0 ? found.playing : current;
    }
    let candidates = [];
    found.flags.forEach((has, index) => {
      if (has && (!rule.slots || rule.slots.indexOf(index) >= 0)) {
        candidates.push(index);
      }
    });
    const index = targetIndex(rule, action, current, candidates);
    if (index === null) {
      remember(rule, { beat: boundary, action: action, error: "nothing to launch" });
      return;
    }
    const path = rule.kind === "track" ? rule.target + " clip_slots " + index
      : "live_set scenes " + index;
    maxApi.outlet("call", "fire", ...atoms(path));
    rule.current = index;
    remember(rule, { beat: boundary, action: action, index: index });
  });
}

function parseActions(value) {
  const entries = typeof value === "string" ? [[value, 1]]
    : Array.isArray(value) ? value : Object.entries(value || {});
  const actions = [];
  for (const entry of entries) {
    const name = String(entry[0]);
    const weight = Number(entry[1]);
    if (FOLLOW_ACTIONS.indexOf(name) < 0 && !/^jump:\d+$/.test(name)) {
      return { error: "unknown action " + JSON.stringify(name) + " — use " +
        FOLLOW_ACTIONS.join(", ") + " or jump:<index>" };
    }
    if (!(weight > 0)) {
      return { error: "action weights must be > 0" };
    }
    actions.push([name, weight]);
  }
  if (!actions.length) {
    return { error: "give at least one action" };
  }
  return { actions: actions };
}

function follow(args, reply) {
  const target = String(args.target || "");
  const kind = target === "live_set" ? "scenes" : "track";
  if (kind === "track" && !/^live_set (tracks|return_tracks) \d+$/.test(target)) {
    reply({ type: "bad_args", message: "target must be 'live_set tracks <n>' or 'live_set' "
      + "(scenes)" });
    return;
  }
  const every = Number(args.every_beats);
  if (!(every >= 0.25 && every <= 1024)) {
    reply({ type: "bad_args", message: "every_beats must be 0.25..1024" });
    return;
  }
  const lead = args.lead_beats === undefined ? 0.25 : Number(args.lead_beats);
  if (!(lead >= 0 && lead < every)) {
    reply({ type: "bad_args", message: "lead_beats must be >= 0 and < every_beats" });
    return;
  }
  const chance = args.chance === undefined ? 1 : Number(args.chance);
  if (!(chance > 0 && chance <= 1)) {
    reply({ type: "bad_args", message: "chance must be > 0 and <= 1" });
    return;
  }
  const parsed = parseActions(args.actions === undefined ? "next" : args.actions);
  if (parsed.error) {
    reply({ type: "bad_args", message: parsed.error });
    return;
  }
  let slots = null;
  if (args.slots !== undefined && args.slots !== null) {
    slots = Array.isArray(args.slots) ? args.slots.map(Number) : [];
    if (!slots.length || slots.some((i) => !Number.isInteger(i) || i < 0)) {
      reply({ type: "bad_args", message: "slots must list clip slot / scene indices" });
      return;
    }
  }
  if (!maxApi) {
    reply({ type: "unsupported", message: "follow actions need the device running in Live" });
    return;
  }
  if (!state.rules.has(target) && state.rules.size >= MAX_RULES) {
    reply({ type: "busy", message: "at most " + MAX_RULES + " follow rules per device" });
    return;
  }
  const previous = state.rules.get(target);
  const rule = {
    target: target, kind: kind, every: every, lead: lead, chance: chance,
    actions: parsed.actions, slots: slots, seed: Number(args.seed || 0) | 0, draws: 0,
    current: args.current === undefined ? (previous ? previous.current : -1)
      : Number(args.current),
    boundary: null, acted: null, history: [],
  };
  state.rules.set(target, rule);
  startTicker();
  reply(null, describeRule(rule));
}

function unfollow(args, reply) {
  const stopped = [];
  for (const rule of [...state.rules.values()]) {
    if (args.all || rule.target === args.target) {
      stopped.push(describeRule(rule));
      state.rules.delete(rule.target);
    }
  }
  stopTickerIfIdle();
  reply(null, { stopped: stopped });
}

// sfrecord~ closes the file asynchronously: poll until its size stops changing.
function waitForFile(file, attempts, done) {
  let last = -1;
  const check = (left) => {
    let size = -1;
    try {
      size = fs.statSync(file).size;
    } catch (error) {
      size = -1;
    }
    if ((size > 44 && size === last) || left <= 0) {
      done();
      return;
    }
    last = size;
    setTimeout(() => check(left - 1), 50);
  };
  setTimeout(() => check(attempts), 50);
}

function framesNow(seconds) {
  const from = Date.now() - seconds * 1000;
  return state.frames.filter((frame) => frame[0] >= from);
}

function handle(request, reply) {
  const args = request.args && typeof request.args === "object" ? request.args : {};
  if (request.cmd === "hello") {
    reply(null, hello());
  } else if (request.cmd === "frames") {
    const seconds = Number(args.seconds === undefined ? 3 : args.seconds);
    if (!Number.isFinite(seconds) || seconds <= 0 || seconds > HISTORY_SECONDS) {
      reply({ type: "bad_args", message: "seconds must be > 0 and <= " + HISTORY_SECONDS });
      return;
    }
    const answer = (frames) => reply(null, {
      fields: FRAME_FIELDS, frames: frames, now: Date.now(), role: state.role,
      track: state.track, frame_interval_ms: frameInterval(),
    });
    if (!args.wait) {
      answer(framesNow(seconds));
      return;
    }
    if (seconds > MAX_WAIT_SECONDS) {
      reply({ type: "bad_args", message: "wait at most " + MAX_WAIT_SECONDS + " seconds" });
      return;
    }
    const waiter = { from: Date.now(), collected: [] };
    state.waiters.add(waiter);
    setTimeout(() => {
      state.waiters.delete(waiter);
      answer(waiter.collected);
    }, seconds * 1000);
  } else if (request.cmd === "capture") {
    capture(args, reply);
  } else if (request.cmd === "modulate") {
    modulate(args, reply);
  } else if (request.cmd === "unmodulate") {
    unmodulate(args, reply);
  } else if (request.cmd === "automate") {
    if (args.cancel) {
      cancelAutomation(reply);
    } else {
      automate(args, reply);
    }
  } else if (request.cmd === "sample") {
    sample(args, reply);
  } else if (request.cmd === "follow") {
    follow(args, reply);
  } else if (request.cmd === "unfollow") {
    unfollow(args, reply);
  } else {
    reply({ type: "unknown_command", message: "unknown command " + JSON.stringify(request.cmd) });
  }
}

function serve(socket) {
  socket.setEncoding("utf8");
  let buffer = "";
  const send = (payload) => {
    if (!socket.destroyed) {
      socket.write(JSON.stringify(payload) + "\n");
    }
  };
  socket.on("data", (chunk) => {
    buffer += chunk;
    if (buffer.length > MAX_LINE && buffer.indexOf("\n") < 0) {
      send({ id: null, ok: false, error: { type: "bad_request", message: "line too long" } });
      socket.destroy();
      return;
    }
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) {
        continue;
      }
      let request;
      try {
        request = JSON.parse(line);
      } catch (error) {
        send({ id: null, ok: false, error: { type: "bad_request", message: "not JSON" } });
        continue;
      }
      const id = request && request.id !== undefined ? request.id : null;
      if (!request || request.token !== state.token) {
        send({ id: id, ok: false, error: { type: "auth", message: "wrong or missing token" } });
        continue;
      }
      handle(request, (error, result) => {
        send(error ? { id: id, ok: false, error: error } : { id: id, ok: true, result: result });
      });
    }
  });
  socket.on("error", () => {});
}

// ---------------------------------------------------------------------------- start

// live.thisdevice reports where the device sits as soon as the device loads, usually before
// this Node process runs, and Max drops messages sent to a node.script that is not running
// yet. So once listening, ask context.js (via the "context" outlet) until it has answered.
const CONTEXT_RETRY_MS = 2000;
const CONTEXT_ATTEMPTS = 30;

function askForContext(attempt = 0) {
  if (!maxApi || state.role !== "unknown" || attempt >= CONTEXT_ATTEMPTS) {
    return;
  }
  maxApi.outlet("context");
  setTimeout(() => askForContext(attempt + 1), CONTEXT_RETRY_MS).unref();
}

function start() {
  const server = net.createServer(serve);
  server.on("error", (error) => {
    log("server error: " + error.message);
    status("error: " + error.message);
  });
  server.listen(0, "127.0.0.1", () => {
    state.port = server.address().port;
    writeRegistry();
    log(NAME + " " + VERSION + " listening on 127.0.0.1:" + state.port);
    if (state.role === "unknown") {
      status("ready");
    }
    askForContext();
  });
  const stop = () => {
    removeRegistry();
    process.exit(0);
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  process.on("exit", removeRegistry);
  if (maxApi) {
    maxApi.addHandler("frame", (...values) => addFrame(values));
    maxApi.addHandler("context", (role, ...track) => setContext(String(role), track.map(String)));
    maxApi.addHandler("transport", setTransport);
    maxApi.addHandler("resolved", resolved);
    maxApi.addHandler("info", info);
    maxApi.addHandler("values", values);
  }
  if (maxApi) {
    maxApi.outlet("ready");          // opens the patch's gate: messages may come in now
  }
}

start();

module.exports = {
  addFrame, setContext, capture, modValue, shapeAt, targetIndex, curveAt, FRAME_FIELDS,
  state,
};
