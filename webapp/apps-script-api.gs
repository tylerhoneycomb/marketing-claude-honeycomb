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
//
// ─── UPDATING AN EXISTING INSTALL ───────────────────────────
// When this file changes (e.g. a new feature like the chat
// backend is added), the simplest re-install path is:
//   1. In your Code.gs, find the start of `handleDashboardApi_`.
//      Select everything from that function down through the
//      very last helper (currently `buildDashboardContext_`).
//   2. Delete that entire block.
//   3. Paste the current contents of the BEGIN PASTE block
//      below in its place.
//   4. Redeploy as a new version (Deploy → Manage deployments
//      → pencil → New version → Deploy). The /exec URL does
//      not change.
// Your two-line patch inside the existing doGet stays intact
// across updates — you only need to re-do it if you
// accidentally delete it.
//
// The chat backend ALSO needs a new `doPost` function. If
// your Code.gs doesn't already have one, the one below will
// add it. If it does, merge the `if (action === 'chat')`
// branch into your existing doPost manually.
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
    narrative: true, summary: true, campaigns: true,
    chat: true,
    run_budget_analysis: true,
    get_spend_goal: true,
    set_spend_goal: true,
    // Slack-safe approval flow. The bare approve/reject
    // actions show a confirmation page only (no side effect),
    // so Slack's unfurl crawler can't accidentally fire them.
    // The actual state change happens on confirm_approve /
    // confirm_reject, which only a human clicking a button
    // can trigger.
    approve: true,
    reject: true,
    confirm_approve: true,
    confirm_reject: true
  };

  if (!action || !dashboardActions[action]) return null;

  // Chat returns its own Response object.
  if (action === 'chat') {
    return handleChatRequest_(e);
  }

  // Approve/reject links from Slack — two-step to defeat
  // the Slack link-unfurling crawler. Crawler hits approve
  // or reject, gets an HTML confirmation page, and stops.
  // Only a human clicking the button triggers the real
  // state change.
  if (action === 'approve' || action === 'reject') {
    return showApprovalConfirmationPage_(e, action);
  }
  if (action === 'confirm_approve' || action === 'confirm_reject') {
    return applyApprovalDecision_(e, action.replace('confirm_', ''));
  }

  // Budget analysis trigger — calls the existing
  // runBudgetAnalysis() function from the intelligence
  // layer. This is the same function the Wed/Fri cron
  // trigger calls; we're just letting the dashboard
  // invoke it on demand.
  if (action === 'run_budget_analysis') {
    try {
      runBudgetAnalysis();
      return jsonResponse_({
        ok: true,
        message: 'Budget analysis complete. Check Slack for the proposal.'
      });
    } catch (err) {
      Logger.log('run_budget_analysis error: ' + err.message);
      return jsonResponse_({
        error: 'Budget analysis failed: ' + err.message
      });
    }
  }

  // Read the current spend goal + tolerance. These
  // default to the hardcoded constants but can be
  // overridden via Script Properties so the dashboard
  // can adjust them without a code change.
  if (action === 'get_spend_goal') {
    var goalOverride      = PROPS.getProperty('DASHBOARD_TARGET_WEEKLY_SPEND');
    var toleranceOverride = PROPS.getProperty('DASHBOARD_WEEKLY_SPEND_TOLERANCE');
    return jsonResponse_({
      target_weekly_spend:    goalOverride      ? Number(goalOverride)      : TARGET_WEEKLY_SPEND,
      weekly_spend_tolerance: toleranceOverride  ? Number(toleranceOverride) : WEEKLY_SPEND_TOLERANCE,
      source: goalOverride ? 'script_property_override' : 'hardcoded_default'
    });
  }

  // Write new spend goal + tolerance to Script Properties.
  // Does NOT modify the code constants — stores overrides
  // that the budget optimizer reads at runtime.
  if (action === 'set_spend_goal') {
    var newTarget    = e.parameter.target;
    var newTolerance = e.parameter.tolerance;
    if (newTarget != null && !isNaN(Number(newTarget)) && Number(newTarget) > 0) {
      PROPS.setProperty('DASHBOARD_TARGET_WEEKLY_SPEND', String(Number(newTarget)));
    }
    if (newTolerance != null && !isNaN(Number(newTolerance)) && Number(newTolerance) >= 0) {
      PROPS.setProperty('DASHBOARD_WEEKLY_SPEND_TOLERANCE', String(Number(newTolerance)));
    }
    // Read back the saved values to confirm.
    var savedTarget    = PROPS.getProperty('DASHBOARD_TARGET_WEEKLY_SPEND');
    var savedTolerance = PROPS.getProperty('DASHBOARD_WEEKLY_SPEND_TOLERANCE');
    return jsonResponse_({
      ok: true,
      target_weekly_spend:    savedTarget    ? Number(savedTarget)    : TARGET_WEEKLY_SPEND,
      weekly_spend_tolerance: savedTolerance ? Number(savedTolerance) : WEEKLY_SPEND_TOLERANCE
    });
  }

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
      date:           dateStr,
      month:          String(data[i][1] || ''),
      week:           Number(data[i][2]) || 0,
      campaign_name:  String(data[i][3] || ''),
      campaign_id:    String(data[i][4] || ''),
      impressions:    Number(data[i][5]) || 0,
      clicks:         Number(data[i][6]) || 0,
      spend:          Number(data[i][7]) || 0,
      reach:          Number(data[i][8]) || 0,
      conversions:    Number(data[i][9]) || 0,
      frequency:      Number(data[i][10]) || 0,
      cpl:            (data[i][11] === '' || data[i][11] == null) ? null : Number(data[i][11]),
      // IC Conversions live in column 12 (added in the IC
      // conversion tracking change). Defaults to 0 for older
      // rows that predate the column.
      ic_conversions: Number(data[i][12]) || 0
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


// ─── CHAT BACKEND (Hive Mind) ───────────────────────────────
// A lightweight chat endpoint the dashboard uses to let the
// user interrogate their own campaign data. The browser
// never sees the Anthropic API key — all requests are
// forwarded from here using the key already stored in
// Script Properties.
//
// Flow:
//   1. Browser POSTs { action: 'chat', message, history }
//      to the Web App URL as application/x-www-form-urlencoded
//   2. doPost routes to handleChatRequest_
//   3. handleChatRequest_ builds a compact "data context"
//      block from the live sheet and prepends it to a
//      system prompt that establishes CPICP as the primary
//      KPI and defines its formula
//   4. Calls claude-sonnet-4-6 with system + history + user
//   5. Returns { reply: '...' } as JSON

function doPost(e) {
  var action = e && e.parameter && e.parameter.action;

  if (action === 'chat') {
    return handleChatRequest_(e);
  }

  return jsonResponse_({ error: 'Unknown POST action: ' + action });
}


function handleChatRequest_(e) {
  try {
    if (!ANTHROPIC_API_KEY) {
      return jsonResponse_({
        error: 'ANTHROPIC_API_KEY is not set in Script Properties. Open Project Settings → Script Properties and add it.'
      });
    }

    var userMessage = String((e && e.parameter && e.parameter.message) || '').trim();
    if (!userMessage) {
      return jsonResponse_({
        error: 'Empty message. Type something before sending.'
      });
    }
    // Guard against abuse/runaway payloads.
    if (userMessage.length > 4000) {
      return jsonResponse_({
        error: 'Message too long (over 4,000 characters). Try breaking it into smaller questions.'
      });
    }

    var historyRaw = (e && e.parameter && e.parameter.history) || '[]';
    var history;
    try {
      history = JSON.parse(historyRaw);
    } catch (parseErr) {
      Logger.log('handleChatRequest_: malformed history JSON, ignoring. Raw: ' +
                 String(historyRaw).substring(0, 200));
      history = [];
    }
    if (!Array.isArray(history)) history = [];

    // Cap history length to protect token budget.
    if (history.length > 30) {
      history = history.slice(-30);
    }

    var contextBlock = buildDashboardContext_();

    var systemPrompt = [
      'You are "Hive Mind," an analytics assistant for the Honeycomb Credit marketing team.',
      'The user is looking at their Meta ads dashboard and wants help interpreting the underlying data.',
      '',
      'ABOUT HONEYCOMB CREDIT:',
      'Honeycomb Credit is a regulated investment crowdfunding platform (Reg CF). Small food and beverage businesses raise capital from their own customers via revenue-sharing notes. The marketing team runs Meta (Facebook/Instagram) ad campaigns to generate qualified applicants (ICPs) who want to raise money on the platform.',
      '',
      'PRIMARY METRIC — CPICP (this is the most important thing to know):',
      'CPICP stands for "Cost Per ICP." It is the primary efficiency metric for the marketing team. Every decision about budgets, creatives, and campaign allocation is made through the lens of CPICP.',
      '',
      'Definition:',
      '  ICP = HubSpot contact decisioned as "investment_crowdfunding" (a small business that qualifies to raise capital on Honeycomb).',
      '  CPICP = Total Meta spend ÷ Estimated ICPs, where "Estimated ICPs" uses a hybrid attribution model:',
      '    Estimated ICPs per campaign = (hard UTM-matched ICPs for that campaign)',
      "                                  + (that campaign's proportional share of the unattributed ICP pool,",
      '                                     weighted by its share of Meta conversion volume)',
      '',
      'Why hybrid attribution: not every ICP comes through with a matching utm_campaign tag. Campaigns that do get UTM credit AND receive a proportional slice of the remainder, rewarding campaigns with strong tracking without abandoning the campaigns whose ICPs lost their UTM somewhere in the funnel.',
      '',
      'IC CONVERSIONS — a supplementary signal, NOT a replacement for CPICP:',
      'The weekly_rollup has a separate ic_conversions column that tracks the Meta custom conversion event "Investment Crowdfunding Prequal Decision." The campaign_mapping sheet has a conversion_event column that flags which campaigns are actually optimized against this event.',
      '',
      'CRITICAL: estimated_icps is the hybrid attribution output for EVERY campaign, including IC-optimized ones. CPICP is computed the same way for all campaigns: spend ÷ estimated_icps. IC conversions do NOT replace estimated_icps — they are a parallel signal you can cross-reference.',
      '',
      'ATTRIBUTION SPILLOVER: ic_conversions can appear on non-IC-optimized campaigns too. Example: a Bakeries lead who later reaches a prequal decision of investment_crowdfunding will have the IC event credited back to the Bakeries campaign by Meta. So a non-zero ic_conversions count on Bakeries does NOT mean Bakeries is an IC campaign. Use campaign_mapping.conversion_event as the source of truth for identifying actually-IC-optimized campaigns.',
      '',
      'For IC-optimized campaigns specifically: their ic_conversions is the direct conversion signal Meta is optimizing against. CPL (spend ÷ lead conversions) is less meaningful for them because their primary conversion event is the IC decision, not a generic lead. Treat CPICP (using hybrid estimated_icps) as the main efficiency metric for all campaigns, and reference ic_conversions as context for IC-optimized ones.',
      '',
      'Lower CPICP is better. The team has a rough sense that CPICP under $120 is healthy and CPICP above $200 warrants investigation.',
      '',
      'SECONDARY METRICS (know what they mean, use them as supporting evidence, not headline):',
      '- Blended CPICP: same as CPICP above — "blended" just emphasizes the hybrid attribution model',
      '- Attributed CPICP: spend ÷ hard UTM-matched ICPs only. Less inclusive.',
      '- Attribution Rate: share of estimated ICPs that came with a UTM tag. Below 50% means tracking is leaky.',
      '- CPL: Cost per Lead using Meta-reported conversions (spend ÷ Meta conversions). Less accurate than CPICP.',
      '- CTR: click-through rate. Creative quality signal.',
      '- Frequency: avg ad exposures per unique reach. Above 3.5 = audience saturation risk.',
      '',
      'HOW TO RESPOND:',
      '- Be concise. Think "quick Slack message," not "long email."',
      '- Cite specific numbers from the data below when possible. Avoid vague language.',
      '- When asked for recommendations, base them on CPICP first, then trend direction, then attribution quality.',
      '- No guaranteed returns, no investment advice, no claims about expected APY — this is a regulated platform.',
      "- If the data doesn't answer the question, say so plainly. Do not invent numbers.",
      '',
      '──────────────────────────────────────────',
      'CURRENT DASHBOARD DATA (live read from Google Sheet):',
      '──────────────────────────────────────────',
      contextBlock
    ].join('\n');

    // Build messages array for Anthropic. Only user/assistant
    // turns — the system prompt goes in the `system` field.
    var messages = [];
    history.forEach(function(m) {
      if (m && (m.role === 'user' || m.role === 'assistant') && m.content) {
        messages.push({ role: m.role, content: String(m.content) });
      }
    });
    messages.push({ role: 'user', content: userMessage });

    var response = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
      method:  'POST',
      headers: {
        'x-api-key':         ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type':      'application/json'
      },
      payload: JSON.stringify({
        model:      'claude-sonnet-4-6',
        max_tokens: 1500,
        system:     systemPrompt,
        messages:   messages
      }),
      muteHttpExceptions: true
    });

    var code = response.getResponseCode();
    var json;
    try {
      json = JSON.parse(response.getContentText());
    } catch (parseErr) {
      return jsonResponse_({
        error: 'Invalid response from Anthropic (HTTP ' + code + '): ' + response.getContentText().substring(0, 200)
      });
    }

    if (code !== 200) {
      // Classify the error so the frontend's
      // classifyFetchFailure() helper can match it to a
      // specific user-facing message with actionable advice.
      var apiMsg = (json && json.error && json.error.message) || 'HTTP ' + code;
      var apiType = (json && json.error && json.error.type) || '';

      if (code === 401 || code === 403 || /authentication|invalid.?api.?key/i.test(apiMsg)) {
        return jsonResponse_({
          error: 'Anthropic API authentication failed: ' + apiMsg +
                 '. Check ANTHROPIC_API_KEY in Script Properties.'
        });
      }
      if (code === 429 || apiType === 'rate_limit_error' || /rate.?limit/i.test(apiMsg)) {
        return jsonResponse_({
          error: 'Anthropic rate limit hit: ' + apiMsg +
                 '. Wait a moment and try again.'
        });
      }
      if (code === 400 || apiType === 'invalid_request_error') {
        return jsonResponse_({
          error: 'Anthropic rejected the request (HTTP 400): ' + apiMsg +
                 '. This usually means the conversation history is too long or malformed.'
        });
      }
      if (code >= 500) {
        return jsonResponse_({
          error: 'Anthropic server error (HTTP ' + code + '): ' + apiMsg +
                 '. Try again in a moment — this is usually transient.'
        });
      }
      return jsonResponse_({ error: 'Anthropic API error (HTTP ' + code + '): ' + apiMsg });
    }

    var reply = json && json.content && json.content[0] && json.content[0].text;
    if (!reply) {
      return jsonResponse_({
        error: 'Anthropic returned no content. Response shape: ' +
               JSON.stringify(json).substring(0, 200)
      });
    }

    return jsonResponse_({ reply: reply });

  } catch (err) {
    Logger.log('handleChatRequest_ exception: ' + err.message + '\n' + err.stack);
    // Apps Script's UrlFetchApp can throw on network/timeout
    // failures. Classify the common ones.
    var errMsg = String(err.message || err);
    if (/timeout/i.test(errMsg)) {
      return jsonResponse_({
        error: 'Request to Anthropic timed out. Apps Script has a 60-second URL fetch limit. ' +
               'Try asking a shorter question or clearing conversation history.'
      });
    }
    if (/DNS|address/i.test(errMsg)) {
      return jsonResponse_({
        error: 'Could not reach the Anthropic API. This is usually transient — try again.'
      });
    }
    return jsonResponse_({ error: 'Chat backend error: ' + errMsg });
  }
}


// Builds a compact, LLM-friendly snapshot of the live data
// that backs the dashboard. Included in every chat request
// so Claude can answer with real numbers rather than guesses.
//
// Sources:
//   - weekly_rollup (last ~40 rows, tab-separated)
//   - campaign_mapping (all rows)
//   - intelligence_log (latest narrative only)
function buildDashboardContext_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var lines = [];

  // ── Weekly rollup (most recent) ─────────────────────
  var rollupSheet = ss.getSheetByName(ROLLUP_SHEET);
  if (rollupSheet && rollupSheet.getLastRow() > 1) {
    var data = rollupSheet.getDataRange().getValues();
    var headers = data[0];
    var maxRows = 40;
    var rows = data.slice(Math.max(1, data.length - maxRows));
    lines.push('WEEKLY ROLLUP (most recent ' + rows.length + ' rows, tab-separated):');
    lines.push(headers.join('\t'));
    rows.forEach(function(r) {
      lines.push(r.map(function(v) {
        if (v instanceof Date) {
          return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
        }
        return v === null || v === undefined ? '' : v;
      }).join('\t'));
    });
    lines.push('');
  }

  // ── Campaign mappings ───────────────────────────────
  var mappingSheet = ss.getSheetByName(MAPPING_SHEET);
  if (mappingSheet && mappingSheet.getLastRow() > 1) {
    var mData = mappingSheet.getDataRange().getValues();
    lines.push('CAMPAIGN → UTM MAPPINGS (' + (mData.length - 1) + ' rows):');
    lines.push(mData[0].join('\t'));
    for (var mi = 1; mi < mData.length; mi++) {
      lines.push(mData[mi].join('\t'));
    }
    lines.push('');
  }

  // ── Latest narrative ────────────────────────────────
  var intelSheet = ss.getSheetByName(INTEL_SHEET);
  if (intelSheet && intelSheet.getLastRow() > 1) {
    var lastRow = intelSheet.getRange(intelSheet.getLastRow(), 1, 1, 7).getValues()[0];
    lines.push('MOST RECENT WEEKLY NARRATIVE:');
    lines.push('Week: ' + (lastRow[1] instanceof Date
      ? Utilities.formatDate(lastRow[1], Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : String(lastRow[1])));
    lines.push('Total spend: $' + lastRow[2]);
    lines.push('Estimated ICPs: ' + lastRow[3]);
    lines.push('Overall CPICP: $' + lastRow[4]);
    lines.push('---');
    lines.push(String(lastRow[6] || ''));
    lines.push('');
  }

  return lines.join('\n');
}


// ─── SPEND GOAL RUNTIME OVERRIDE ──────────────────────────
// Helper functions that let the budget optimizer read the
// spend goal from Script Properties (if the dashboard has
// set one) rather than the hardcoded constant. Call these
// in place of the bare constants in computeRecommendations_
// if you want dashboard-adjustable goals.
//
// If no override has been set, these return the original
// hardcoded values, so behavior is unchanged by default.
function getTargetWeeklySpend_() {
  var override = PROPS.getProperty('DASHBOARD_TARGET_WEEKLY_SPEND');
  return override ? Number(override) : TARGET_WEEKLY_SPEND;
}

function getWeeklySpendTolerance_() {
  var override = PROPS.getProperty('DASHBOARD_WEEKLY_SPEND_TOLERANCE');
  return override ? Number(override) : WEEKLY_SPEND_TOLERANCE;
}


// ─── SLACK-SAFE APPROVAL CONFIRMATION FLOW ────────────────
// Why this exists:
// Slack's link-unfurling crawler automatically fires GET
// requests against every URL in an incoming webhook message.
// If approve/reject were one-step GET handlers (like they
// used to be), the crawler would fire BOTH URLs the instant
// the proposal posts to Slack — setting both APPROVED and
// REJECTED tokens before any human ever clicks. Result:
// proposals silently auto-approve themselves.
//
// The fix: the bare `approve` and `reject` actions now just
// return an HTML confirmation page with a "Click to confirm"
// button. That button points at `confirm_approve` or
// `confirm_reject`, which do the actual state write. The
// Slack crawler visits the first URL and stops there (it
// doesn't click buttons), so the side effect only happens
// when a real human clicks.

function showApprovalConfirmationPage_(e, action) {
  var token = e && e.parameter && e.parameter.token;
  if (!token) {
    return HtmlService.createHtmlOutput('<h2>Invalid link.</h2><p>Missing token.</p>');
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

  var isApprove = action === 'approve';
  var color       = isApprove ? '#10b981' : '#ef4444';
  var label       = isApprove ? 'APPROVE' : 'REJECT';
  var description = isApprove
    ? 'This will mark the current budget proposal approved. The scheduled executor will apply all proposed changes at 3:00 AM.'
    : 'This will mark the current budget proposal rejected. No budget changes will be applied this cycle.';

  var baseUrl = WEB_APP_URL || ScriptApp.getService().getUrl();
  var confirmUrl = baseUrl + '?action=confirm_' + action + '&token=' + token;

  var html =
    '<!doctype html><html><head>' +
    '<meta charset="utf-8">' +
    '<title>Honeycomb Budget — Confirm</title>' +
    '<meta name="robots" content="noindex">' +
    '<style>' +
    'body{font-family:-apple-system,system-ui,"Segoe UI",sans-serif;max-width:560px;margin:60px auto;padding:24px;color:#1c1917;}' +
    'h1{font-size:22px;margin-bottom:8px;}' +
    'p{line-height:1.55;color:#57534e;}' +
    '.btn{display:inline-block;padding:14px 28px;color:white;text-decoration:none;' +
    'border-radius:8px;font-weight:600;font-size:15px;background:' + color + ';margin-top:16px;}' +
    '.btn:hover{opacity:.92}' +
    '.note{margin-top:24px;padding:12px 16px;background:#fef3c7;border:1px solid #fde68a;' +
    'border-radius:6px;font-size:13px;color:#78350f;line-height:1.5;}' +
    '</style></head><body>' +
    '<h1>🐝 Honeycomb Budget — Confirm ' + label + '</h1>' +
    '<p>You clicked the <strong>' + label + '</strong> link for the current budget proposal.</p>' +
    '<p>' + description + '</p>' +
    '<a class="btn" href="' + confirmUrl + '">Click to confirm ' + label + '</a>' +
    '<div class="note">This extra confirmation step exists so that link previews (like Slack unfurl) can\'t accidentally approve or reject proposals on your behalf.</div>' +
    '</body></html>';

  return HtmlService.createHtmlOutput(html);
}

function applyApprovalDecision_(e, decision) {
  var token = e && e.parameter && e.parameter.token;
  if (!token) {
    return HtmlService.createHtmlOutput('<h2>Invalid link.</h2><p>Missing token.</p>');
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

  if (decision === 'approve') {
    PROPS.setProperty('BUDGET_APPROVED_TOKEN', token);
    postToSlack_('*Honeycomb Budget* ✅ Approved by ' + user +
                 '. Changes will execute tonight at 3:00 AM.');
    return HtmlService.createHtmlOutput(
      '<h2>✅ Budget changes approved.</h2>' +
      '<p>Changes will execute tonight at 3:00 AM. ' +
      'You\'ll receive a Slack confirmation when complete.</p>');
  }

  if (decision === 'reject') {
    PROPS.setProperty('BUDGET_REJECTED_TOKEN', token);
    postToSlack_('*Honeycomb Budget* ❌ Rejected by ' + user +
                 '. No changes will be applied this cycle.');
    return HtmlService.createHtmlOutput(
      '<h2>❌ Budget changes rejected.</h2>' +
      '<p>No budget changes will be made this cycle.</p>');
  }

  return HtmlService.createHtmlOutput('<h2>Unknown decision.</h2>');
}


// ───────────────────────────── END PASTE ─────────────────────────────
