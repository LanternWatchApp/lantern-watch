/**
 * Lantern Watch — install + opt-in usage recorder (Google Apps Script web app).
 *
 * Two kinds of ping, both upserted by install_id (one row per router):
 *   - event:"install"  sent on first boot AND whenever the version changes (each
 *                      update). Records first_seen (once), and refreshes last_seen,
 *                      version, model, OpenWrt. Does NOT mark opted_in.
 *   - event:"ping"     opt-in daily usage. Bumps ping_count, sets opted_in=TRUE,
 *                      records feature flags + device count.
 *
 * Update-checking is no longer here — the app reads GitHub tags directly, so
 * there's no version to keep in sync.
 *
 * Deploy: paste, Save, then Deploy -> Manage deployments -> (edit the existing
 * web app) -> New version -> Deploy. Editing in place keeps the same /exec URL.
 */

var SPREADSHEET_ID = "1nQa9L6tIWXxl1iH_En5mEVJHkaEsRQ5r7d32Iqu_0W8";
var SHEET_NAME     = "Installs";

var HEADER = [
  "first_seen", "install_id", "last_seen", "ping_count", "opted_in",
  "version", "router_model", "openwrt_version", "adguard_connected",
  "device_count", "social_profile", "screen_time", "social_blocking",
  "bedtime_enabled", "focus_times", "notif_ntfy", "notif_telegram", "notif_email"
];

function doPost(e) {
  var lock = LockService.getScriptLock();
  try { lock.waitLock(30000); }
  catch (err) { return jsonOut_({ ok: false, error: "Busy, could not obtain lock" }); }

  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonOut_({ ok: false, error: "No POST body" });
    }
    var d  = JSON.parse(e.postData.contents);
    var id = d.install_id;
    if (!id) return jsonOut_({ ok: false, error: "Missing install_id" });

    var sheet     = getSheet_();
    var C         = colMap_(sheet);
    var now       = new Date();
    var isInstall = (d.event === "install");
    var feats     = d.features || {};
    var notif     = feats.notifications || {};

    // Find the row for this install_id (columns are looked up by name).
    var row = -1, last = sheet.getLastRow();
    if (last >= 2 && C["install_id"]) {
      var ids = sheet.getRange(2, C["install_id"], last - 1, 1).getValues();
      for (var i = 0; i < ids.length; i++) {
        if (String(ids[i][0]) === String(id)) { row = i + 2; break; }
      }
    }

    function set(name, val) { if (C[name]) sheet.getRange(row, C[name]).setValue(val); }
    function get(name)      { return C[name] ? sheet.getRange(row, C[name]).getValue() : ""; }

    if (row === -1) {
      var blank = [];
      for (var k = 0; k < sheet.getLastColumn(); k++) blank.push("");
      sheet.appendRow(blank);
      row = sheet.getLastRow();
      set("first_seen", now);
      set("install_id", id);
      set("ping_count", 0);
      set("opted_in", false);
    }

    // Both ping types report these.
    set("last_seen", now);
    if (d.version)         set("version", d.version);
    if (d.router_model)    set("router_model", d.router_model);
    if (d.openwrt_version) set("openwrt_version", d.openwrt_version);

    if (!isInstall) {
      // Opt-in usage ping: count it, mark opted-in, record the feature flags.
      var prev = get("ping_count");
      set("ping_count", (typeof prev === "number" ? prev : 0) + 1);
      set("opted_in", true);
      set("adguard_connected", d.adguard_connected === true);
      if (typeof d.device_count === "number") set("device_count", d.device_count);
      set("social_profile", d.social_profile || "");
      set("screen_time", feats.screen_time === true);
      set("social_blocking", feats.social_blocking === true);
      set("bedtime_enabled", feats.bedtime_enabled === true);
      set("focus_times", feats.focus_times_enabled === true);
      set("notif_ntfy", notif.ntfy === true);
      set("notif_telegram", notif.telegram === true);
      set("notif_email", notif.email === true);
    }

    return jsonOut_({ ok: true, status: "success", event: isInstall ? "install" : "ping" });

  } catch (err) {
    return jsonOut_({ ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

function getSheet_() {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADER);
    sheet.setFrozenRows(1);
    return sheet;
  }
  // Migration-safe: add any missing canonical columns (e.g. opted_in) on the right.
  var have = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  for (var i = 0; i < HEADER.length; i++) {
    if (have.indexOf(HEADER[i]) === -1) {
      sheet.getRange(1, sheet.getLastColumn() + 1).setValue(HEADER[i]);
    }
  }
  return sheet;
}

function colMap_(sheet) {
  var hdr = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  var m = {};
  for (var i = 0; i < hdr.length; i++) m[hdr[i]] = i + 1; // 1-based column
  return m;
}

function jsonOut_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// Sanity check in a browser (GET the web app URL).
function doGet() {
  return jsonOut_({ ok: true, endpoint: "lanternwatch-installs" });
}
