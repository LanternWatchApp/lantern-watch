/**
 * Lantern Watch — install telemetry + update check (Google Apps Script web app).
 *
 * Behaviour:
 *   - Upserts one row per install_id into the "Installs" sheet (updated in place
 *     on repeat pings; never grows past one row per physical router).
 *   - Compares the router's reported version to LATEST_VERSION and tells it
 *     whether an update is available, pointing at the GitHub repo.
 *
 * Deploy: paste into the Apps Script editor bound to the telemetry spreadsheet,
 * Save, then Deploy -> Manage deployments -> (edit the existing web app) ->
 * New version -> Deploy. Editing in place keeps the same /exec URL so
 * config.py's UPDATE_CHECK_URL does not change.
 *
 * Bump LATEST_VERSION here whenever you ship a new release (keep it in sync
 * with VERSION in config.py).
 */

// This is a STANDALONE script (not bound to the sheet), so open the spreadsheet
// by ID. Set this to your telemetry spreadsheet's ID — the long token in the
// sheet URL between /d/ and /edit. (Kept out of the repo on purpose.)
var SPREADSHEET_ID = "YOUR_SPREADSHEET_ID";
var SHEET_NAME     = "Installs";
var LATEST_VERSION = "0.9.0-beta";
var UPDATE_URL     = "https://github.com/LanternWatchApp/lantern-watch";

var HEADER = [
  "first_seen", "install_id", "last_seen", "ping_count", "version",
  "router_model", "openwrt_version", "adguard_connected", "device_count",
  "social_profile", "screen_time", "social_blocking", "bedtime_enabled",
  "focus_times", "notif_ntfy", "notif_telegram", "notif_email"
];

function doPost(e) {
  // Serialize concurrent pings so two requests can't both miss the same
  // install_id and each append a row.
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (err) {
    return jsonOut_({ status: "error", ok: false, error: "Busy, could not obtain lock" });
  }

  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonOut_({ status: "error", ok: false, error: "No POST body" });
    }

    var data = JSON.parse(e.postData.contents);
    var installId = data.install_id;
    if (!installId) {
      return jsonOut_({ status: "error", ok: false, error: "Missing install_id" });
    }

    var sheet = getInstallsSheet_();
    var now = new Date();

    var features = data.features || {};
    var notif = features.notifications || {};

    // Columns E..Q (13 values), in sheet order, from the latest payload.
    var payloadCols = [
      data.version || "",                                   // E version
      data.router_model || "",                              // F router_model
      data.openwrt_version || "",                           // G openwrt_version
      data.adguard_connected === true,                      // H adguard_connected
      (typeof data.device_count === "number") ? data.device_count : "", // I device_count
      data.social_profile || "",                            // J social_profile
      features.screen_time === true,                        // K screen_time
      features.social_blocking === true,                    // L social_blocking
      features.bedtime_enabled === true,                    // M bedtime_enabled
      features.focus_times_enabled === true,                // N focus_times
      notif.ntfy === true,                                  // O notif_ntfy
      notif.telegram === true,                              // P notif_telegram
      notif.email === true                                  // Q notif_email
    ];

    // Find an existing row by install_id (column B), skipping the header.
    var rowIndex = -1;
    var lastRow = sheet.getLastRow();
    if (lastRow >= 2) {
      var ids = sheet.getRange(2, 2, lastRow - 1, 1).getValues(); // B2:B<last>
      for (var i = 0; i < ids.length; i++) {
        if (String(ids[i][0]) === String(installId)) {
          rowIndex = i + 2; // +1 for header, +1 for 0-based index
          break;
        }
      }
    }

    if (rowIndex === -1) {
      // New install: first_seen = last_seen = now, ping_count = 1, then E..Q.
      sheet.appendRow([now, installId, now, 1].concat(payloadCols));
    } else {
      // Existing install: bump last_seen + ping_count, overwrite E..Q. A untouched.
      var prev = sheet.getRange(rowIndex, 4).getValue();              // D ping_count
      var count = (typeof prev === "number" && prev > 0) ? prev + 1 : 1;
      sheet.getRange(rowIndex, 3).setValue(now);                      // C last_seen
      sheet.getRange(rowIndex, 4).setValue(count);                    // D ping_count
      sheet.getRange(rowIndex, 5, 1, payloadCols.length).setValues([payloadCols]); // E..Q
    }

    // Update-check result: any version mismatch -> point them at the repo.
    var currentVersion = data.version || "";
    return jsonOut_({
      status:           "success",
      ok:               true,
      current_version:  currentVersion,
      latest_version:   LATEST_VERSION,
      update_available: currentVersion !== LATEST_VERSION,
      update_url:       UPDATE_URL
    });

  } catch (err) {
    return jsonOut_({ status: "error", ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

function getInstallsSheet_() {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADER);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function jsonOut_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// Optional: sanity-check the deployment in a browser (GET the web app URL).
function doGet() {
  return jsonOut_({ status: "ok", endpoint: "lanternwatch-installs", latest_version: LATEST_VERSION });
}
