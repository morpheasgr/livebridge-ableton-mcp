// LiveBridge Engine — where the device sits, from Live's API (Max js object).
//
// Sends "context master <track name>" when the device is on the master track, otherwise
// "context track <track name>", on every bang (live.thisdevice when Live is ready, engine.js
// asking once it runs) and again when the track is renamed. engine.js puts it in the registry
// file the MCP server reads. Nothing is sent while Live's API cannot place the device yet;
// engine.js asks again.

autowatch = 0;
inlets = 1;
outlets = 1;

var nameObserver = null;

function bang() {
    report();
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
