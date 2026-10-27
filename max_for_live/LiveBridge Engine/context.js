// LiveBridge Engine — where the device sits, from Live's API (Max js object).
//
// Sends "context master <track name>" when the device is on the master track, otherwise
// "context track <track name>", on every bang (live.thisdevice when Live is ready, engine.js
// asking once it runs) and again when the track is renamed. engine.js puts it in the registry
// file the MCP server reads. Nothing is sent while Live's API cannot place the device yet;
// engine.js asks again.
//
// Also, for the parameter modulation in engine.js: "resolve <slot> <path...>" answers
// "resolved <slot> <id> <min> <max> <value> <name...>" (id 0 when nothing is there), and the
// transport is reported as "transport <beat> <tempo> <playing>" when it changes (song position
// at most every 50 ms while playing).
//
// For follow actions: "query <id> track <path...>" answers "info <id> <playing slot> <fired
// slot> <has clip 0/1 per slot...>", "query <id> scenes live_set" answers "info <id> -1 -1
// 1 1 ..." (one per scene), and "call fire|stop_all_clips <path...>" launches or stops.

autowatch = 0;
inlets = 1;
outlets = 1;

var nameObserver = null;
var transportObservers = null;
var transportState = { beat: 0, tempo: 120, playing: 0 };
var lastPositionReport = 0;
var POSITION_EVERY_MS = 50;

function bang() {
    report();
    observeTransport();
    var track = nameObserver === null ? trackOf() : null;
    if (track !== null) {
        nameObserver = new LiveAPI(onName, track.unquotedpath);
        nameObserver.property = "name";
    }
}

function onName(args) {
    if (args && args[0] === "name") {
        report();
    }
}

function observeTransport() {
    if (transportObservers !== null) {
        sendTransport();
        return;
    }
    var song = new LiveAPI("live_set");
    if (!song || song.id == 0) {
        return;
    }
    transportState.tempo = Number(song.get("tempo")) || 120;
    transportState.playing = Number(song.get("is_playing")) ? 1 : 0;
    transportState.beat = Number(song.get("current_song_time")) || 0;
    transportObservers = [];
    var watch = function (property) {
        var api = new LiveAPI(onTransport, "live_set");
        api.property = property;
        transportObservers.push(api);
    };
    watch("tempo");
    watch("is_playing");
    watch("current_song_time");
    sendTransport();
}

function onTransport(args) {
    if (!args || args.length < 2) {
        return;
    }
    var value = Number(args[1]);
    if (args[0] === "tempo") {
        transportState.tempo = value || 120;
    } else if (args[0] === "is_playing") {
        transportState.playing = value ? 1 : 0;
    } else if (args[0] === "current_song_time") {
        transportState.beat = value;
        var now = new Date().getTime();
        if (now - lastPositionReport < POSITION_EVERY_MS) {
            return;
        }
        lastPositionReport = now;
    } else {
        return;
    }
    sendTransport();
}

function sendTransport() {
    outlet(0, "transport", transportState.beat, transportState.tempo, transportState.playing);
}

function resolve() {
    var parts = arrayfromargs(arguments);
    var slot = parts.shift();
    var api = new LiveAPI(parts.join(" "));
    if (!api || api.id == 0 || api.type !== "DeviceParameter") {
        outlet(0, "resolved", slot, 0, 0, 0, 0);
        return;
    }
    var name = api.get("name");
    outlet(0, "resolved", slot, Number(api.id), Number(api.get("min")), Number(api.get("max")),
           Number(api.get("value")), name && name.length ? String(name.join(" ")) : "");
}

function query() {
    var parts = arrayfromargs(arguments);
    var id = parts.shift();
    var kind = parts.shift();
    var api = new LiveAPI(parts.join(" "));
    var flags = [];
    if (!api || api.id == 0) {
        outlet(0, "info", id, -1, -1);
        return;
    }
    if (kind === "scenes") {
        var scenes = api.getcount("scenes");
        for (var s = 0; s < scenes; s++) {
            flags.push(1);
        }
        outlet(0, ["info", id, -1, -1].concat(flags));
        return;
    }
    var slots = api.getcount("clip_slots");
    var base = parts.join(" ");
    for (var i = 0; i < slots; i++) {
        var slot = new LiveAPI(base + " clip_slots " + i);
        flags.push(Number(slot.get("has_clip")) ? 1 : 0);
    }
    outlet(0, ["info", id, Number(api.get("playing_slot_index")),
               Number(api.get("fired_slot_index"))].concat(flags));
}

var CALLS = { fire: true, stop_all_clips: true };

function call() {
    var parts = arrayfromargs(arguments);
    var method = parts.shift();
    if (!CALLS[method]) {
        return;
    }
    var api = new LiveAPI(parts.join(" "));
    if (api && api.id != 0) {
        api.call(method);
    }
}

function trackOf() {
    // a device inside a rack chain: walk up until a track (or the master) is reached
    var api = new LiveAPI("this_device");
    for (var depth = 0; depth < 8; depth++) {
        api = new LiveAPI(api.unquotedpath + " canonical_parent");
        if (!api || api.id == 0) {
            return null;
        }
        var type = api.type;
        if (type === "Track" || type === "MainTrack" || type === "MasterTrack") {
            return api;
        }
    }
    return null;
}

function report() {
    var track = trackOf();
    if (track === null) {
        return;
    }
    var master = new LiveAPI("live_set master_track");
    var name = track.get("name");
    var label = name && name.length ? String(name.join(" ")) : "";
    outlet(0, "context", track.id == master.id ? "master" : "track", label || "Master");
}
