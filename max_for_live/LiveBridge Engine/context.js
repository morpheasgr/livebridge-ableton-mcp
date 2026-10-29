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
//
// For the fast automation recording: "call begin_gesture|end_gesture <path...>" and
// "setvalue <value> <path...>" (the parameter objects are kept between calls); for sampling
// "getvalues <id> <path|path|...>" answers "values <id> <value per path...>".
//
// For the API dump (tests/dump_m4l_lom.py): "lomdump <file>" walks Live's object model as Max
// sees it (LiveAPI "info": properties, functions, children of each object type, devices per
// class), writes it as JSON to <file> and answers "lomdumped <object count>".

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

var CALLS = { fire: true, stop_all_clips: true, begin_gesture: true, end_gesture: true };
var parameters = {};

function parameterAt(path) {
    var api = parameters[path];
    if (!api || api.id == 0) {
        api = new LiveAPI(path);
        parameters[path] = api;
    }
    return api;
}

function setvalue() {
    var parts = arrayfromargs(arguments);
    var value = parts.shift();
    var api = parameterAt(parts.join(" "));
    if (api && api.id != 0) {
        api.set("value", value);
    }
}

function call() {
    var parts = arrayfromargs(arguments);
    var method = parts.shift();
    if (!CALLS[method]) {
        return;
    }
    var path = parts.join(" ");
    var api = method === "fire" || method === "stop_all_clips" ? new LiveAPI(path)
        : parameterAt(path);
    if (api && api.id != 0) {
        api.call(method);
    }
}

function getvalues(id, joined) {
    var paths = String(joined).split("|");
    var result = ["values", id];
    for (var i = 0; i < paths.length; i++) {
        var api = parameterAt(paths[i]);
        result.push(api && api.id != 0 ? Number(api.get("value")) : 0);
    }
    outlet(0, result);
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

// The LiveBridge Host names its device after the plug-in it holds (Live saves the name with
// the Set) and reads it back after the Set is reopened.
function rename() {
    var name = arrayfromargs(arguments).join(" ");
    var api = new LiveAPI("this_device");
    if (api.id != 0 && name) {
        api.set("name", name);
    }
}

function devicename() {
    var api = new LiveAPI("this_device");
    if (api.id != 0) {
        var name = api.get("name");
        outlet(0, "devicename", name instanceof Array ? name.join(" ") : String(name));
    }
}

// ---------------------------------------------------------------------------- API dump

var LOM_ROOTS = ["live_app", "live_app view", "live_set", "live_set view", "this_device"];
var LOM_MAX_OBJECTS = 600;
var LOM_LIST_ITEMS = 24;        // items of one child list looked at
var LOM_LIST_REPEATS = 4;       // stop a list after this many already-known types in a row

function lomInfo(api) {
    var data = { type: api.type, description: "", properties: [], functions: [], children: [] };
    var lines = String(api.info).split("\n");
    for (var i = 0; i < lines.length; i++) {
        var words = lines[i].replace(/^\s+|\s+$/g, "").split(" ");
        var head = words.shift();
        if (head === "description") {
            data.description = words.join(" ");
        } else if (head === "property") {
            data.properties.push([words[0], words.slice(1).join(" ")]);
        } else if (head === "function") {
            data.functions.push(words[0]);
        } else if (head === "children" || head === "child") {
            data.children.push([words[0], words.slice(1).join(" "), head === "children"]);
        }
    }
    return data;
}

function lomKey(api) {
    var type = String(api.type);
    if (/Device$/.test(type) || type === "Device") {
        var cls = api.get("class_name");
        return type + ":" + (cls instanceof Array ? cls.join(" ") : String(cls));
    }
    return type;
}

function lomdump() {
    var file = arrayfromargs(arguments).join(" ");
    var types = {};
    var queue = LOM_ROOTS.slice();
    var looked = 0;
    while (queue.length && looked < LOM_MAX_OBJECTS) {
        var path = queue.shift();
        var api = new LiveAPI(path);
        looked++;
        if (!api || api.id == 0) {
            continue;
        }
        var key = lomKey(api);
        if (types[key]) {
            continue;
        }
        var info = lomInfo(api);
        info.path = path;
        types[key] = info;
        for (var c = 0; c < info.children.length; c++) {
            var name = info.children[c][0];
            if (!info.children[c][2]) {
                queue.push(path + " " + name);
                continue;
            }
            var count = api.getcount(name);
            var known = 0;
            for (var n = 0; n < Math.min(count, LOM_LIST_ITEMS); n++) {
                var item = new LiveAPI(path + " " + name + " " + n);
                looked++;
                if (!item || item.id == 0) {
                    continue;
                }
                if (types[lomKey(item)]) {
                    if (++known >= LOM_LIST_REPEATS && !/devices|chains|drum_pads/.test(name)) {
                        break;
                    }
                    continue;
                }
                known = 0;
                queue.push(path + " " + name + " " + n);
            }
        }
    }
    var app = new LiveAPI("live_app");
    var dump = {
        max_version: typeof max !== "undefined" ? String(max.version) : null,
        live_version: [app.call("get_major_version"), app.call("get_minor_version"),
                       app.call("get_bugfix_version")].join("."),
        objects_looked_at: looked,
        types: types
    };
    var text = JSON.stringify(dump);
    var out = new File(file, "write", "TEXT");
    if (!out.isopen) {
        outlet(0, "lomdumped", -1);
        return;
    }
    out.eof = 0;
    for (var at = 0; at < text.length; at += 8000) {     // writestring takes limited chunks
        out.writestring(text.substr(at, 8000));
    }
    out.close();
    outlet(0, "lomdumped", Object.keys(types).length);
}
