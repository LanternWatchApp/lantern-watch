/**
 * Lantern Watch — install + opt-in usage recorder (Google Apps Script web app).
 *
 * One row per router, upserted by install_id. Two payload kinds:
 *
 *   event:"install"  BASE heartbeat that EVERY router sends — opted in or not —
 *                    on boot and once a day. Records first_seen (once) and
 *                    refreshes last_seen, version, model, RAM, profile, OpenWrt.
 *                    Marks opted_in = FALSE. Does NOT touch ping_count.
 *
 *   event:"ping"     OPT-IN usage. Everything the base heartbeat does, plus:
 *                    bumps ping_count, marks opted_in = TRUE, and records the
 *                    feature flags, device count, and chosen DNS tier.
 *
 * READING THE SHEET:
 *   - last_seen is the liveness signal. Every router refreshes it daily, so a
 *     stale last_seen means genuinely gone.
 *   - ping_count is NOT liveness — it only counts opt-in pings, so a healthy
 *     router that never opted in sits at 0 forever. Never prune on ping_count.
 *   - opted_in follows the live state: opting back out flips it to FALSE.
 *   - install_id is a random per-install UUID (never MAC-derived), so a factory
 *     reset legitimately creates a NEW row. Rows are install events, not unique
 *     hardware.
 *
 * SCALE: each request holds the script lock for one read + one batched write
 * (not ~20 per-cell writes) and finds its row with TextFinder, so the critical
 * section stays tiny. Combined with the client-side daily jitter (each router
 * pings at a stable minute-of-day derived from its ID), a fleet of thousands
 * spreads to well under one ping/minute.
 *
 * SAFE FOR YOUR OWN COLUMNS: this only ever reads/writes the canonical columns
 * below. Add helper columns (e.g. a "days since last seen" formula) to the RIGHT
 * and they will never be overwritten.
 *
 * Deploy: paste, Save, then Deploy -> Manage deployments -> (edit the existing
 * web app) -> New version -> Deploy. Editing in place keeps the same /exec URL.
 */

var SPREADSHEET_ID = "1nQa9L6tIWXxl1iH_En5mEVJHkaEsRQ5r7d32Iqu_0W8";
var SHEET_NAME     = "Installs";

var HEADER = [
  // ── Base: every install reports these, opted in or not ──
  "last_seen", "first_seen", "install_id", "version",
  "router_model", "ram_mb", "protection_profile", "openwrt_version",
  "opted_in", "ping_count",
  // ── Opt-in only: populated when "Share anonymous usage stats" is on ──
  "adguard_connected", "device_count", "social_profile", "lite_dns_tier",
  "screen_time", "social_blocking", "bedtime_enabled", "focus_times",
  "notif_ntfy", "notif_telegram", "notif_email"
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

    // Only ever read/write the CANONICAL columns, so any helper column the user
    // adds to the right survives. This handler reads a whole row and writes it
    // back in one batched update, and getValues() returns COMPUTED values — so
    // spanning the full sheet width would silently replace a user's formula with
    // a frozen number on every ping.
    var width = 0;
    for (var hi = 0; hi < HEADER.length; hi++) {
      if (C[HEADER[hi]] && C[HEADER[hi]] > width) width = C[HEADER[hi]];
    }
    if (!width) width = sheet.getLastColumn();

    // Locate this install_id's row with TextFinder (fast even at thousands of rows).
    var row = -1, last = sheet.getLastRow();
    if (last >= 2 && C["install_id"]) {
      var found = sheet.getRange(2, C["install_id"], last - 1, 1)
                       .createTextFinder(String(id)).matchEntireCell(true).findNext();
      if (found) row = found.getRow();
    }

    // New rows go to getLastRow()+1 with real content — never appendRow()+
    // getLastRow(), which silently overwrites the last row when the appended row
    // is all-empty (that bug made the sheet hold only one router).
    var isNew = (row === -1), vals;
    if (isNew) {
      row  = last + 1;
      vals = new Array(width).fill("");
      set_(vals, C, "first_seen", now);
      set_(vals, C, "install_id", id);
      set_(vals, C, "ping_count", 0);
    } else {
      vals = sheet.getRange(row, 1, 1, width).getValues()[0];
    }

    // ── Base fields — refreshed by EVERY ping, install or opt-in ──
    set_(vals, C, "last_seen", now);
    set_(vals, C, "opted_in", !isInstall);   // the event is the source of truth
    if (d.version)                        set_(vals, C, "version", d.version);
    if (d.router_model)                   set_(vals, C, "router_model", d.router_model);
    if (d.openwrt_version)                set_(vals, C, "openwrt_version", d.openwrt_version);
    if (d.protection_profile)             set_(vals, C, "protection_profile", d.protection_profile);
    if (typeof d.ram_mb === "number" && d.ram_mb > 0) set_(vals, C, "ram_mb", d.ram_mb);

    // ── Opt-in only ──
    if (!isInstall) {
      var prev = get_(vals, C, "ping_count");
      var n    = (typeof prev === "number") ? prev : (Number(prev) || 0);
      set_(vals, C, "ping_count", n + 1);
      set_(vals, C, "adguard_connected", d.adguard_connected === true);
      if (typeof d.device_count === "number") set_(vals, C, "device_count", d.device_count);
      set_(vals, C, "social_profile",  d.social_profile  || "");
      set_(vals, C, "lite_dns_tier",   d.lite_dns_tier   || "");
      set_(vals, C, "screen_time",     feats.screen_time        === true);
      set_(vals, C, "social_blocking", feats.social_blocking    === true);
      set_(vals, C, "bedtime_enabled", feats.bedtime_enabled    === true);
      set_(vals, C, "focus_times",     feats.focus_times_enabled === true);
      set_(vals, C, "notif_ntfy",      notif.ntfy     === true);
      set_(vals, C, "notif_telegram",  notif.telegram === true);
      set_(vals, C, "notif_email",     notif.email    === true);
    }

    sheet.getRange(row, 1, 1, width).setValues([vals]);
    return jsonOut_({ ok: true, status: "success",
                      event: isInstall ? "install" : "ping", created: isNew, row: row });

  } catch (err) {
    return jsonOut_({ ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

function getSheet_() {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  // Empty sheet (or freshly cleared): lay down the header and freeze it.
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADER);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADER.length).setFontWeight("bold");
    return sheet;
  }
  // Migration-safe: add any missing canonical columns on the right. Existing
  // order is preserved; colMap_ looks everything up by name, so order never
  // matters. NOTE: a newly added column lands to the right of any helper column
  // you've added — move your helper back to the far right if that ever happens.
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
  for (var i = 0; i < hdr.length; i++) if (hdr[i] && !(hdr[i] in m)) m[hdr[i]] = i + 1; // 1-based
  return m;
}

// Write into / read from an in-memory row array by column name.
function set_(vals, C, name, val) { if (C[name]) vals[C[name] - 1] = val; }
function get_(vals, C, name)      { return C[name] ? vals[C[name] - 1] : ""; }

function jsonOut_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// Sanity check in a browser (GET the web app URL) — also reports the row count.
function doGet() {
  var rows = 0;
  try { rows = Math.max(0, getSheet_().getLastRow() - 1); } catch (err) {}
  return jsonOut_({ ok: true, endpoint: "lanternwatch-installs", rows: rows });
}
