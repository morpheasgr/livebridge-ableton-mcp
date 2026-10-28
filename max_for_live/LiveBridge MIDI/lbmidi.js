// LiveBridge MIDI — the Node for Max part of the MIDI effect.
//
// The patch passes MIDI through unchanged and copies every note and controller that reaches the
// device (the keyboard on an armed / monitored track, and the track's own clips) to this
// script, which keeps the last 10 minutes with Live's song position. It also plays notes into
// the instrument after it ("play": a phrase at the next bar or beat, optionally looped), timed
// from Live's song position that lbmidi_context.js reports.
//
// Like the LiveBridge Engine it listens on 127.0.0.1 (random port, random token) and announces
// itself in ~/.livebridge/engine/<instance>.json with kind "midi".
//
// Requests: {"id", "token", "cmd": "hello" | "played" | "play" | "stop", "args"}
// Answers:  {"id", "ok": true, "result"} or {"id", "ok": false, "error": {"type", "message"}}

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

const NAME = "LiveBridge MIDI";
const VERSION = "1.0.1";
const PROTOCOL = 1;
const KEEP_MS = 10 * 60 * 1000;
const MAX_EVENTS = 20000;
const MAX_LINE = 1024 * 1024;
const MAX_PLAY_NOTES = 2000;
const MAX_REPEAT = 256;
const CONTEXT_RETRY_MS = 2000;
const CONTEXT_ATTEMPTS = 30;

const state = {
  instance: crypto.randomBytes(6).toString("hex"),
  token: crypto.randomBytes(24).toString("hex"),
  role: "unknown",
  track: "",
  started: new Date().toISOString(),
  events: [],          // [ms, song beat | null, "note"|"cc", number, value]
  transport: null,     // {beat, tempo, playing, at}
  timers: new Set(),
  sounding: new Map(), // pitch -> count of notes this script holds down
  playing: null,       // what "play" is doing: {notes, repeat, loop_beats, started_beat}
  port: null,
};

function registryDir() {
  return process.env.LIVEBRIDGE_ENGINE_DIR || path.join(os.homedir(), ".livebridge", "engine");
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
    name: NAME, kind: "midi", version: VERSION, protocol: PROTOCOL, instance: state.instance,
    pid: process.pid, host: "127.0.0.1", port: state.port, token: state.token,
    role: state.role, track: state.track, started: state.started,
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
  try {
    fs.unlinkSync(registryFile());
  } catch (error) {
    // already gone
  }
}

function setContext(role, trackParts) {
  state.role = role === "master" ? "master" : "track";
  state.track = trackParts.join(" ");
  status(state.role === "master" ? "master" : "track: " + state.track);
  writeRegistry();
}

function setTransport(beat, tempo, playing) {
  state.transport = {
    beat: Number(beat) || 0, tempo: Number(tempo) || 120, playing: Number(playing) === 1,
    at: Date.now(),
  };
}

function songBeat(now) {
  const t = state.transport;
  if (!t) {
    return null;
  }
  return t.playing ? t.beat + (now - t.at) / 60000 * t.tempo : t.beat;
}

function tempo() {
  return state.transport ? state.transport.tempo : 120;
}

// ---------------------------------------------------------------------------- what was played

function addEvent(kind, number, value) {
  const now = Date.now();
  state.events.push([now, songBeat(now), kind, Number(number), Number(value)]);
  const from = now - KEEP_MS;
  let drop = 0;
  while (drop < state.events.length && (state.events[drop][0] < from ||
         state.events.length - drop > MAX_EVENTS)) {
    drop++;
  }
  if (drop) {
    state.events.splice(0, drop);
  }
}

// Notes (on/off pairs) and controller changes of the last `seconds`.
function played(seconds) {
  const now = Date.now();
  const from = now - seconds * 1000;
  const open = new Map();
  const notes = [];
  const controllers = [];
  for (const [ms, beat, kind, number, value] of state.events) {
    if (kind === "cc") {
      if (ms >= from) {
        controllers.push({ ms: ms, beat: beat, cc: number, value: value });
      }
      continue;
    }
    if (value > 0) {
      if (!open.has(number)) {
        open.set(number, []);
      }
      open.get(number).push({ ms: ms, beat: beat, velocity: value });
    } else if (open.has(number) && open.get(number).length) {
      const on = open.get(number).shift();
      if (ms >= from) {
        notes.push({ pitch: number, velocity: on.velocity, ms: on.ms, beat: on.beat,
                     end_ms: ms, end_beat: beat });
      }
    }
  }
  for (const [pitch, list] of open) {          // still held down
    for (const on of list) {
      if (on.ms >= from) {
        notes.push({ pitch: pitch, velocity: on.velocity, ms: on.ms, beat: on.beat,
                     end_ms: null, end_beat: null, held: true });
      }
    }
  }
  notes.sort((a, b) => a.ms - b.ms || a.pitch - b.pitch);
  return {
    notes: notes, controllers: controllers, now: now, now_beat: songBeat(now),
    tempo: tempo(), playing: Boolean(state.transport && state.transport.playing),
  };
}

// ---------------------------------------------------------------------------- playing notes

function send(pitch, velocity) {
  if (maxApi) {
    maxApi.outlet("play", pitch, velocity);
  }
}

function later(ms, fn) {
  const timer = setTimeout(() => {
    state.timers.delete(timer);
    fn();
  }, Math.max(0, ms));
  state.timers.add(timer);
}

function noteOn(pitch, velocity) {
  state.sounding.set(pitch, (state.sounding.get(pitch) || 0) + 1);
  send(pitch, velocity);
}

function noteOff(pitch) {
  const count = state.sounding.get(pitch) || 0;
  if (count <= 1) {
    state.sounding.delete(pitch);
  } else {
    state.sounding.set(pitch, count - 1);
  }
  send(pitch, 0);
}

function stopPlaying() {
  for (const timer of state.timers) {
    clearTimeout(timer);
  }
  state.timers.clear();
  for (const [pitch, count] of state.sounding) {
    for (let i = 0; i < count; i++) {
      send(pitch, 0);
    }
  }
  const was = state.playing;
  state.sounding.clear();
  state.playing = null;
  return was;
}

// When to start: "now", or the next beat / bar boundary of the song position.
function startDelayBeats(start, beatsPerBar) {
  const beat = songBeat(Date.now());
  if (start === "now" || beat === null || !(state.transport && state.transport.playing)) {
    return 0;
  }
  const grid = start === "next_beat" ? 1 : beatsPerBar;
  const next = (Math.floor(beat / grid + 1e-9) + 1) * grid;
  return next - beat;
}

function parseNotes(list) {
  if (!Array.isArray(list) || !list.length || list.length > MAX_PLAY_NOTES) {
    return { error: "notes: 1.." + MAX_PLAY_NOTES + " notes" };
  }
  const notes = [];
  for (const note of list) {
    const pitch = Number(note && note.pitch);
    const start = Number(note && note.start);
    const duration = Number(note && note.duration);
    const velocity = note && note.velocity !== undefined ? Number(note.velocity) : 100;
    if (!Number.isInteger(pitch) || pitch < 0 || pitch > 127 || !(start >= 0) ||
        !(duration > 0) || !Number.isInteger(velocity) || velocity < 1 || velocity > 127) {
      return { error: "each note needs pitch 0..127, start >= 0, duration > 0 (beats) and "
        + "velocity 1..127" };
    }
    notes.push({ pitch: pitch, start: start, duration: duration, velocity: velocity });
  }
  return { notes: notes };
}

function play(args, reply) {
  const parsed = parseNotes(args.notes);
  if (parsed.error) {
    reply({ type: "bad_args", message: parsed.error });
    return;
  }
  const start = String(args.start || "next_bar");
  if (["now", "next_beat", "next_bar"].indexOf(start) < 0) {
    reply({ type: "bad_args", message: "start must be now, next_beat or next_bar" });
    return;
  }
  const end = Math.max(...parsed.notes.map((n) => n.start + n.duration));
  const loopBeats = args.loop_beats === undefined || args.loop_beats === null
    ? Math.ceil(end - 1e-9) : Number(args.loop_beats);
  const repeat = args.repeat === undefined ? 1 : Number(args.repeat);
  if (!(loopBeats > 0) || !Number.isInteger(repeat) || repeat < 1 || repeat > MAX_REPEAT) {
    reply({ type: "bad_args", message: "loop_beats > 0 and repeat 1.." + MAX_REPEAT });
    return;
  }
  const beatsPerBar = Number(args.beats_per_bar) > 0 ? Number(args.beats_per_bar) : 4;
  stopPlaying();
  const msPerBeat = 60000 / tempo();
  const delay = startDelayBeats(start, beatsPerBar);
  for (let pass = 0; pass < repeat; pass++) {
    for (const note of parsed.notes) {
      const on = (delay + pass * loopBeats + note.start) * msPerBeat;
      later(on, () => noteOn(note.pitch, note.velocity));
      later(on + note.duration * msPerBeat, () => noteOff(note.pitch));
    }
  }
  const total = (delay + repeat * loopBeats) * msPerBeat;
  later(total + 5, () => {
    state.playing = null;
  });
  const beat = songBeat(Date.now());
  state.playing = {
    notes: parsed.notes.length, repeat: repeat, loop_beats: loopBeats, start: start,
    starts_in_beats: delay, starts_at_beat: beat === null ? null : beat + delay,
    seconds: total / 1000, tempo: tempo(),
  };
  reply(null, state.playing);
}

// ---------------------------------------------------------------------------- requests

function hello() {
  return {
    name: NAME, kind: "midi", version: VERSION, protocol: PROTOCOL, instance: state.instance,
    role: state.role, track: state.track, started: state.started,
    events_buffered: state.events.length, playing: state.playing,
    transport: state.transport ? { tempo: state.transport.tempo,
                                   playing: state.transport.playing } : null,
  };
}

function handle(request, reply) {
  const args = request.args && typeof request.args === "object" ? request.args : {};
  if (request.cmd === "hello") {
    reply(null, hello());
  } else if (request.cmd === "played") {
    const seconds = Number(args.seconds === undefined ? 30 : args.seconds);
    if (!(seconds > 0 && seconds <= KEEP_MS / 1000)) {
      reply({ type: "bad_args", message: "seconds must be > 0 and <= " + KEEP_MS / 1000 });
      return;
    }
    reply(null, played(seconds));
  } else if (request.cmd === "play") {
    play(args, reply);
  } else if (request.cmd === "stop") {
    reply(null, { stopped: stopPlaying() });
  } else {
    reply({ type: "unknown_command", message: "unknown command " + JSON.stringify(request.cmd) });
  }
}

function serve(socket) {
  socket.setEncoding("utf8");
  let buffer = "";
  const reply = (payload) => {
    if (!socket.destroyed) {
      socket.write(JSON.stringify(payload) + "\n");
    }
  };
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
      if (!line) {
        continue;
      }
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

// ---------------------------------------------------------------------------- start

// The device's track is reported when the device loads, usually before Node runs (see the
// Engine's engine.js): ask lbmidi_context.js until it answers.
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
    stopPlaying();
    removeRegistry();
    process.exit(0);
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  process.on("exit", removeRegistry);
  if (maxApi) {
    maxApi.addHandler("note", (pitch, velocity) => addEvent("note", pitch, velocity));
    maxApi.addHandler("cc", (number, value) => addEvent("cc", number, value));
    maxApi.addHandler("context", (role, ...track) => setContext(String(role), track.map(String)));
    maxApi.addHandler("transport", setTransport);
    maxApi.addHandler("resolved", () => {});
    maxApi.addHandler("info", () => {});
  }
  if (maxApi) {
    maxApi.outlet("ready");          // opens the patch's gate: messages may come in now
  }
}

start();

module.exports = { addEvent, played, play, stopPlaying, setTransport, state };
