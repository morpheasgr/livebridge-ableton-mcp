// LiveBridge Engine — where the device sits, from Live's API (Max js object).
//
// Sends "context master <track name>" when the device is on the master track, otherwise
// "context track <track name>", once Live is ready (live.thisdevice bangs) and again when the
// track is renamed. engine.js puts it in the registry file the MCP server reads.

autowatch = 0;
inlets = 1;
outlets = 1;

var nameObserver = null;

function bang() {
    report();
    if (nameObserver === null) {
        nameObserver = new LiveAPI(onName, "this_device canonical_parent");
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
        outlet(0, "context", "track", "?");
        return;
    }
    var master = new LiveAPI("live_set master_track");
    var name = track.get("name");
    var label = name && name.length ? String(name.join(" ")) : "";
    outlet(0, "context", track.id == master.id ? "master" : "track", label || "Master");
}
