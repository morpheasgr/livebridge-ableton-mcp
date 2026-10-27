// LiveBridge Engine — the Node for Max part of the device.
//
// The patch measures the audio passing through the device (gen~, lb_engine.gendsp) and sends
// a "frame" of 18 meter values ten times a second; this script keeps the last minute of
// frames and answers the LiveBridge MCP server over a local TCP connection (127.0.0.1 only,
// JSON lines, a random token). It announces itself with a small registry file in
// ~/.livebridge/engine/<instance>.json (port, token, role, track), removed when the device
// goes away, so the MCP server finds every Engine without fixed ports.
//
// Requests: {"id": 1, "token": "...", "cmd": "hello" | "frames", "args": {...}}
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
const VERSION = "1.0.0";
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

const state = {
  instance: crypto.randomBytes(6).toString("hex"),
  token: crypto.randomBytes(24).toString("hex"),
  role: "unknown",
  track: "",
  started: new Date().toISOString(),
  frames: [], // [timestamp_ms, ...values]
  waiters: new Set(),
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
    history_seconds: HISTORY_SECONDS,
  };
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
  }
}

start();

module.exports = { addFrame, setContext, FRAME_FIELDS, state };
