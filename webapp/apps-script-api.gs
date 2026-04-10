// ============================================================
// HONEYCOMB ADS DASHBOARD — APPS SCRIPT API LAYER
// ============================================================
//
// This file extends the existing doGet() in
// honeycomb_ads_intelligence_final.js with JSON API endpoints
// consumed by the webapp dashboard in /webapp/index.html.
//
// HOW TO INSTALL:
// 1. Open the Apps Script project (the link in the handoff).
// 2. Replace the existing doGet() function with the version
//    below. The budget approve/reject logic is preserved by
//    delegating to handleBudgetApproval_().
// 3. Paste the remaining helpers (handleDashboardApi_,
//    sheetToObjects_, getDailyData_, etc.) at the bottom of
//    the file.
// 4. Redeploy the web app: Deploy → Manage deployments →
//    select the existing deployment → pencil icon → New
//    version → Deploy. The /exec URL stays the same.
// 5. Test in the browser:
//      <WEB_APP_URL>?action=mappings
//    Should return a JSON array.
//
// SECURITY NOTES:
// - Deployment access controls who can call these endpoints.
//   For internal-only use, set "Who has access" to "Anyone
//   within <workspace>". For sharing with an external
//   agency, use "Anyone with the link" — the data exposed
//   is aggregate ad performance only (no PII).
// - No data is written by these endpoints. They are read-only.
// ============================================================


// ─── ENTRY POINT ────────────────────────────────────────────
// Replaces the existing doGet(). Routes dashboard API calls
// to handleDashboardApi_() and everything else (approve,
// reject) to handleBudgetApproval_().
function doGet(e) {
  var action = e && e.parameter && e.parameter.action;

  var dashboardActions = {
    rollup: true, daily: true, mappings: true,
    narrative: true, summary: true, campaigns: true
  };

  if (action && dashboardActions[action]) {
    return handleDashboardApi_(e);
  }

  return handleBudgetApproval_(e);
}


// ─── DASHBOARD API ROUTER ───────────────────────────────────
function handleDashboardApi_(e) {
  try {
    var action = e.parameter.action;
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
      default:
        result = { error: 'Unknown action: ' + action };
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
// numbers without pulling every row. The client also
// computes totals itself from /daily, so this is optional
// but cheap.
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


// ─── BUDGET APPROVAL HANDLER (unchanged behavior) ───────────
// This is the existing doGet() body, renamed. It handles the
// ?action=approve and ?action=reject links from the weekly
// budget proposal Slack message.
function handleBudgetApproval_(e) {
  var action = e && e.parameter && e.parameter.action;
  var token  = e && e.parameter && e.parameter.token;

  if (!token || !action) {
    return HtmlService.createHtmlOutput(
      '<h2>Invalid link.</h2><p>Missing action or token parameter.</p>');
  }

  var pendingToken = PROPS.getProperty('BUDGET_PENDING_TOKEN');

  if (!pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>No pending budget proposal.</h2>' +
      '<p>This proposal may have already been actioned or expired.</p>');
  }

  if (token !== pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>Token mismatch.</h2>' +
      '<p>This link is invalid or has already been used.</p>');
  }

  var user = Session.getActiveUser().getEmail() || 'unknown user';

  if (action === 'approve') {
    PROPS.setProperty('BUDGET_APPROVED_TOKEN', token);
    postToSlack_('*Honeycomb Budget* ✅ Approved by ' + user +
                 '. Changes will execute tonight at 3:00 AM.');
    return HtmlService.createHtmlOutput(
      '<h2>✅ Budget changes approved.</h2>' +
      '<p>Changes will execute tonight at 3:00 AM. ' +
      'You\'ll receive a Slack confirmation when complete.</p>');
  }

  if (action === 'reject') {
    PROPS.setProperty('BUDGET_REJECTED_TOKEN', token);
    postToSlack_('*Honeycomb Budget* ❌ Rejected by ' + user +
                 '. No changes will be applied this cycle.');
    return HtmlService.createHtmlOutput(
      '<h2>❌ Budget changes rejected.</h2>' +
      '<p>No budget changes will be made this cycle.</p>');
  }

  return HtmlService.createHtmlOutput('<h2>Unknown action.</h2>');
}
