// ============================================================
// HONEYCOMB ADS DASHBOARD — APPS SCRIPT API LAYER
// ============================================================
//
// Adds JSON API endpoints to the existing Apps Script project
// so the webapp dashboard in /webapp/index.html can read from
// the Google Sheet. No existing functions are changed or
// overwritten. The budget approve/reject flow keeps working
// exactly as it does today.
//
// ─── HOW TO INSTALL (≈90 seconds, two steps) ────────────────
//
// STEP 1 — PASTE THIS FILE AT THE BOTTOM OF YOUR SCRIPT
//   a. Open the Apps Script editor.
//   b. Scroll to the very bottom of honeycomb_ads_intelligence_final.js
//      (or whichever file holds the existing doGet function).
//   c. Copy EVERYTHING in this file below the "──── BEGIN PASTE ────"
//      marker and paste it at the bottom.
//   d. Press Cmd+S (Mac) or Ctrl+S (Windows) to save.
//
// STEP 2 — ADD TWO LINES TO THE EXISTING doGet FUNCTION
//   a. In the same Apps Script file, press Cmd+F / Ctrl+F and
//      search for:    function doGet(e)
//   b. Immediately inside the opening curly brace, add these
//      two lines as the very first thing the function does:
//
//        var dashboardResponse = handleDashboardApi_(e);
//        if (dashboardResponse) return dashboardResponse;
//
//      The result should look like this:
//
//        function doGet(e) {
//          var dashboardResponse = handleDashboardApi_(e);
//          if (dashboardResponse) return dashboardResponse;
//
//          // ... all the existing approve/reject code stays here,
//          //     completely untouched ...
//        }
//
//   c. Save again (Cmd+S / Ctrl+S).
//
// STEP 3 — REDEPLOY THE WEB APP
//   a. Click the blue "Deploy" button in the top-right.
//   b. Choose "Manage deployments".
//   c. Click the pencil icon on the existing deployment.
//   d. Under "Version", pick "New version".
//   e. Click "Deploy".
//   f. Copy the "Web app URL" shown (ends in /exec). This is
//      the same URL your Slack approve/reject links already
//      use — it does not change.
//
// STEP 4 — CONNECT THE DASHBOARD
//   a. Open the dashboard in your browser.
//   b. Click "Connect API" in the top-right corner.
//   c. Paste the /exec URL you just copied.
//   d. Click "Save". The dashboard switches from mock data
//      to real data instantly.
//
// ─── TESTING ────────────────────────────────────────────────
// After Step 3 you can sanity-check by visiting this URL in
// your browser (replacing WEB_APP_URL with the /exec URL):
//     WEB_APP_URL?action=mappings
// You should see a JSON list of your campaign mappings.
//
// ─── SAFETY / ROLLBACK ──────────────────────────────────────
// Apps Script keeps version history automatically. If anything
// behaves oddly, go to File → See version history and restore
// the previous version. None of the changes below modify data
// — every new function here is read-only.
// ============================================================


// ──────────────────────────── BEGIN PASTE ────────────────────────────


// ─── DASHBOARD API ROUTER ───────────────────────────────────
// Returns a Response object for dashboard actions, or null
// for anything else (so the existing doGet can keep handling
// approve/reject links unchanged).
function handleDashboardApi_(e) {
  var action = e && e.parameter && e.parameter.action;

  var dashboardActions = {
    rollup: true, daily: true, mappings: true,
    narrative: true, summary: true, campaigns: true
  };

  if (!action || !dashboardActions[action]) return null;

  try {
    var result;
    switch (action) {
      case 'rollup':
        result = sheetToObjects_(ROLLUP_SHEET);
        break;
      case 'daily':
        result = getDailyData_(e.parameter.start, e.parameter.end);
        break;
      case 'mappings':
        result = sheetToObjects_(MAPPING_SHEET);
        break;
      case 'narrative':
        result = getLatestNarrative_();
        break;
      case 'summary':
        result = getSummary_(e.parameter.start, e.parameter.end);
        break;
      case 'campaigns':
        result = getCampaignList_();
        break;
    }
    return jsonResponse_(result);

  } catch (err) {
    Logger.log('handleDashboardApi_ exception: ' + err.message + '\n' + err.stack);
    return jsonResponse_({
      error: err.message,
      stack: err.stack
    });
  }
}


function jsonResponse_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}


// ─── GENERIC SHEET → JSON CONVERTER ─────────────────────────
// Reads a sheet, normalizes headers to snake_case keys, and
// returns an array of row objects. Dates are formatted as
// yyyy-MM-dd strings so the JSON is trivially consumable.
function sheetToObjects_(sheetName) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(sheetName);
  if (!sheet) return [];

  var data = sheet.getDataRange().getValues();
  if (data.length < 2) return [];

  var headers = data[0].map(function(h) {
    return String(h).trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  });

  var rows = [];
  for (var i = 1; i < data.length; i++) {
    var row = {};
    var hasValue = false;
    for (var j = 0; j < headers.length; j++) {
      var v = data[i][j];
      if (v instanceof Date) {
        v = Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
      }
      if (v !== '' && v !== null) hasValue = true;
      row[headers[j]] = v === '' ? null : v;
    }
    if (hasValue) rows.push(row);
  }
  return rows;
}


// ─── DAILY DATA (rolling_data, date-filtered) ───────────────
// Returns per-day per-campaign rows. This is the main dataset
// the dashboard uses for the date-range charts and tables.
function getDailyData_(start, end) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(META_SHEET);
  if (!sheet) return [];

  var data = sheet.getDataRange().getValues();
  if (data.length < 2) return [];

  var startStr = start || '1900-01-01';
  var endStr   = end   || '2999-12-31';

  var rows = [];
  for (var i = 1; i < data.length; i++) {
    var dateStr = dateToYMD_(data[i][0]);
    if (!dateStr || dateStr < startStr || dateStr > endStr) continue;

    rows.push({
      date:          dateStr,
      month:         String(data[i][1] || ''),
      week:          Number(data[i][2]) || 0,
      campaign_name: String(data[i][3] || ''),
      campaign_id:   String(data[i][4] || ''),
      impressions:   Number(data[i][5]) || 0,
      clicks:        Number(data[i][6]) || 0,
      spend:         Number(data[i][7]) || 0,
      reach:         Number(data[i][8]) || 0,
      conversions:   Number(data[i][9]) || 0,
      frequency:     Number(data[i][10]) || 0,
      cpl:           (data[i][11] === '' || data[i][11] == null) ? null : Number(data[i][11])
    });
  }
  return rows;
}


// ─── LATEST WEEKLY NARRATIVE (intelligence_log) ─────────────
function getLatestNarrative_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(INTEL_SHEET);
  if (!sheet || sheet.getLastRow() < 2) return null;

  var last = sheet.getLastRow();
  var row = sheet.getRange(last, 1, 1, 7).getValues()[0];

  return {
    generated_at:   row[0] instanceof Date ? row[0].toISOString() : String(row[0]),
    reporting_week: row[1] instanceof Date
      ? Utilities.formatDate(row[1], Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : String(row[1]),
    total_spend:    Number(row[2]) || 0,
    total_icps:     Number(row[3]) || 0,
    overall_cpicp:  String(row[4] || ''),
    narrative:      String(row[6] || '')
  };
}


// ─── SUMMARY (aggregated totals for a date range) ───────────
// Server-side aggregation so the client can show topline
// numbers without pulling every row. The client also computes
// totals itself from /daily, so this is optional but cheap.
function getSummary_(start, end) {
  var tz = Session.getScriptTimeZone();
  if (!end)   end   = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  if (!start) {
    var s = new Date();
    s.setDate(s.getDate() - 30);
    start = Utilities.formatDate(s, tz, 'yyyy-MM-dd');
  }

  var daily = getDailyData_(start, end);
  var totals = { spend: 0, conversions: 0, clicks: 0, impressions: 0 };
  daily.forEach(function(r) {
    totals.spend       += r.spend;
    totals.conversions += r.conversions;
    totals.clicks      += r.clicks;
    totals.impressions += r.impressions;
  });

  return {
    start: start,
    end:   end,
    rows:  daily.length,
    totals: totals,
    cpl: totals.conversions > 0 ? totals.spend / totals.conversions : null,
    ctr: totals.impressions > 0 ? totals.clicks / totals.impressions : null
  };
}


// ─── CAMPAIGN LIST (distinct campaigns from rolling_data) ───
// Returns one entry per campaign with last-active date,
// useful for filter dropdowns in the UI.
function getCampaignList_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(META_SHEET);
  if (!sheet) return [];

  var data = sheet.getDataRange().getValues();
  var byCampaign = {};
  for (var i = 1; i < data.length; i++) {
    var name = String(data[i][3] || '').trim();
    var id   = String(data[i][4] || '').trim();
    var date = dateToYMD_(data[i][0]);
    if (!name || !id) continue;
    if (!byCampaign[id] || date > byCampaign[id].last_active) {
      byCampaign[id] = byCampaign[id] || { campaign_id: id, campaign_name: name, last_active: date };
      byCampaign[id].last_active = date;
    }
  }
  return Object.keys(byCampaign).map(function(k) { return byCampaign[k]; });
}


// ───────────────────────────── END PASTE ─────────────────────────────
