// ============================================================
// HONEYCOMB CREDIT — META ADS INTELLIGENCE SYSTEM
// Full script — paste entire contents into Apps Script editor
//
// TOKENS: Set these in the Apps Script UI, not in code.
// Gear icon (Project Settings) → Script Properties → add:
//   META_ACCESS_TOKEN
//   HUBSPOT_API_KEY
//   SLACK_WEBHOOK_URL
//   ANTHROPIC_API_KEY
// ============================================================


// ============ TOKENS — read from Script Properties ============
const PROPS = PropertiesService.getScriptProperties();
const ACCESS_TOKEN = PROPS.getProperty('META_ACCESS_TOKEN');
const HUBSPOT_API_KEY = PROPS.getProperty('HUBSPOT_API_KEY');
const SLACK_WEBHOOK_URL = PROPS.getProperty('SLACK_WEBHOOK_URL');
const ANTHROPIC_API_KEY = PROPS.getProperty('ANTHROPIC_API_KEY');
const WEB_APP_URL = PROPS.getProperty('WEB_APP_URL');       // Set in Script Properties → WEB_APP_URL


// ============ DO NOT CHANGE ANYTHING BELOW THIS LINE ============
const AD_ACCOUNT_ID = 'act_1953544531525812';
const API_VERSION = 'v21.0';
const META_SHEET = 'rolling_data';
const HS_SHEET = 'hubspot_icps';
const MAPPING_SHEET = 'campaign_mapping';
const ROLLUP_SHEET = 'weekly_rollup';
const INTEL_SHEET = 'intelligence_log';
const BUDGET_SHEET = 'budget_queue';

// Budget automation constants
const TARGET_WEEKLY_SPEND = 10000;   // dollars
const WEEKLY_SPEND_TOLERANCE = 500;     // dollars ±
const CAMPAIGN_DAILY_MIN_CENTS = 2500;    // $25.00/day floor
const MAX_CHANGE_PCT = 0.02;    // ±2% per cycle
const MAX_REDUCTION_PCT = 0.04;    // hard cap: dramatic underperformers only, max 4% reduction per cycle
const LIFETIME_MIN_CONVERSIONS = 10;      // eligibility gate
const WEEKLY_ICP_TARGET = 75;      // weekly ICP benchmark (informational — no kill switch)
const ROLLING_DAYS = 14;      // signal window
const FREQ_WATCH_THRESHOLD = 2.0;     // modifier
const FREQ_HIGH_THRESHOLD = 3.0;     // override

// Portfolio-scaling constants (Session 2). The 12% cap is the single hard
// rail that gates every per-campaign budget movement summed across all
// sources (optimizer increase/decrease, knockdown, strategic reallocation).
// Mirrors data/config/benchmarks.json:scaling.max_weekly_total_change_pct.
// Dual-source by design — Python reads JSON, Code.js reads this constant.
// Change both if you ever change one.
const SCALING_MAX_WEEKLY_PCT = 0.12;
const SCALING_PROFILES_URL = 'https://raw.githubusercontent.com/' +
  'tylerhoneycomb/marketing-claude-honeycomb/main/' +
  'data/derived/scaling_profiles.json';
const SCALING_PROFILES_MAX_AGE_DAYS = 14;
// Script Property keys used by the strategic reallocation flow:
//   SCALING_PENDING_TOKEN, SCALING_APPROVED_TOKEN, SCALING_REJECTED_TOKEN
//   SCALING_LOCKOUT_UNTIL  (ISO timestamp; optimizer skips affected
//                            campaigns until this passes)
//   SCALING_AFFECTED_CAMPAIGN_IDS  (comma-separated)
//   SCALING_PROFILES_CACHE  (JSON string of last successful profile fetch)
//   SCALING_PROFILES_CACHED_AT  (ISO timestamp of that fetch)

const ANTHROPIC_MODEL = 'claude-opus-4-7';  // used by narrative, chat, budget commentary, daily digest

// IC-specific conversion tracking
// The "Investment Crowdfunding Prequal Decision" custom conversion fires
// on Lead events where content_name contains "investment_crowdfunding".
// Campaigns optimized against this event produce direct ICP results.
// The script auto-discovers which campaigns use this conversion via the
// promoted_object field on their ad sets, and stores the plain-text name
// in campaign_mapping for visibility.
//
// IC CONVERSION DISCONTINUITY (2026-04-15 → 2026-04-21):
// Before 2026-04-15, the `conversion_event` column in campaign_mapping was
// effectively empty, so the pattern check at getICConversionMap_ was
// bypassed and IC Conversions in rolling_data reflected the "Prequal
// results page view (leads)" event (ccid 1120689106810751) — a legitimate
// top-of-funnel signal on the older 4 campaigns that fed into the hybrid
// attribution model.
//
// On ~2026-04-15, syncCampaignMappings_ auto-populated conversion_event
// with Meta's plain-text display names (e.g. "Investment Crowdfunding
// Prequal Decision"). The then-current pattern `'investment_crowdfunding'`
// (underscored) stopped matching anything, so IC Conversions dropped to 0
// for 5 consecutive days (4/15-4/19).
//
// Fix (2026-04-21): pattern changed to 'investment crowdfunding' (space)
// to match "Investment Crowdfunding Prequal Decision". Going forward IC
// Conversions reflect ONLY the decision event (ccid 2330338620810873) —
// a cleaner, decision-level signal. Pre-4/15 and post-4/21 IC Conversions
// are NOT directly comparable; treat them as two linked but distinct
// series in historical analysis.
const IC_CONVERSION_EVENT_PATTERN = 'investment crowdfunding';  // matches conversion event name


// ============================================================
// HELPERS
// ============================================================

function validateTokens_() {
  var missing = [];
  if (!ACCESS_TOKEN) missing.push('META_ACCESS_TOKEN');
  if (!HUBSPOT_API_KEY) missing.push('HUBSPOT_API_KEY');
  if (!SLACK_WEBHOOK_URL) missing.push('SLACK_WEBHOOK_URL');
  if (!ANTHROPIC_API_KEY) missing.push('ANTHROPIC_API_KEY');
  if (!WEB_APP_URL) missing.push('WEB_APP_URL');
  if (missing.length > 0) {
    var msg = 'SETUP ERROR: Missing Script Properties: ' + missing.join(', ') +
      '. Go to Apps Script → Project Settings (gear icon) → Script Properties.';
    Logger.log(msg);
    throw new Error(msg);
  }
}

function getWeekNumber(date) {
  var d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  var dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  var yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

function getMonthName(date) {
  var months = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
  return months[date.getMonth()];
}

// Canonical week function — all week labels in the pipeline flow from here.
// buildWeeklyRollup and generateWeeklyNarrative both depend on this returning
// Monday. Changing the convention here changes it everywhere; never inline
// week math elsewhere.
function getWeekStart(date) {
  var d;
  if (typeof date === 'string' && date.match(/^\d{4}-\d{2}-\d{2}/)) {
    var parts = date.substring(0, 10).split('-');
    d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  } else if (date instanceof Date) {
    d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  } else {
    d = new Date(date);
  }
  d.setDate(d.getDate() - (d.getDay() + 6) % 7); // back to Monday
  return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

function dateToYMD_(val) {
  if (!val) return '';
  if (val instanceof Date) {
    return Utilities.formatDate(val, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  var s = String(val).trim();
  if (s.match(/^\d{4}-\d{2}-\d{2}$/)) return s;
  var d = new Date(s);
  if (!isNaN(d.getTime())) {
    return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  return s;
}

function getMostRecentCompletedWeek_(allWeeks) {
  var tz = Session.getScriptTimeZone();
  var today = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  for (var i = allWeeks.length - 1; i >= 0; i--) {
    var parts = String(allWeeks[i]).split('-');
    var weekEnd = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
    weekEnd.setDate(weekEnd.getDate() + 6);
    if (Utilities.formatDate(weekEnd, tz, 'yyyy-MM-dd') < today) return allWeeks[i];
  }
  return null;
}


// ============================================================
// SHARED SLACK HELPER
// ============================================================

function postToSlack_(text) {
  try {
    var response = UrlFetchApp.fetch(SLACK_WEBHOOK_URL, {
      method: 'POST', contentType: 'application/json',
      payload: JSON.stringify({ text: text }),
      muteHttpExceptions: true
    });
    if (response.getResponseCode() !== 200) {
      Logger.log('Slack post failed (' + response.getResponseCode() + '): ' +
        response.getContentText());
    }
  } catch (e) {
    Logger.log('postToSlack_ exception: ' + e.message);
  }
}


// ============================================================
// FETCH WITH RETRY
// ============================================================

function fetchWithRetry_(url, options, maxRetries) {
  maxRetries = maxRetries || 3;
  var lastResponse = null;

  for (var i = 0; i < maxRetries; i++) {
    try {
      var response = UrlFetchApp.fetch(url, options);
      var code = response.getResponseCode();

      if (code === 200) return response;

      if (code >= 400 && code < 500) {
        Logger.log('fetchWithRetry_: non-retryable HTTP ' + code + ' from ' + url.substring(0, 80));
        return response;
      }

      lastResponse = response;
      Logger.log('fetchWithRetry_: transient HTTP ' + code + ' (attempt ' + (i + 1) + '/' + maxRetries + ')');

    } catch (e) {
      Logger.log('fetchWithRetry_: exception (attempt ' + (i + 1) + '/' + maxRetries + '): ' + e.message);
      lastResponse = null;
      if (i === maxRetries - 1) throw e;
    }

    if (i < maxRetries - 1) {
      Utilities.sleep((Math.pow(2, i) * 1000) + Math.round(Math.random() * 1000));
    }
  }

  var codeStr = lastResponse ? ' (HTTP ' + lastResponse.getResponseCode() + ')' : ' (exception)';
  var domain = url.match(/https?:\/\/([^\/]+)/);
  var host = domain ? domain[1] : url.substring(0, 40);
  postToSlack_(
    '⚠️ *Honeycomb Ads — API failure*\n' +
    'After ' + maxRetries + ' attempts, `' + host + '` is still returning errors' + codeStr + '.\n' +
    'Today\'s data pipeline may be incomplete. Check Apps Script logs.'
  );
  Logger.log('fetchWithRetry_: exhausted all retries for ' + url.substring(0, 80));
  return lastResponse;
}


// ============================================================
// CAMPAIGN → UTM MAPPING (AUTO-SYNC FROM META DESTINATION URLs)
//
// syncCampaignMappings_ discovers unmapped campaigns in
// rolling_data, queries Meta for their ads' destination URLs,
// extracts utm_campaign, and writes new rows to campaign_mapping
// automatically. Manual entries are never overwritten.
//
// Guards:
// - Once-per-day execution (SYNC_LAST_RUN_DATE)
// - Only queries ACTIVE + PAUSED ads (no archived/deleted)
// - Deduplicates before writing
// - Alerts Slack only for newly unresolvable campaigns
// - Detects missing ads_read permission and alerts once
// ============================================================

function decodeUtmValue_(str) {
  try {
    return decodeURIComponent(str.replace(/\+/g, ' '));
  } catch (e) {
    return str.replace(/\+/g, ' ');
  }
}

function extractUtmCampaignFromTags_(urlTags) {
  if (!urlTags) return null;
  var match = String(urlTags).match(/utm_campaign=([^&]+)/);
  if (match) return decodeUtmValue_(match[1]);
  return null;
}

function syncCampaignMappings_() {
  Logger.log('--- syncCampaignMappings_ ---');

  // Once-per-day guard: buildCampaignUTMMap_ is called by both
  // buildWeeklyRollup and computeBudgetSignals_. Both run daily
  // (budget 6am, pipeline 7am). Sync only needs to run once.
  var tz = Session.getScriptTimeZone();
  var todayStr = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  var lastSync = PROPS.getProperty('SYNC_LAST_RUN_DATE');
  if (lastSync === todayStr) {
    Logger.log('Sync already ran today (' + todayStr + '). Skipping.');
    return;
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var mappingSheet = ss.getSheetByName(MAPPING_SHEET);

  if (!mappingSheet) {
    mappingSheet = ss.insertSheet(MAPPING_SHEET);
    mappingSheet.appendRow(['campaign_name', 'utm_campaign', 'conversion_event', 'custom_conversion_id', 'campaign_id']);
    mappingSheet.getRange('D:D').setNumberFormat('@');
    mappingSheet.getRange('E:E').setNumberFormat('@');
  }

  // Ensure existing sheets have all columns (backward compat)
  var headerRow = mappingSheet.getRange(1, 1, 1, mappingSheet.getLastColumn()).getValues()[0];
  if (headerRow.length < 4 || String(headerRow[2]).trim() !== 'conversion_event') {
    if (headerRow.length < 3) mappingSheet.getRange(1, 3).setValue('conversion_event');
    if (headerRow.length < 4) mappingSheet.getRange(1, 4).setValue('custom_conversion_id');
  }
  if (headerRow.length < 5 || String(headerRow[4]).trim() !== 'campaign_id') {
    mappingSheet.getRange(1, 5).setValue('campaign_id');
  }
  mappingSheet.getRange('D:D').setNumberFormat('@');
  mappingSheet.getRange('E:E').setNumberFormat('@');

  var existingMappings = {};
  var existingById = {};
  var mappingData = mappingSheet.getDataRange().getValues();
  for (var mi = 1; mi < mappingData.length; mi++) {
    var mName = String(mappingData[mi][0]).trim();
    var mUtm = String(mappingData[mi][1]).trim();
    var mCid = (mappingData[mi].length > 4) ? String(mappingData[mi][4]).trim() : '';
    if (mName && mUtm) existingMappings[mName] = mUtm;
    if (mCid) existingById[mCid] = mi + 1;
  }
  Logger.log('Existing mappings: ' + Object.keys(existingMappings).length +
    ' (' + Object.keys(existingById).length + ' with campaign_id)');

  var metaSheet = ss.getSheetByName(META_SHEET);
  if (!metaSheet) { Logger.log('rolling_data not found — skipping sync.'); return; }

  var metaData = metaSheet.getDataRange().getValues();
  var unmappedById = {};
  var unmappedNameSet = {};

  for (var ri = 1; ri < metaData.length; ri++) {
    var cName = String(metaData[ri][3]).trim();
    var cId = String(metaData[ri][4]).trim();
    if (!cName || !cId) continue;
    if (existingMappings[cName]) continue;
    if (unmappedNameSet[cName]) continue;
    unmappedById[cId] = cName;
    unmappedNameSet[cName] = true;
  }

  var unmappedCount = Object.keys(unmappedById).length;
  if (unmappedCount === 0) {
    Logger.log('All campaigns already mapped. No API call needed.');
    PROPS.setProperty('SYNC_LAST_RUN_DATE', todayStr);
    return;
  }
  Logger.log('Unmapped campaigns to resolve via Meta API: ' + unmappedCount);

  // Query Meta for ACTIVE + PAUSED ads only (no archived/deleted
  // which may carry stale UTMs from old creatives)
  var resolved = {};
  var creativeFieldSeen = false;
  var statusFilter = encodeURIComponent('["ACTIVE","PAUSED"]');
  var url = 'https://graph.facebook.com/' + API_VERSION + '/' + AD_ACCOUNT_ID +
    '/ads?fields=campaign_id,creative%7Burl_tags%7D' +
    '&effective_status=' + statusFilter +
    '&limit=200&access_token=' + ACCESS_TOKEN;

  var pageNum = 0;
  while (url) {
    pageNum++;
    try {
      var resp = fetchWithRetry_(url, { muteHttpExceptions: true });
      if (!resp || resp.getResponseCode() !== 200) {
        Logger.log('syncCampaignMappings_: Meta API error on page ' + pageNum);
        break;
      }
      var json = JSON.parse(resp.getContentText());
      if (json.error) {
        Logger.log('syncCampaignMappings_: API error: ' + json.error.message);
        break;
      }

      (json.data || []).forEach(function (ad) {
        var cid = ad.campaign_id;
        if (ad.creative) creativeFieldSeen = true;
        if (!unmappedById[cid]) return;
        if (resolved[cid]) return;

        var utmValue = null;

        if (ad.creative && ad.creative.url_tags) {
          utmValue = extractUtmCampaignFromTags_(ad.creative.url_tags);
        }

        if (utmValue) {
          resolved[cid] = utmValue;
          Logger.log('Auto-resolved: "' + unmappedById[cid] + '" → "' + utmValue + '"');
        }
      });

      if (Object.keys(resolved).length >= unmappedCount) break;
      url = (json.paging && json.paging.next) ? json.paging.next : null;

    } catch (e) {
      Logger.log('syncCampaignMappings_: exception on page ' + pageNum + ': ' + e.message);
      break;
    }
  }

  // API scope check: if ads returned but no creative field, token
  // likely missing ads_read permission. Alert once.
  if (pageNum > 0 && !creativeFieldSeen && Object.keys(resolved).length === 0) {
    var scopeWarned = PROPS.getProperty('SYNC_SCOPE_WARNED');
    if (scopeWarned !== 'true') {
      postToSlack_(
        '⚠️ *Honeycomb Ads — API Permission Issue*\n' +
        'The Meta API returned ads but no creative data. The System User token ' +
        'may be missing the `ads_read` permission.\n\n' +
        'Campaign UTM auto-sync cannot work without this. Check Business Manager → ' +
        'System Users → token permissions and ensure `ads_read` is included.\n\n' +
        '_This alert will not repeat until the issue is resolved._'
      );
      PROPS.setProperty('SYNC_SCOPE_WARNED', 'true');
    }
    Logger.log('WARNING: No creative data in API response — likely missing ads_read permission.');
    PROPS.setProperty('SYNC_LAST_RUN_DATE', todayStr);
    return;
  }

  if (creativeFieldSeen) {
    PROPS.deleteProperty('SYNC_SCOPE_WARNED');
  }

  // ── Discover conversion events from ad sets ─────────────────
  // Query ad sets for promoted_object to find which campaigns use
  // custom conversions (like IC-specific events). Builds a map
  // of campaign_id → { conversionName, customConversionId }.
  var conversionByCampaignId = {};
  var customConversionNames = {};  // cache: id → name

  try {
    var adsetUrl = 'https://graph.facebook.com/' + API_VERSION + '/' + AD_ACCOUNT_ID +
      '/adsets?fields=campaign_id,promoted_object,optimization_goal' +
      '&effective_status=' + statusFilter +
      '&limit=200&access_token=' + ACCESS_TOKEN;

    var adsetPageNum = 0;
    while (adsetUrl) {
      adsetPageNum++;
      var adsetResp = fetchWithRetry_(adsetUrl, { muteHttpExceptions: true });
      if (!adsetResp || adsetResp.getResponseCode() !== 200) {
        Logger.log('syncCampaignMappings_: ad set query error on page ' + adsetPageNum);
        break;
      }
      var adsetJson = JSON.parse(adsetResp.getContentText());
      if (adsetJson.error) {
        Logger.log('syncCampaignMappings_: ad set API error: ' + adsetJson.error.message);
        break;
      }

      (adsetJson.data || []).forEach(function (adset) {
        var cid = adset.campaign_id;
        if (!cid) return;
        if (conversionByCampaignId[cid]) return;  // first ad set wins

        if (adset.promoted_object && adset.promoted_object.custom_conversion_id) {
          var ccId = String(adset.promoted_object.custom_conversion_id);
          conversionByCampaignId[cid] = { customConversionId: ccId, conversionName: null };
          customConversionNames[ccId] = null;  // queue for name lookup
        }
      });

      adsetUrl = (adsetJson.paging && adsetJson.paging.next) ? adsetJson.paging.next : null;
    }
    Logger.log('Conversion events discovered for ' +
      Object.keys(conversionByCampaignId).length + ' campaigns.');
  } catch (e) {
    Logger.log('syncCampaignMappings_: conversion event discovery exception: ' + e.message);
  }

  // Resolve custom conversion IDs to plain-text names
  var ccIds = Object.keys(customConversionNames);
  if (ccIds.length > 0) {
    ccIds.forEach(function (ccId) {
      try {
        var ccUrl = 'https://graph.facebook.com/' + API_VERSION + '/' + ccId +
          '?fields=name,id&access_token=' + ACCESS_TOKEN;
        var ccResp = fetchWithRetry_(ccUrl, { muteHttpExceptions: true });
        if (ccResp && ccResp.getResponseCode() === 200) {
          var ccJson = JSON.parse(ccResp.getContentText());
          customConversionNames[ccId] = ccJson.name || '';
          Logger.log('Custom conversion ' + ccId + ' → "' + ccJson.name + '"');
        }
      } catch (e) {
        Logger.log('Custom conversion name lookup failed for ' + ccId + ': ' + e.message);
      }
    });

    // Assign names back to campaign map
    Object.keys(conversionByCampaignId).forEach(function (cid) {
      var info = conversionByCampaignId[cid];
      info.conversionName = customConversionNames[info.customConversionId] || '';
    });
  }
  // ────────────────────────────────────────────────────────────

  // Dedup: re-read campaign_mapping before writing. Use campaign_id
  // as the primary key when populated so renames don't create
  // duplicate rows.
  var freshMappingData = mappingSheet.getDataRange().getValues();
  var freshNames = {};
  var freshIdToRow = {};
  for (var fi = 1; fi < freshMappingData.length; fi++) {
    var fn = String(freshMappingData[fi][0]).trim();
    if (fn) freshNames[fn] = true;
    var fCid = (freshMappingData[fi].length > 4) ? String(freshMappingData[fi][4]).trim() : '';
    if (fCid) freshIdToRow[fCid] = fi + 1;
  }

  var newRows = [];
  var renamedCampaigns = [];
  var renamesByCid = {};
  Object.keys(resolved).forEach(function (cid) {
    var campaignName = unmappedById[cid];
    var convInfo = conversionByCampaignId[cid] || {};

    if (freshIdToRow[cid]) {
      // Campaign ID already has a mapping row — this is a rename
      // or a re-discovery. Update the name; preserve manual columns.
      var rowNum = freshIdToRow[cid];
      var oldName = String(mappingSheet.getRange(rowNum, 1).getValue()).trim();
      if (oldName !== campaignName) {
        mappingSheet.getRange(rowNum, 1).setValue(campaignName);
        renamedCampaigns.push(oldName + ' → ' + campaignName);
        renamesByCid[cid] = campaignName;
        Logger.log('Rename detected: row ' + rowNum + ' "' + oldName + '" → "' + campaignName + '"');
      }
    } else if (!freshNames[campaignName]) {
      // Truly new campaign — append with campaign_id in column E.
      newRows.push([
        campaignName,
        resolved[cid],
        convInfo.conversionName || '',
        convInfo.customConversionId || '',
        cid
      ]);
    } else {
      // Name exists but no campaign_id — legacy row. Backfill the id.
      for (var bfi = 1; bfi < freshMappingData.length; bfi++) {
        if (String(freshMappingData[bfi][0]).trim() === campaignName) {
          var existingId = (freshMappingData[bfi].length > 4) ? String(freshMappingData[bfi][4]).trim() : '';
          if (!existingId) {
            mappingSheet.getRange(bfi + 1, 5).setValue(cid);
            Logger.log('Backfilled campaign_id for "' + campaignName + '": ' + cid);
          }
          break;
        }
      }
    }
  });

  if (newRows.length > 0) {
    mappingSheet.getRange(
      mappingSheet.getLastRow() + 1, 1, newRows.length, 5
    ).setValues(newRows);
    Logger.log('Auto-populated ' + newRows.length + ' new campaign mapping(s).');

    postToSlack_(
      '🔗 *Honeycomb Ads — New Campaign Mappings Auto-Detected*\n' +
      newRows.length + ' new campaign' + (newRows.length !== 1 ? 's' : '') +
      ' discovered and mapped from Meta destination URLs:\n\n' +
      newRows.map(function (r) {
        var convLabel = r[2] ? ' [' + r[2] + ']' : '';
        return '• ' + r[0] + ' → `' + r[1] + '`' + convLabel;
      }).join('\n') +
      '\n\nThese have been added to `campaign_mapping`. Edit the tab to override if needed.'
    );
  }

  if (renamedCampaigns.length > 0) {
    // Update historical rolling_data rows so all downstream
    // consumers (weekly rollup, dashboard, AI chat context) see
    // one canonical name per campaign_id. Campaign_id is the
    // stable key; the historical name is just a stale label.
    var rdSheet = ss.getSheetByName(META_SHEET);
    var rdRowsUpdated = 0;
    if (rdSheet && rdSheet.getLastRow() > 1) {
      var rdLastRow = rdSheet.getLastRow();
      var rdNameRange = rdSheet.getRange(2, 4, rdLastRow - 1, 1);
      var rdNames = rdNameRange.getValues();
      var rdIds = rdSheet.getRange(2, 5, rdLastRow - 1, 1).getValues();
      for (var rdi = 0; rdi < rdNames.length; rdi++) {
        var rdCid = String(rdIds[rdi][0]).trim();
        if (renamesByCid[rdCid] && String(rdNames[rdi][0]).trim() !== renamesByCid[rdCid]) {
          rdNames[rdi][0] = renamesByCid[rdCid];
          rdRowsUpdated++;
        }
      }
      if (rdRowsUpdated > 0) {
        rdNameRange.setValues(rdNames);
        Logger.log('Updated ' + rdRowsUpdated + ' historical rolling_data rows to new campaign name(s).');
      }
    }

    postToSlack_(
      '🔄 *Honeycomb Ads — Campaign Renames Detected*\n' +
      renamedCampaigns.length + ' campaign' + (renamedCampaigns.length !== 1 ? 's' : '') +
      ' renamed in Meta. Mapping rows updated in place (UTM and conversion settings preserved)' +
      (rdRowsUpdated > 0 ? '. ' + rdRowsUpdated + ' historical rolling_data rows also updated so reports use the new name' : '') +
      ':\n\n' +
      renamedCampaigns.map(function (r) { return '• ' + r; }).join('\n')
    );
  }

  // Unresolvable campaigns: alert only on NEW ones that aren't
  // already mapped via campaign_id in the mapping sheet.
  var stillUnmapped = Object.keys(unmappedById).filter(function (cid) {
    return !resolved[cid] && !freshIdToRow[cid];
  });

  if (stillUnmapped.length > 0) {
    var priorWarnedRaw = PROPS.getProperty('SYNC_WARNED_CAMPAIGNS') || '';
    var priorWarned = {};
    if (priorWarnedRaw) {
      priorWarnedRaw.split('||').forEach(function (n) {
        if (n) priorWarned[n] = true;
      });
    }

    var newlyUnresolved = [];
    stillUnmapped.forEach(function (cid) {
      var name = unmappedById[cid];
      if (!priorWarned[name]) {
        newlyUnresolved.push(name);
        priorWarned[name] = true;
      }
    });

    PROPS.setProperty('SYNC_WARNED_CAMPAIGNS',
      Object.keys(priorWarned).join('||'));

    if (newlyUnresolved.length > 0) {
      Logger.log('Newly unresolvable campaigns: ' + newlyUnresolved.join(', '));
      postToSlack_(
        '⚠️ *Honeycomb Ads — Unresolvable Campaigns*\n' +
        newlyUnresolved.length + ' new campaign' + (newlyUnresolved.length !== 1 ? 's' : '') +
        ' could not be auto-mapped (no active/paused ads with destination URLs found):\n\n' +
        newlyUnresolved.map(function (n) { return '• ' + n; }).join('\n') +
        '\n\nThese will be excluded from rollup and budget optimizer until mapped. ' +
        'Add them manually to `campaign_mapping` or ensure ads have destination URLs with utm_campaign.\n' +
        '_This alert will not repeat for these campaigns._'
      );
    } else {
      Logger.log('Unresolvable campaigns (' + stillUnmapped.length +
        ') already warned — suppressing repeat alert.');
    }

    // Clean warned set: remove campaigns that have since been mapped
    var currentMapped = {};
    var refreshData = mappingSheet.getDataRange().getValues();
    for (var ri2 = 1; ri2 < refreshData.length; ri2++) {
      var rn = String(refreshData[ri2][0]).trim();
      if (rn) currentMapped[rn] = true;
    }
    var cleanedWarned = {};
    Object.keys(priorWarned).forEach(function (n) {
      if (!currentMapped[n]) cleanedWarned[n] = true;
    });
    PROPS.setProperty('SYNC_WARNED_CAMPAIGNS',
      Object.keys(cleanedWarned).join('||'));
  } else {
    PROPS.deleteProperty('SYNC_WARNED_CAMPAIGNS');
  }

  // ── Backfill conversion events for existing campaigns ──────
  // Existing mapped campaigns may not have conversion_event populated.
  // Fill in any blanks using the ad set data we already queried.
  if (Object.keys(conversionByCampaignId).length > 0) {
    var backfillData = mappingSheet.getDataRange().getValues();

    // Build reverse lookup: campaign_name → campaign_id(s) from rolling_data
    var nameToIds = {};
    for (var bri = 1; bri < metaData.length; bri++) {
      var bName = String(metaData[bri][3]).trim();
      var bId = String(metaData[bri][4]).trim();
      if (bName && bId && !nameToIds[bName]) nameToIds[bName] = bId;
    }

    var backfilled = 0;
    for (var bi = 1; bi < backfillData.length; bi++) {
      var bfConvEvent = (backfillData[bi].length > 2) ? String(backfillData[bi][2]).trim() : '';
      if (bfConvEvent) continue;  // already populated

      var bfCid = (backfillData[bi].length > 4) ? String(backfillData[bi][4]).trim() : '';
      if (!bfCid) bfCid = nameToIds[String(backfillData[bi][0]).trim()];
      if (bfCid && conversionByCampaignId[bfCid]) {
        var bfInfo = conversionByCampaignId[bfCid];
        mappingSheet.getRange(bi + 1, 3).setValue(bfInfo.conversionName || '');
        mappingSheet.getRange(bi + 1, 4).setValue(bfInfo.customConversionId || '');
        backfilled++;
      }
    }
    if (backfilled > 0) {
      Logger.log('Backfilled conversion events for ' + backfilled + ' existing campaign(s).');
    }
  }
  // ────────────────────────────────────────────────────────────

  backfillCampaignIds_();

  // ── General-purpose rename reconciliation ─────────────────
  // The rename detection in the resolved-campaign loop above only
  // catches campaigns whose ads have discoverable URL tags.
  // Campaigns without URL tags (the AD-* batch) bypass that loop
  // entirely. This second pass catches ALL renames by comparing
  // each mapping row's campaign_name to the latest name in
  // rolling_data for that campaign_id.
  var reconMappingData = mappingSheet.getDataRange().getValues();
  var latestNameById = {};
  for (var lni = metaData.length - 1; lni >= 1; lni--) {
    var lnCid = String(metaData[lni][4]).trim();
    if (lnCid && !latestNameById[lnCid]) {
      latestNameById[lnCid] = String(metaData[lni][3]).trim();
    }
  }

  var reconRenames = [];
  for (var rmi = 1; rmi < reconMappingData.length; rmi++) {
    var rmCid = (reconMappingData[rmi].length > 4) ? String(reconMappingData[rmi][4]).trim() : '';
    if (!rmCid) continue;
    var rmCurrentName = String(reconMappingData[rmi][0]).trim();
    var rmLatestName = latestNameById[rmCid];
    if (rmLatestName && rmLatestName !== rmCurrentName) {
      mappingSheet.getRange(rmi + 1, 1).setValue(rmLatestName);
      reconRenames.push(rmCurrentName + ' → ' + rmLatestName);
      Logger.log('Reconcile rename: row ' + (rmi + 1) + ' "' + rmCurrentName + '" → "' + rmLatestName + '"');
    }
  }

  if (reconRenames.length > 0) {
    // Also normalize historical rolling_data rows
    var rdSheet = ss.getSheetByName(META_SHEET);
    var rdNormalized = 0;
    if (rdSheet && rdSheet.getLastRow() > 1) {
      var rdLastRow = rdSheet.getLastRow();
      var rdNameRange = rdSheet.getRange(2, 4, rdLastRow - 1, 1);
      var rdNames = rdNameRange.getValues();
      var rdIds = rdSheet.getRange(2, 5, rdLastRow - 1, 1).getValues();
      for (var rdi = 0; rdi < rdNames.length; rdi++) {
        var rdCid = String(rdIds[rdi][0]).trim();
        if (rdCid && latestNameById[rdCid] && String(rdNames[rdi][0]).trim() !== latestNameById[rdCid]) {
          rdNames[rdi][0] = latestNameById[rdCid];
          rdNormalized++;
        }
      }
      if (rdNormalized > 0) {
        rdNameRange.setValues(rdNames);
      }
    }

    Logger.log('Reconciled ' + reconRenames.length + ' mapping name(s), ' + rdNormalized + ' rolling_data rows.');
    postToSlack_(
      '🔄 *Honeycomb Ads — Campaign Names Reconciled*\n' +
      reconRenames.length + ' mapping row' + (reconRenames.length !== 1 ? 's' : '') +
      ' updated to match current Meta names' +
      (rdNormalized > 0 ? ' (' + rdNormalized + ' historical rolling_data rows also normalized)' : '') +
      ':\n\n' +
      reconRenames.map(function (r) { return '• ' + r; }).join('\n')
    );
  }

  PROPS.setProperty('SYNC_LAST_RUN_DATE', todayStr);
  Logger.log('syncCampaignMappings_ complete.');
}


function buildCampaignUTMMap_() {
  Logger.log('--- buildCampaignUTMMap_ ---');

  // Auto-discover and populate mappings for any new campaigns
  syncCampaignMappings_();

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var mappingSheet = ss.getSheetByName(MAPPING_SHEET);

  if (!mappingSheet) {
    Logger.log('ERROR: campaign_mapping sheet not found.');
    return {};
  }

  var mappingData = mappingSheet.getDataRange().getValues();
  var nameToUtm = {};
  var cidToUtm = {};
  for (var mi = 1; mi < mappingData.length; mi++) {
    var cName = String(mappingData[mi][0]).trim();
    var cUtm = String(mappingData[mi][1]).trim();
    var cMappingId = (mappingData[mi].length > 4) ? String(mappingData[mi][4]).trim() : '';
    if (cName && cUtm) nameToUtm[cName] = cUtm;
    if (cMappingId && cUtm) cidToUtm[cMappingId] = cUtm;
  }
  Logger.log('Mappings loaded: ' + Object.keys(nameToUtm).length +
    ' (' + Object.keys(cidToUtm).length + ' with campaign_id)');

  if (Object.keys(nameToUtm).length === 0) {
    Logger.log('ERROR: campaign_mapping tab is empty.');
    return {};
  }

  var metaSheet = ss.getSheetByName(META_SHEET);
  if (!metaSheet) { Logger.log('ERROR: rolling_data sheet not found.'); return {}; }

  var metaData = metaSheet.getDataRange().getValues();
  var idToUtm = {};
  var unmapped = {};

  for (var ri = 1; ri < metaData.length; ri++) {
    var cName2 = String(metaData[ri][3]).trim();
    var cid = String(metaData[ri][4]).trim();
    if (!cName2 || !cid) continue;
    if (idToUtm[cid]) continue;

    if (cidToUtm[cid]) {
      idToUtm[cid] = cidToUtm[cid];
    } else if (nameToUtm[cName2]) {
      idToUtm[cid] = nameToUtm[cName2];
    } else {
      unmapped[cName2] = true;
    }
  }

  Logger.log('Campaign IDs resolved: ' + Object.keys(idToUtm).length);

  if (Object.keys(unmapped).length > 0) {
    Logger.log('WARNING: Still unmapped after auto-sync: ' +
      Object.keys(unmapped).join(', '));
  }

  return idToUtm;
}


// ============================================================
// IC CAMPAIGN IDENTIFICATION
// One-time backfill: populates campaign_id (column E) in
// campaign_mapping for all rows that are missing it. Matches
// by campaign_name against rolling_data. Also deduplicates
// rows where the same campaign_id appears under multiple names
// (prior renames). Runs automatically once, gated by
// MAPPING_BACKFILL_COMPLETE in Script Properties.
function backfillCampaignIds_() {
  if (PROPS.getProperty('MAPPING_BACKFILL_COMPLETE')) return;
  Logger.log('--- backfillCampaignIds_ (one-time migration) ---');

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var mappingSheet = ss.getSheetByName(MAPPING_SHEET);
  var metaSheet = ss.getSheetByName(META_SHEET);
  if (!mappingSheet || !metaSheet) return;

  var metaData = metaSheet.getDataRange().getValues();
  var nameToId = {};
  for (var ri = 1; ri < metaData.length; ri++) {
    var n = String(metaData[ri][3]).trim();
    var id = String(metaData[ri][4]).trim();
    if (n && id) nameToId[n] = id;
  }

  var mappingData = mappingSheet.getDataRange().getValues();
  var filled = 0;
  var idToRows = {};

  for (var mi = 1; mi < mappingData.length; mi++) {
    var mName = String(mappingData[mi][0]).trim();
    var existingId = (mappingData[mi].length > 4) ? String(mappingData[mi][4]).trim() : '';

    if (!existingId && nameToId[mName]) {
      existingId = nameToId[mName];
      mappingSheet.getRange(mi + 1, 5).setValue(existingId);
      filled++;
      Logger.log('  Backfilled campaign_id for "' + mName + '": ' + existingId);
    }

    if (existingId) {
      if (!idToRows[existingId]) idToRows[existingId] = [];
      idToRows[existingId].push(mi + 1);
    }
  }

  // Deduplicate: if a campaign_id maps to multiple rows (prior
  // renames), keep the row with the most-filled manual columns
  // and delete the others.
  var dupsRemoved = 0;
  Object.keys(idToRows).forEach(function (cid) {
    var rows = idToRows[cid];
    if (rows.length < 2) return;

    var best = rows[0];
    var bestScore = 0;
    rows.forEach(function (rowNum) {
      var vals = mappingSheet.getRange(rowNum, 1, 1, 5).getValues()[0];
      var score = 0;
      if (String(vals[1]).trim()) score++;
      if (String(vals[2]).trim()) score++;
      if (String(vals[3]).trim()) score++;
      if (score > bestScore) { bestScore = score; best = rowNum; }
    });

    var latestName = mappingSheet.getRange(rows[rows.length - 1], 1).getValue();
    mappingSheet.getRange(best, 1).setValue(latestName);

    for (var di = rows.length - 1; di >= 0; di--) {
      if (rows[di] !== best) {
        Logger.log('  Dedup: deleting row ' + rows[di] + ' for campaign_id ' + cid);
        mappingSheet.deleteRow(rows[di]);
        dupsRemoved++;
      }
    }
  });

  // Normalize historical rolling_data names: if a campaign_id has
  // multiple names (from prior renames that happened before this
  // fix deployed), collapse them all to the name on its latest-
  // dated row. This runs once during migration; going forward,
  // rename detection in syncCampaignMappings_ keeps names aligned.
  var rdNormalized = 0;
  if (metaSheet.getLastRow() > 1) {
    var rdLastRow = metaSheet.getLastRow();
    var rdNameRange = metaSheet.getRange(2, 4, rdLastRow - 1, 1);
    var rdNames = rdNameRange.getValues();
    var rdIds = metaSheet.getRange(2, 5, rdLastRow - 1, 1).getValues();
    var rdDates = metaSheet.getRange(2, 1, rdLastRow - 1, 1).getValues();

    // Find latest-dated name per campaign_id.
    var latestByCid = {};
    for (var rdi = 0; rdi < rdNames.length; rdi++) {
      var rdCid = String(rdIds[rdi][0]).trim();
      var rdName = String(rdNames[rdi][0]).trim();
      if (!rdCid || !rdName) continue;
      var rdDate = rdDates[rdi][0] instanceof Date
        ? Utilities.formatDate(rdDates[rdi][0], Session.getScriptTimeZone(), 'yyyy-MM-dd')
        : String(rdDates[rdi][0]).substring(0, 10);
      if (!latestByCid[rdCid] || rdDate > latestByCid[rdCid].date) {
        latestByCid[rdCid] = { name: rdName, date: rdDate };
      }
    }

    // Rewrite any row whose name differs from the latest.
    for (var rdj = 0; rdj < rdNames.length; rdj++) {
      var jCid = String(rdIds[rdj][0]).trim();
      if (jCid && latestByCid[jCid] && String(rdNames[rdj][0]).trim() !== latestByCid[jCid].name) {
        rdNames[rdj][0] = latestByCid[jCid].name;
        rdNormalized++;
      }
    }
    if (rdNormalized > 0) {
      rdNameRange.setValues(rdNames);
      Logger.log('  Normalized ' + rdNormalized + ' historical rolling_data names to match latest per campaign_id.');
    }
  }

  PROPS.setProperty('MAPPING_BACKFILL_COMPLETE', new Date().toISOString());
  Logger.log('Backfill complete: ' + filled + ' IDs filled, ' + dupsRemoved +
    ' mapping duplicates removed, ' + rdNormalized + ' rolling_data rows normalized.');
}


// Reads campaign_mapping to identify which campaign IDs use
// IC-specific conversion events, and returns their custom
// conversion IDs so collectMetaRows_ can extract IC conversion
// counts from the actions array.
// ============================================================

function getICConversionMap_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var mappingSheet = ss.getSheetByName(MAPPING_SHEET);
  if (!mappingSheet) return { icCampaignIds: {}, customConversionIds: {} };

  var mappingData = mappingSheet.getDataRange().getValues();
  var metaSheet = ss.getSheetByName(META_SHEET);
  if (!metaSheet) return { icCampaignIds: {}, customConversionIds: {} };
  var metaData = metaSheet.getDataRange().getValues();

  // Build name → campaign_ids from rolling_data (fallback for rows
  // without campaign_id in column E).
  var nameToIds = {};
  for (var ri = 1; ri < metaData.length; ri++) {
    var cName = String(metaData[ri][3]).trim();
    var cId = String(metaData[ri][4]).trim();
    if (cName && cId) {
      if (!nameToIds[cName]) nameToIds[cName] = {};
      nameToIds[cName][cId] = true;
    }
  }

  var icCampaignIds = {};    // campaign_id → true
  var customConversionIds = {};  // custom_conversion_id → action_type string

  for (var mi = 1; mi < mappingData.length; mi++) {
    var mName = String(mappingData[mi][0]).trim();
    var convEvent = (mappingData[mi].length > 2) ? String(mappingData[mi][2]).trim() : '';
    var ccId = (mappingData[mi].length > 3) ? String(mappingData[mi][3]).trim() : '';
    var mCampaignId = (mappingData[mi].length > 4) ? String(mappingData[mi][4]).trim() : '';

    if (!convEvent || !ccId) continue;

    if (convEvent.toLowerCase().indexOf(IC_CONVERSION_EVENT_PATTERN) === -1) continue;

    // Prefer campaign_id from mapping column E when populated.
    if (mCampaignId) {
      icCampaignIds[mCampaignId] = true;
    } else {
      var ids = nameToIds[mName] || {};
      Object.keys(ids).forEach(function (cid) {
        icCampaignIds[cid] = true;
      });
    }

    var actionType = 'offsite_conversion.custom.' + ccId;
    customConversionIds[ccId] = actionType;
  }

  Logger.log('IC campaigns identified: ' + Object.keys(icCampaignIds).length);
  Logger.log('IC custom conversion action types: ' +
    Object.values(customConversionIds).join(', '));

  return {
    icCampaignIds: icCampaignIds,
    customConversionIds: customConversionIds
  };
}


// ============================================================
// META ADS PULL
// ============================================================

function fetchMetaAdsData() {
  Logger.log('=== fetchMetaAdsData ===');
  validateTokens_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(META_SHEET);
  if (!sheet) {
    Logger.log('ERROR: Sheet "' + META_SHEET + '" not found. Check the tab name.');
    return;
  }
  var yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  var dateString = Utilities.formatDate(yesterday, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  Logger.log('Fetching date: ' + dateString);
  fetchDataForDateRange_(sheet, dateString, dateString);
}

function backfillFromJan1() {
  Logger.log('=== backfillFromJan1 ===');
  validateTokens_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(META_SHEET);
  if (!sheet) {
    Logger.log('ERROR: Sheet "' + META_SHEET + '" not found.');
    return;
  }
  var endDate = new Date();
  endDate.setDate(endDate.getDate() - 1);
  var endString = Utilities.formatDate(endDate, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  Logger.log('Backfilling 2026-01-01 to ' + endString);
  fetchDataForDateRange_(sheet, '2026-01-01', endString);
}

function fetchLast30Days() {
  Logger.log('=== fetchLast30Days ===');
  validateTokens_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(META_SHEET);
  if (!sheet) { Logger.log('ERROR: Sheet "' + META_SHEET + '" not found.'); return; }
  var endDate = new Date(); endDate.setDate(endDate.getDate() - 1);
  var startDate = new Date(); startDate.setDate(startDate.getDate() - 30);
  fetchDataForDateRange_(
    sheet,
    Utilities.formatDate(startDate, Session.getScriptTimeZone(), 'yyyy-MM-dd'),
    Utilities.formatDate(endDate, Session.getScriptTimeZone(), 'yyyy-MM-dd')
  );
}

function fetchDataForDateRange_(sheet, startDate, endDate) {
  var existingKeys = new Set();
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    var existing = sheet.getRange(2, 1, lastRow - 1, 5).getValues();
    existing.forEach(function (row) {
      if (!row[0] || !row[4]) return;
      var dateStr = row[0] instanceof Date
        ? Utilities.formatDate(row[0], Session.getScriptTimeZone(), 'yyyy-MM-dd')
        : String(row[0]).substring(0, 10);
      existingKeys.add(dateStr + '||' + String(row[4]));
    });
  }
  Logger.log('Existing Meta rows for dedup: ' + existingKeys.size);

  // Discover IC custom conversion action types for extraction
  var icMap = getICConversionMap_();
  var icActionTypes = Object.values(icMap.customConversionIds);
  Logger.log('IC action types to extract: ' + (icActionTypes.length > 0 ? icActionTypes.join(', ') : 'none'));

  var fields = ['campaign_name', 'campaign_id', 'impressions', 'clicks', 'spend', 'reach', 'actions'].join(',');
  var timeRange = encodeURIComponent(JSON.stringify({ since: startDate, until: endDate }));
  var url = 'https://graph.facebook.com/' + API_VERSION + '/' + AD_ACCOUNT_ID + '/insights?' +
    'fields=' + fields + '&time_range=' + timeRange +
    '&time_increment=1&level=campaign&access_token=' + ACCESS_TOKEN;

  try {
    var response = fetchWithRetry_(url, { muteHttpExceptions: true });
    var responseCode = response.getResponseCode();
    var json = JSON.parse(response.getContentText());

    if (responseCode !== 200 || json.error) {
      Logger.log('Meta API Error (HTTP ' + responseCode + '): ' +
        (json.error ? json.error.message : response.getContentText()));
      return;
    }
    if (!json.data || json.data.length === 0) {
      Logger.log('No data returned for ' + startDate + ' to ' + endDate);
      return;
    }
    Logger.log('Meta rows (page 1): ' + json.data.length);

    var allRows = [];
    collectMetaRows_(allRows, json.data, existingKeys, icActionTypes);

    var nextUrl = json.paging && json.paging.next ? json.paging.next : null;
    var page = 1;
    while (nextUrl) {
      page++;
      var pageResponse = fetchWithRetry_(nextUrl, { muteHttpExceptions: true });
      var pageJson = JSON.parse(pageResponse.getContentText());
      if (pageJson.error) {
        Logger.log('Pagination error (page ' + page + '): ' + pageJson.error.message);
        break;
      }
      if (!pageJson.data || pageJson.data.length === 0) break;
      Logger.log('Meta rows (page ' + page + '): ' + pageJson.data.length);
      collectMetaRows_(allRows, pageJson.data, existingKeys, icActionTypes);
      nextUrl = pageJson.paging && pageJson.paging.next ? pageJson.paging.next : null;
    }

    if (allRows.length === 0) {
      Logger.log('No new rows to write (all duplicates or zero-spend).');
      return;
    }

    if (sheet.getLastRow() === 0) {
      sheet.appendRow(['Date', 'Month', 'Week', 'Campaign Name', 'Campaign ID',
        'Impressions', 'Clicks', 'Spend', 'Reach', 'Conversions', 'Frequency', 'CPL',
        'IC Conversions']);
    }
    sheet.getRange('E:E').setNumberFormat('@');

    sheet.getRange(sheet.getLastRow() + 1, 1, allRows.length, 13).setValues(allRows);
    Logger.log('Meta fetch complete. New rows written: ' + allRows.length);

  } catch (e) {
    Logger.log('Meta exception: ' + e.message + '\n' + e.stack);
  }
}

function collectMetaRows_(allRows, data, existingKeys, icActionTypes) {
  data.forEach(function (row) {
    var spend = parseFloat(row.spend) || 0;
    if (spend === 0) {
      Logger.log('Skipping $0 spend: ' + row.campaign_name + ' on ' + row.date_start);
      return;
    }

    var dedupeKey = row.date_start + '||' + row.campaign_id;
    if (existingKeys.has(dedupeKey)) {
      Logger.log('Skipping duplicate: ' + dedupeKey);
      return;
    }
    existingKeys.add(dedupeKey);

    var dp = row.date_start.split('-');
    var rowDate = new Date(parseInt(dp[0]), parseInt(dp[1]) - 1, parseInt(dp[2]));
    var monthName = getMonthName(rowDate);
    var weekNum = getWeekNumber(rowDate);

    var conversions = 0;
    var icConversions = 0;
    if (row.actions) {
      // General lead conversions (existing behavior)
      var ca = row.actions.find(function (a) {
        return a.action_type === 'lead' ||
          a.action_type === 'offsite_conversion.fb_pixel_lead' ||
          a.action_type === 'onsite_conversion.lead_grouped';
      });
      if (ca) conversions = parseInt(ca.value, 10);

      // IC-specific custom conversion(s)
      if (icActionTypes && icActionTypes.length > 0) {
        row.actions.forEach(function (a) {
          if (icActionTypes.indexOf(a.action_type) > -1) {
            icConversions += parseInt(a.value, 10) || 0;
          }
        });
      }
    }

    var impressions = parseInt(row.impressions, 10) || 0;
    var reach = parseInt(row.reach, 10) || 0;
    var clicks = parseInt(row.clicks, 10) || 0;
    var frequency = reach > 0 ? Math.round((impressions / reach) * 100) / 100 : 0;
    var cpl = conversions > 0 ? Math.round((spend / conversions) * 100) / 100 : null;

    allRows.push([
      row.date_start, monthName, weekNum,
      row.campaign_name, row.campaign_id,
      impressions, clicks, spend, reach, conversions, frequency, cpl,
      icConversions
    ]);
  });
}


// ============================================================
// HUBSPOT ICP PULL
// ============================================================

function fetchHubspotICPs() {
  Logger.log('=== fetchHubspotICPs ===');
  validateTokens_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(HS_SHEET);
  if (!sheet) sheet = ss.insertSheet(HS_SHEET);

  var headers = [
    'hs_contact_id', 'prequal_submitted', 'prequal_decision',
    'prequal_utm_source', 'prequal_utm_medium', 'prequal_utm_campaign',
    'prequal_industry', 'prequal_industry_tier',
    'prequal_funding_need', 'prequal_monthly_revenue', 'prequal_pre_approval_amount',
    'prequal_business_name', 'prequal_credit_score', 'prequal_rejection_reasons',
    'week_number', 'week_start'
  ];

  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
  }

  var existingIds = new Set();
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.getRange(2, 1, lastRow - 1, 1).getValues().forEach(function (r) {
      existingIds.add(String(r[0]));
    });
  }
  Logger.log('Existing HubSpot ICP records: ' + existingIds.size);

  var properties = [
    'prequal_submitted', 'prequal_decision',
    'prequal_utm_source', 'prequal_utm_medium', 'prequal_utm_campaign',
    'prequal_industry', 'prequal_industry_tier',
    'prequal_funding_need', 'prequal_monthly_revenue', 'prequal_pre_approval_amount',
    'prequal_business_name', 'prequal_credit_score', 'prequal_rejection_reasons'
  ];

  var payload = {
    filterGroups: [{
      filters: [{
        propertyName: 'prequal_decision',
        operator: 'EQ',
        value: 'investment_crowdfunding'
      }]
    }],
    properties: properties,
    limit: 100,
    sorts: [{ propertyName: 'prequal_submitted', direction: 'ASCENDING' }]
  };

  var after = null;
  var allRows = [];
  var pageCount = 0;

  do {
    if (after) payload.after = after;

    var response = fetchWithRetry_('https://api.hubapi.com/crm/v3/objects/contacts/search', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + HUBSPOT_API_KEY,
        'Content-Type': 'application/json'
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });

    var responseCode = response.getResponseCode();
    var json = JSON.parse(response.getContentText());
    pageCount++;

    if (responseCode !== 200 || json.status === 'error') {
      Logger.log('HubSpot API Error (HTTP ' + responseCode + '): ' +
        (json.message || response.getContentText()));
      return;
    }

    Logger.log('HubSpot page ' + pageCount + ': ' + (json.results || []).length + ' contacts');

    (json.results || []).forEach(function (contact) {
      var id = String(contact.id);
      if (existingIds.has(id)) return;

      var p = contact.properties;
      var submittedRaw = p.prequal_submitted || '';
      var submittedDate = null;

      if (submittedRaw) {
        submittedDate = new Date(submittedRaw);
        if (isNaN(submittedDate.getTime())) {
          Logger.log('WARNING: Could not parse prequal_submitted for contact ' + id + ': ' + submittedRaw);
          submittedDate = null;
        }
      }

      var weekNum = submittedDate ? getWeekNumber(submittedDate) : '';
      var weekStart = submittedDate ? getWeekStart(submittedDate) : '';

      allRows.push([
        id,
        submittedDate
          ? Utilities.formatDate(submittedDate, Session.getScriptTimeZone(), 'yyyy-MM-dd')
          : '',
        p.prequal_decision || '',
        p.prequal_utm_source || '',
        p.prequal_utm_medium || '',
        p.prequal_utm_campaign || '',
        p.prequal_industry || '',
        p.prequal_industry_tier || '',
        p.prequal_funding_need || '',
        p.prequal_monthly_revenue || '',
        p.prequal_pre_approval_amount || '',
        p.prequal_business_name || '',
        p.prequal_credit_score || '',
        p.prequal_rejection_reasons || '',
        weekNum,
        weekStart
      ]);

      existingIds.add(id);
    });

    after = (json.paging && json.paging.next) ? json.paging.next.after : null;

  } while (after);

  if (allRows.length === 0) {
    Logger.log('HubSpot pull complete. No new ICP records.');
    return;
  }

  sheet.getRange(sheet.getLastRow() + 1, 1, allRows.length, headers.length).setValues(allRows);
  Logger.log('HubSpot pull complete. New records written: ' + allRows.length);
}


// ============================================================
// WEEKLY ROLLUP
// Rebuilds entirely on each run from the two source sheets.
// Computes per-campaign per-week: spend, ICPs, CPICP, CPL,
// frequency, CTR, plus WoW and 4-week trend signals.
//
// ATTRIBUTION MODEL — HYBRID (v3, IC-conversion-based):
// Each campaign's estimated ICPs = Meta IC conversions for that campaign
// + proportional share of unattributed remainder.
//
// IC conversions come from the "Investment Crowdfunding Prequal Decision"
// custom conversion event. Meta deduplicates across campaigns — each IC
// conversion is attributed to exactly one campaign (last-click priority).
//
// Unattributed remainder = total HubSpot ICPs that day minus total IC
// conversions across all campaigns that day. Floored at zero to handle
// timing mismatches. Distributed by each campaign's share of Meta
// conversion volume.
//
// FREQUENCY: Weighted average — total impressions divided by
// total implied reach (daily_impressions / daily_frequency).
// ============================================================

function buildWeeklyRollup() {
  Logger.log('=== buildWeeklyRollup ===');
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  var metaSheet = ss.getSheetByName(META_SHEET);
  var hsSheet = ss.getSheetByName(HS_SHEET);
  var rollupSheet = ss.getSheetByName(ROLLUP_SHEET);
  if (!rollupSheet) rollupSheet = ss.insertSheet(ROLLUP_SHEET);

  if (!metaSheet) {
    Logger.log('ERROR: Sheet "' + META_SHEET + '" not found. Run fetchMetaAdsData first.');
    return;
  }
  if (!hsSheet) {
    Logger.log('ERROR: Sheet "' + HS_SHEET + '" not found. Run fetchHubspotICPs first.');
    return;
  }

  // Load UTM mappings from campaign_mapping tab.
  var utmByCampaignId = buildCampaignUTMMap_();
  if (Object.keys(utmByCampaignId).length === 0) {
    Logger.log('ERROR: No UTM mappings loaded. Populate campaign_mapping tab and re-run.');
    return;
  }

  rollupSheet.clearContents();
  rollupSheet.appendRow([
    'week_start', 'campaign_name', 'utm_campaign',
    'spend', 'impressions', 'clicks', 'reach', 'avg_frequency', 'ctr',
    'meta_conversions', 'ic_conversions', 'icps_attributed', 'estimated_icps', 'attribution_rate',
    'cpl', 'cpicp_attributed', 'cpicp_blended',
    'cpicp_blended_prior_week', 'cpicp_blended_4wk_avg',
    'cpicp_blended_wow_pct', 'cpicp_blended_vs_4wk_pct', 'icp_wow_delta'
  ]);

  // rolling_data columns: Date(0) Month(1) Week(2) Campaign Name(3) Campaign ID(4)
  //   Impressions(5) Clicks(6) Spend(7) Reach(8) Conversions(9) Frequency(10) CPL(11)
  //   IC Conversions(12)
  var metaData = metaSheet.getDataRange().getValues();

  // hubspot_icps columns: hs_id(0) submitted(1) decision(2) utm_source(3)
  //   utm_medium(4) utm_campaign(5) ... week_start(15)
  var hsData = hsSheet.getDataRange().getValues();

  // ── HYBRID ATTRIBUTION PRE-COMPUTATION ───────────────────────
  // Step 1: Count total ICPs by submitted date regardless of UTM.
  // Ground-truth ICP volume from HubSpot.
  var totalIcpsByDate = {};
  for (var hi0 = 1; hi0 < hsData.length; hi0++) {
    var subDate = dateToYMD_(hsData[hi0][1]); // prequal_submitted
    if (subDate) totalIcpsByDate[subDate] = (totalIcpsByDate[subDate] || 0) + 1;
  }

  // Step 2: Count total Meta conversions by date across all campaigns.
  var totalConvByDate = {};
  for (var ri0 = 1; ri0 < metaData.length; ri0++) {
    var dStr0 = dateToYMD_(metaData[ri0][0]);
    if (!dStr0) continue;
    totalConvByDate[dStr0] = (totalConvByDate[dStr0] || 0) + (parseInt(metaData[ri0][9]) || 0);
  }

  // Step 3: Count total IC conversions by date across all campaigns.
  // IC conversions are Meta-deduplicated (last-click priority) and serve
  // as the attribution foundation. The unattributed pool is whatever
  // HubSpot ICPs remain after subtracting total IC conversions.
  var totalICConvByDate = {};
  for (var icPass = 1; icPass < metaData.length; icPass++) {
    var icDate = dateToYMD_(metaData[icPass][0]);
    if (!icDate) continue;
    var icConv = parseInt(metaData[icPass][12]) || 0;
    totalICConvByDate[icDate] = (totalICConvByDate[icDate] || 0) + icConv;
  }
  // ─────────────────────────────────────────────────────────────

  // Aggregate Meta data by week_start + campaign_id.
  var metaByKey = {};

  for (var ri = 1; ri < metaData.length; ri++) {
    var mRow2 = metaData[ri];
    if (!mRow2[0]) continue;
    var dateStr = dateToYMD_(mRow2[0]);
    var weekStart = getWeekStart(mRow2[0]);
    var campaign = String(mRow2[3]).trim();
    var campaignId = String(mRow2[4]).trim();
    var mkey = weekStart + '||' + campaignId;
    var dailyConvs = parseInt(mRow2[9]) || 0;

    var totalConvDay = totalConvByDate[dateStr] || 0;
    var totalIcpDay = totalIcpsByDate[dateStr] || 0;

    if (!metaByKey[mkey]) {
      metaByKey[mkey] = {
        spend: 0, impressions: 0, clicks: 0, reach: 0,
        freqImpressions: 0, freqReach: 0,
        metaConversions: 0, icConversions: 0, estimatedIcps: 0,
        weekStart: weekStart, campaign: campaign, campaignId: campaignId
      };
    }
    metaByKey[mkey].spend += parseFloat(mRow2[7]) || 0;
    metaByKey[mkey].impressions += parseInt(mRow2[5]) || 0;
    metaByKey[mkey].clicks += parseInt(mRow2[6]) || 0;
    metaByKey[mkey].reach += parseInt(mRow2[8]) || 0;
    metaByKey[mkey].metaConversions += dailyConvs;
    metaByKey[mkey].icConversions += parseInt(mRow2[12]) || 0;

    // ── Hybrid attribution (v3, IC-conversion-based) ───────────
    // 1. This campaign's IC conversions for this day (Meta-attributed)
    var dailyICConv = parseInt(mRow2[12]) || 0;
    // 2. Unattributed remainder: HubSpot ICPs minus all IC conversions
    var totalICConvDay = totalICConvByDate[dateStr] || 0;
    var dailyUnattributed = Math.max(0, totalIcpDay - totalICConvDay);
    // 3. This campaign's share of unattributed, by Meta conversion volume
    var shareOfUnattributed = totalConvDay > 0
      ? dailyConvs / totalConvDay * dailyUnattributed : 0;
    // 4. Total hybrid credit = IC floor + unattributed share
    metaByKey[mkey].estimatedIcps += dailyICConv + shareOfUnattributed;
    // ───────────────────────────────────────────────────────────

    var dailyFreq = parseFloat(mRow2[10]) || 0;
    var dailyImpr = parseInt(mRow2[5]) || 0;
    if (dailyFreq > 0 && dailyImpr > 0) {
      metaByKey[mkey].freqImpressions += dailyImpr;
      metaByKey[mkey].freqReach += dailyImpr / dailyFreq;
    }
  }

  // Aggregate hard-attributed ICPs by week_start + utm_campaign (UTM match).
  // Kept as data quality reference alongside hybrid estimate.
  var icpsByKey = {};
  for (var hi = 1; hi < hsData.length; hi++) {
    var hRow = hsData[hi];
    var hsWeekStart = dateToYMD_(hRow[15]);
    var utmCampaign = String(hRow[5]).trim();
    if (!hsWeekStart || !utmCampaign) continue;
    var hkey = hsWeekStart + '||' + utmCampaign;
    icpsByKey[hkey] = (icpsByKey[hkey] || 0) + 1;
  }

  // Build rollup rows
  var rollupRows = [];
  var unmappedCampaigns = [];

  Object.values(metaByKey).forEach(function (m) {
    var utmValue = utmByCampaignId[m.campaignId];
    if (!utmValue) {
      if (unmappedCampaigns.indexOf(m.campaign) === -1) unmappedCampaigns.push(m.campaign);
      return;
    }

    var icpKey = m.weekStart + '||' + utmValue;
    var icpsAttrib = icpsByKey[icpKey] || 0;
    var estimatedIcps = Math.round(m.estimatedIcps * 10) / 10;
    var attrRate = m.icConversions > 0 && estimatedIcps > 0
      ? Math.round((m.icConversions / estimatedIcps) * 1000) / 10 : null;

    var avgFreq = m.freqReach > 0 ? Math.round((m.freqImpressions / m.freqReach) * 100) / 100 : 0;
    var ctr = m.impressions > 0 ? Math.round((m.clicks / m.impressions) * 10000) / 10000 : 0;
    var cpl = m.metaConversions > 0 ? Math.round((m.spend / m.metaConversions) * 100) / 100 : null;
    var cpicpAttrib = icpsAttrib > 0 ? Math.round((m.spend / icpsAttrib) * 100) / 100 : null;
    var cpicpBlended = estimatedIcps > 0 ? Math.round((m.spend / estimatedIcps) * 100) / 100 : null;

    rollupRows.push({
      week_start: m.weekStart, campaign: m.campaign, utm: utmValue,
      spend: m.spend, impressions: m.impressions, clicks: m.clicks, reach: m.reach,
      avgFreq: avgFreq, ctr: ctr,
      metaConversions: m.metaConversions, icConversions: m.icConversions,
      icpsAttrib: icpsAttrib, estimatedIcps: estimatedIcps, attrRate: attrRate,
      cpl: cpl, cpicpAttrib: cpicpAttrib, cpicpBlended: cpicpBlended
    });
  });

  if (unmappedCampaigns.length > 0) {
    // Log only — Slack alerting for unmapped campaigns is handled by
    // syncCampaignMappings_ to avoid duplicate/daily spam.
    Logger.log('WARNING: Unmapped after auto-sync (excluded from rollup): ' +
      unmappedCampaigns.join(', '));
  }

  rollupRows.sort(function (a, b) {
    var w = a.week_start.localeCompare(b.week_start);
    return w !== 0 ? w : a.campaign.localeCompare(b.campaign);
  });

  var history = {};
  rollupRows.forEach(function (r) {
    if (!history[r.campaign]) history[r.campaign] = [];
    history[r.campaign].push({
      week: r.week_start,
      cpicpBlended: r.cpicpBlended,
      estimatedIcps: r.estimatedIcps
    });
  });

  var outputRows = [];
  rollupRows.forEach(function (r) {
    var h = history[r.campaign];
    var idx = -1;
    for (var k = 0; k < h.length; k++) {
      if (h[k].week === r.week_start) { idx = k; break; }
    }

    var priorBlended = idx > 0 ? h[idx - 1].cpicpBlended : null;
    var prior4valid = h.slice(Math.max(0, idx - 4), idx)
      .filter(function (x) { return x.cpicpBlended !== null; });
    var avg4Blended = prior4valid.length > 0
      ? prior4valid.reduce(function (s, x) { return s + x.cpicpBlended; }, 0) / prior4valid.length
      : null;

    var wowPct = (r.cpicpBlended !== null && priorBlended !== null)
      ? Math.round(((r.cpicpBlended - priorBlended) / priorBlended) * 1000) / 10 : null;
    var vsPct = (r.cpicpBlended !== null && avg4Blended !== null)
      ? Math.round(((r.cpicpBlended - avg4Blended) / avg4Blended) * 1000) / 10 : null;

    var priorIcps = idx > 0 ? h[idx - 1].estimatedIcps : null;
    var icpWow = priorIcps !== null
      ? Math.round((r.estimatedIcps - priorIcps) * 10) / 10 : null;

    outputRows.push([
      r.week_start, r.campaign, r.utm,
      Math.round(r.spend * 100) / 100,
      r.impressions, r.clicks, r.reach, r.avgFreq, r.ctr,
      r.metaConversions, r.icConversions, r.icpsAttrib, r.estimatedIcps, r.attrRate,
      r.cpl, r.cpicpAttrib, r.cpicpBlended,
      priorBlended,
      avg4Blended !== null ? Math.round(avg4Blended * 100) / 100 : null,
      wowPct, vsPct, icpWow
    ]);
  });

  if (outputRows.length > 0) {
    var range = rollupSheet.getRange(2, 1, outputRows.length, 22);
    range.setValues(outputRows);
    rollupSheet.getRange(2, 1, outputRows.length, 1).setNumberFormat('@');
  }
  Logger.log('Rollup complete. Rows written: ' + outputRows.length);
}


// ============================================================
// WEEKLY INTELLIGENCE NARRATIVE
// ============================================================

function generateWeeklyNarrative() {
  Logger.log('=== generateWeeklyNarrative ===');
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var rollupSheet = ss.getSheetByName(ROLLUP_SHEET);
  if (!rollupSheet) {
    Logger.log('ERROR: weekly_rollup sheet not found. Run buildWeeklyRollup first.');
    return;
  }
  var data = rollupSheet.getDataRange().getValues();
  if (data.length < 2) {
    Logger.log('ERROR: weekly_rollup is empty. Run buildWeeklyRollup first.');
    return;
  }
  var weekSet = {};
  var allWeeks = [];
  data.slice(1).forEach(function (r) {
    var w = dateToYMD_(r[0]);
    if (w && !weekSet[w]) { weekSet[w] = true; allWeeks.push(w); }
  });
  allWeeks.sort();
  var targetWeek = getMostRecentCompletedWeek_(allWeeks);
  if (!targetWeek) {
    Logger.log('No completed week available. Current week is still in progress.');
    return;
  }

  var logSheet = ss.getSheetByName(INTEL_SHEET);
  if (logSheet && logSheet.getLastRow() > 1) {
    var logData = logSheet.getDataRange().getValues();
    for (var li = 1; li < logData.length; li++) {
      if (resolveReportingWeek_(logData[li][1]) === targetWeek) {
        Logger.log('Narrative already exists for ' + targetWeek + ' (row ' + (li + 1) +
          '). Skipping. Use generateNarrativeForWeek_ with overwrite:true to regenerate.');
        return;
      }
    }
  }

  generateNarrativeForWeek_(targetWeek, { postToSlack: true, overwrite: false });
  Logger.log('=== generateWeeklyNarrative complete ===');
}


// Resolves any reporting_week cell value to a YYYY-MM-DD string.
// Handles Date objects, "YYYY-MM-DD" strings, and JS Date.toString() output
// like "Sun Mar 15 2026 00:00:00 GMT-0400 (Eastern Daylight Time)".
function resolveReportingWeek_(val) {
  var tz = Session.getScriptTimeZone();
  if (val instanceof Date) {
    return Utilities.formatDate(val, tz, 'yyyy-MM-dd');
  }
  var s = String(val).trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
    return s.substring(0, 10);
  }
  try {
    var parsed = new Date(s);
    if (!isNaN(parsed.getTime())) {
      return Utilities.formatDate(parsed, tz, 'yyyy-MM-dd');
    }
  } catch (e) { /* unparseable — fall through */ }
  return null;
}


// Core narrative generator for a specific week.
// targetWeek must be a Monday-dated YYYY-MM-DD string.
// opts.postToSlack (default true): post the Slack digest after writing.
// opts.overwrite (default false): delete existing intelligence_log rows
//   for this week window (Monday + preceding Sunday) before appending.
function generateNarrativeForWeek_(targetWeek, opts) {
  opts = opts || {};
  var postSlack = opts.postToSlack !== false;
  var overwrite = !!opts.overwrite;

  Logger.log('--- generateNarrativeForWeek_: ' + targetWeek +
    ' (overwrite=' + overwrite + ', slack=' + postSlack + ') ---');

  if (typeof targetWeek !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(targetWeek)) {
    Logger.log('ERROR: targetWeek must be a YYYY-MM-DD string, got: ' +
      typeof targetWeek + ' "' + String(targetWeek).substring(0, 40) + '". Aborting.');
    return;
  }

  // Monday assertion — parse YYYY-MM-DD component parts to stay in script
  // timezone. new Date('2026-03-09') parses as UTC midnight, which is
  // Sunday 8 PM in ET — wrong day-of-week.
  var twParts = targetWeek.split('-');
  var twDate = new Date(parseInt(twParts[0]), parseInt(twParts[1]) - 1, parseInt(twParts[2]));
  if (twDate.getDay() !== 1) {
    Logger.log('ERROR: ' + targetWeek + ' is not a Monday (day=' + twDate.getDay() + '). Aborting.');
    return;
  }

  validateTokens_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var rollupSheet = ss.getSheetByName(ROLLUP_SHEET);
  if (!rollupSheet || rollupSheet.getLastRow() < 2) {
    Logger.log('ERROR: weekly_rollup sheet missing or empty.');
    return;
  }

  var data = rollupSheet.getDataRange().getValues();

  var weekSet = {};
  var allWeeks = [];
  data.slice(1).forEach(function (r) {
    var w = dateToYMD_(r[0]);
    if (w && !weekSet[w]) { weekSet[w] = true; allWeeks.push(w); }
  });
  allWeeks.sort();

  var weekRows = data.slice(1).filter(function (r) { return dateToYMD_(r[0]) === targetWeek; });
  if (weekRows.length === 0) {
    Logger.log('ERROR: No rollup rows found for week ' + targetWeek);
    return;
  }

  Logger.log('Generating narrative for week of: ' + targetWeek +
    ' (' + weekRows.length + ' campaign rows)');

  var totalSpend = 0, totalICPs = 0, totalAttrICPs = 0, totalConversions = 0, totalICConversions = 0;
  weekRows.forEach(function (r) {
    totalSpend += r[3] || 0;
    totalICPs += r[12] || 0;
    totalAttrICPs += r[11] || 0;
    totalConversions += r[9] || 0;
    totalICConversions += r[10] || 0;
  });
  totalSpend = Math.round(totalSpend * 100) / 100;
  totalICPs = Math.round(totalICPs * 10) / 10;
  totalAttrICPs = Math.round(totalAttrICPs * 10) / 10;
  var overallCPICP = totalICPs > 0 ? (totalSpend / totalICPs).toFixed(2) : 'N/A';
  var overallCPL = totalConversions > 0 ? (totalSpend / totalConversions).toFixed(2) : 'N/A';
  var overallAttrRate = totalICPs > 0
    ? Math.round((totalICConversions / totalICPs) * 1000) / 10 + '%' : 'N/A';

  var sortedRows = weekRows.slice().sort(function (a, b) {
    if (a[16] === null && b[16] === null) return 0;
    if (a[16] === null) return 1;
    if (b[16] === null) return -1;
    return a[16] - b[16];
  });

  var campaignLines = sortedRows.map(function (r) {
    var wowStr = (r[19] !== null && r[19] !== '')
      ? (r[19] > 0 ? '+' + r[19] + '%' : r[19] + '%') : 'no prior week';
    var vsStr = (r[20] !== null && r[20] !== '')
      ? (r[20] > 0 ? '+' + r[20] + '%' : r[20] + '%') : 'no baseline yet';
    var cpicpStr = r[16] !== null ? '$' + r[16] : 'no est. ICPs';
    var attrStr = r[13] !== null ? r[13] + '% IC attributed' : 'no IC data';
    var icConvStr = (r[10] || 0) > 0 ? ' | IC convs ' + r[10] : '';
    return '  ' + r[2] + ' (' + r[1] + ')' +
      ': spend $' + (r[3] || 0).toFixed(0) +
      ' | est. ICPs ' + (r[12] || 0) +
      ' | CPICP ' + cpicpStr +
      ' | WoW ' + wowStr +
      ' | vs 4wk avg ' + vsStr +
      ' | freq ' + (r[7] || 0) +
      ' | attr. quality ' + attrStr +
      icConvStr;
  }).join('\n');

  var freqAlerts = weekRows
    .filter(function (r) { return (r[7] || 0) > 3.5; })
    .map(function (r) { return '  ' + r[2] + ': frequency ' + r[7]; })
    .join('\n') || '  None';

  var spikeAlerts = weekRows
    .filter(function (r) { return r[20] !== null && r[20] !== '' && r[20] > 25 && (r[12] || 0) > 0; })
    .map(function (r) { return '  ' + r[2] + ': CPICP $' + r[16] + ' (+' + r[20] + '% vs 4wk avg)'; })
    .join('\n') || '  None';

  var zeroICPLines = weekRows
    .filter(function (r) { return (r[12] || 0) === 0 && (r[3] || 0) > 0; })
    .map(function (r) { return '  ' + r[2] + ': $' + (r[3] || 0).toFixed(0) + ' spend, 0 est. ICPs'; })
    .join('\n') || '  None';

  var contextBlock = [
    'REPORTING WEEK: ' + targetWeek,
    'TOTAL SPEND: $' + totalSpend.toFixed(2),
    'TOTAL ICPs (hybrid est.): ' + totalICPs,
    'TOTAL ICPs (UTM attributed): ' + totalAttrICPs,
    'TOTAL IC CONVERSIONS (direct from Meta custom conversion): ' + totalICConversions,
    'ATTRIBUTION QUALITY: ' + overallAttrRate + ' of estimated ICPs backed by IC conversions',
    'OVERALL CPICP (hybrid): $' + overallCPICP,
    'OVERALL CPL: $' + overallCPL,
    '',
    'NOTE: ICP counts use hybrid attribution (v3). Each campaign receives its Meta IC conversions',
    '(deduplicated by Meta, last-click priority) plus a proportional share of unattributed ICPs',
    '(HubSpot ICPs not traceable to any campaign, distributed by Meta conversion volume).',
    '',
    'IC CONVERSIONS: Meta tracks the "Investment Crowdfunding Prequal Decision" custom conversion',
    'across all campaigns. IC conversions are the attribution foundation — each ICP decision is',
    'attributed to exactly one campaign by Meta (last-click priority, deduplicated).',
    '',
    'CAMPAIGN BREAKDOWN (sorted by hybrid CPICP, best first):',
    campaignLines,
    '',
    'FREQUENCY ALERTS (threshold: >3.5):',
    freqAlerts,
    '',
    'CPICP SPIKE ALERTS (>25% above 4-week average):',
    spikeAlerts,
    '',
    'ZERO-ICP CAMPAIGNS WITH ACTIVE SPEND:',
    zeroICPLines
  ].join('\n');

  Logger.log('Context block:\n' + contextBlock);

  var systemPrompt = [
    'You are a performance marketing analyst for Honeycomb Credit, a fintech providing investment crowdfunding capital to small food and beverage businesses.',
    'Audience: Marketing Director, Sales Director, CEO, CFO.',
    'Primary metrics: CPICP, CPL, frequency, spend, segment conversion.',
    '',
    'ICP = contact where Prequal Decision = investment_crowdfunding.',
    'ICP counts use hybrid attribution (v3): Meta IC conversions (deduplicated, last-click) + proportional share of unattributed pool.',
    'IC conversions are the foundation. Unattributed ICPs (organic, email, lost UTMs) distributed by Meta conversion volume.',
    '',
    'Write a SHORT weekly summary for Slack. Plain text only.',
    'No Markdown headers (no ##, ###), no horizontal rules (no ---), no bold (**text**).',
    'Use plain section labels. Total length: 150-220 words maximum.',
    '',
    'Format exactly as below — use these exact section labels:',
    '',
    'OVERALL',
    '1-2 direct sentences. State verdict and key number. Flag attribution quality if below 50%.',
    '',
    'SEGMENTS',
    '- [utm value]: $[CPICP] CPICP, [N] ICPs  (one line per campaign with ICPs, best first)',
    '- Zero ICPs: [utm values that spent but produced nothing, comma-separated]',
    '',
    'WATCH',
    '- One line per flag (dead spend, CPICP spike, frequency issue)',
    '',
    'ACTION',
    '- One line per recommendation. Specific. No hedging.',
    '',
    'Rules: Numbers only from data. No invented figures. No softening.'
  ].join('\n');

  var narrative = '[LLM call failed — see context_block column in intelligence_log for raw data]';

  try {
    var llmResponse = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
      },
      payload: JSON.stringify({
        model: ANTHROPIC_MODEL,
        max_tokens: 1000,
        system: systemPrompt,
        messages: [{ role: 'user', content: contextBlock }]
      }),
      muteHttpExceptions: true
    });

    var llmCode = llmResponse.getResponseCode();
    var llmJson = JSON.parse(llmResponse.getContentText());

    if (llmCode !== 200) {
      Logger.log('Anthropic API Error (HTTP ' + llmCode + '): ' + llmResponse.getContentText());
    } else if (llmJson.content && llmJson.content[0] && llmJson.content[0].text) {
      narrative = llmJson.content[0].text;
      Logger.log('LLM narrative generated successfully');
    } else {
      Logger.log('Unexpected Anthropic response: ' + llmResponse.getContentText());
    }
  } catch (e) {
    Logger.log('Anthropic exception: ' + e.message);
  }

  // ── Write to intelligence_log ──────────────────────
  var logSheet = ss.getSheetByName(INTEL_SHEET);
  if (!logSheet) logSheet = ss.insertSheet(INTEL_SHEET);
  if (logSheet.getLastRow() === 0) {
    logSheet.appendRow(['generated_at', 'reporting_week', 'total_spend', 'total_icps',
      'overall_cpicp', 'context_block', 'narrative']);
  }

  if (overwrite && logSheet.getLastRow() > 1) {
    var tz = Session.getScriptTimeZone();
    var sundayBefore = new Date(twDate);
    sundayBefore.setDate(sundayBefore.getDate() - 1);
    var sundayStr = Utilities.formatDate(sundayBefore, tz, 'yyyy-MM-dd');

    var logData = logSheet.getDataRange().getValues();
    for (var i = logData.length - 1; i >= 1; i--) {
      var resolved = resolveReportingWeek_(logData[i][1]);
      if (resolved === targetWeek || resolved === sundayStr) {
        Logger.log('  DELETE row ' + (i + 1) + ': reporting_week raw="' +
          String(logData[i][1]).substring(0, 40) + '" → resolved=' + resolved);
        logSheet.deleteRow(i + 1);
      }
    }
  }

  logSheet.appendRow([
    new Date().toISOString(), targetWeek,
    totalSpend.toFixed(2), totalICPs, overallCPICP,
    contextBlock, narrative
  ]);
  Logger.log('  APPEND: reporting_week=' + targetWeek +
    ' spend=$' + totalSpend.toFixed(2) + ' icps=' + totalICPs);

  // ── Reconciliation: verify spend matches rollup ────
  var rollupCheck = 0;
  var freshRollup = rollupSheet.getDataRange().getValues();
  for (var ri = 1; ri < freshRollup.length; ri++) {
    if (dateToYMD_(freshRollup[ri][0]) === targetWeek) {
      rollupCheck += freshRollup[ri][3] || 0;
    }
  }
  var spendGap = Math.abs(totalSpend - rollupCheck);
  if (spendGap > 0.01) {
    Logger.log('WARNING: RECONCILIATION FAILED for ' + targetWeek +
      ': narrative=$' + totalSpend.toFixed(2) +
      ' rollup=$' + rollupCheck.toFixed(2) +
      ' gap=$' + spendGap.toFixed(2));
  } else {
    Logger.log('  Reconciliation OK: spend=$' + totalSpend.toFixed(2) + ' matches rollup');
  }

  if (postSlack) {
    postWeeklyNarrativeToSlack_(
      targetWeek, totalSpend, totalICPs, overallCPICP, overallCPL,
      totalAttrICPs, overallAttrRate, weekRows, allWeeks, data, narrative
    );
  }
}


// One-time backfill: regenerate the three Sunday-convention narrative rows
// under the correct Monday convention. Run once from the Apps Script editor
// after deploying this code.
//
// Step 1: Dry run on a known-good Monday week (2026-04-06) to validate the
//   core helper. That row already exists and is Monday-aligned — if the
//   overwrite works, reconciliation passes, and the new row has the same
//   totals, the machinery is sound.
// Step 2: Backfill the three historical weeks that used the old Sunday
//   convention (3/8 → 3/9, 3/15 → 3/16, 3/22 → 3/23).
function backfillHistoricalNarratives() {
  Logger.log('=== backfillHistoricalNarratives ===');

  Logger.log('--- DRY RUN: overwriting known-good week 2026-04-06 ---');
  generateNarrativeForWeek_('2026-04-06', { postToSlack: false, overwrite: true });
  Logger.log('--- Dry run complete. Check reconciliation above. ---');
  Utilities.sleep(2000);

  var backfillWeeks = ['2026-03-09', '2026-03-16', '2026-03-23'];
  for (var i = 0; i < backfillWeeks.length; i++) {
    Logger.log('--- BACKFILL ' + (i + 1) + '/3: ' + backfillWeeks[i] + ' ---');
    generateNarrativeForWeek_(backfillWeeks[i], { postToSlack: false, overwrite: true });
    if (i < backfillWeeks.length - 1) Utilities.sleep(2000);
  }

  Logger.log('=== backfillHistoricalNarratives complete ===');
  Logger.log('Verify: intelligence_log should now have Monday-aligned rows for');
  Logger.log('  2026-03-09, 2026-03-16, 2026-03-23, and 2026-04-06.');
  Logger.log('  All Sunday-dated rows (3/8, 3/15, 3/22) and the malformed');
  Logger.log('  Date.toString() row should be deleted.');
}


// ============================================================
// WEEKLY NARRATIVE SLACK POST
// ============================================================

function postWeeklyNarrativeToSlack_(week, spend, icps, cpicp, cpl,
  attrICPs, attrRate, weekRows, allWeeks, rollupData, narrative) {

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tz = Session.getScriptTimeZone();

  var yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
  var ydStr = Utilities.formatDate(yesterday, tz, 'yyyy-MM-dd');
  var ydLabel = Utilities.formatDate(yesterday, tz, 'MMM d');
  var metaSheet = ss.getSheetByName(META_SHEET);
  var ydSpend = 0, ydConvs = 0, ydClicks = 0;
  if (metaSheet) {
    var metaData = metaSheet.getDataRange().getValues();
    for (var mi = 1; mi < metaData.length; mi++) {
      if (dateToYMD_(metaData[mi][0]) !== ydStr) continue;
      ydSpend += parseFloat(metaData[mi][7]) || 0;
      ydConvs += parseInt(metaData[mi][9]) || 0;
      ydClicks += parseInt(metaData[mi][6]) || 0;
    }
  }

  var hsSheet = ss.getSheetByName(HS_SHEET);
  var ydICPs = 0;
  if (hsSheet) {
    var hsData = hsSheet.getDataRange().getValues();
    for (var hi = 1; hi < hsData.length; hi++) {
      if (dateToYMD_(hsData[hi][1]) === ydStr) ydICPs++;
    }
  }
  var ydCPICP = ydICPs > 0 ? '$' + (ydSpend / ydICPs).toFixed(0) : 'N/A';
  var ydCPL = ydConvs > 0 ? '$' + (ydSpend / ydConvs).toFixed(2) : 'N/A';

  var priorWeek = null;
  var completed = allWeeks.filter(function (w) { return w < week; });
  if (completed.length > 0) priorWeek = completed[completed.length - 1];
  var wowStr = '';
  if (priorWeek) {
    var priorRows = rollupData.slice(1).filter(function (r) { return dateToYMD_(r[0]) === priorWeek; });
    var priorSpend = 0, priorICPs = 0;
    priorRows.forEach(function (r) { priorSpend += r[3] || 0; priorICPs += r[12] || 0; });
    if (priorICPs > 0 && cpicp !== 'N/A') {
      var priorCPICP = priorSpend / priorICPs;
      var wowPct = Math.round(((parseFloat(cpicp) - priorCPICP) / priorCPICP) * 1000) / 10;
      wowStr = '  |  WoW: ' + (wowPct > 0 ? '+' : '') + wowPct + '%  (prior $' + priorCPICP.toFixed(0) + ')';
    }
  }

  var last4 = completed.slice(-4);
  var d30Rows = rollupData.slice(1).filter(function (r) { return last4.indexOf(dateToYMD_(r[0])) > -1; });
  var d30Spend = 0, d30ICPs = 0, d30Convs = 0;
  d30Rows.forEach(function (r) { d30Spend += r[3] || 0; d30ICPs += r[12] || 0; d30Convs += r[9] || 0; });
  var d30CPICP = d30ICPs > 0 ? '$' + (d30Spend / d30ICPs).toFixed(0) : 'N/A';
  var d30CPL = d30Convs > 0 ? '$' + (d30Spend / d30Convs).toFixed(2) : 'N/A';

  var prior4 = completed.slice(-8, -4);
  var p30Rows = rollupData.slice(1).filter(function (r) { return prior4.indexOf(dateToYMD_(r[0])) > -1; });
  var p30Spend = 0, p30ICPs = 0;
  p30Rows.forEach(function (r) { p30Spend += r[3] || 0; p30ICPs += r[12] || 0; });
  var d30WowStr = '';
  if (p30ICPs > 0 && d30ICPs > 0) {
    var p30CPICP = p30Spend / p30ICPs;
    var c30CPICP = d30Spend / d30ICPs;
    var d30Pct = Math.round(((c30CPICP - p30CPICP) / p30CPICP) * 1000) / 10;
    d30WowStr = '  |  vs prior 30: ' + (d30Pct > 0 ? '+' : '') + d30Pct + '%';
  }

  var text = '*Honeycomb Ads — Week of ' + week + '*\n\n';

  text += '*Yesterday (' + ydLabel + ')*\n';
  text += 'Spend: $' + ydSpend.toFixed(0) +
    '  |  ICPs: ' + ydICPs +
    '  |  CPICP: ' + ydCPICP +
    '  |  CPL: ' + ydCPL + '\n\n';

  text += '*Week of ' + week + '*\n';
  text += 'Spend: $' + Number(spend).toFixed(0) +
    '  |  ICPs: ' + Math.round(icps * 10) / 10 +
    '  |  CPICP: $' + cpicp +
    '  |  CPL: $' + cpl +
    wowStr + '\n';
  if (attrRate !== 'N/A') {
    text += '_IC attribution: ' + attrRate + ' of estimated ICPs_\n';
  }
  text += '\n';

  text += '*Last 30 days*\n';
  text += 'Spend: $' + d30Spend.toFixed(0) +
    '  |  ICPs: ' + Math.round(d30ICPs * 10) / 10 +
    '  |  CPICP: ' + d30CPICP +
    '  |  CPL: ' + d30CPL +
    d30WowStr + '\n\n';

  text += '─────────────────\n' + narrative;

  var budgetSummary = buildBudgetWeeklySummary_();
  if (budgetSummary) {
    text += '\n\n─────────────────\n' + budgetSummary;
  }

  postToSlack_(text);
  Logger.log('Weekly narrative posted to Slack');
}


// ============================================================
// DAILY SLACK DIGEST
// ============================================================

function postDailyDigest() {
  Logger.log('=== postDailyDigest ===');
  validateTokens_();
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  var metaSheet = ss.getSheetByName(META_SHEET);
  var hsSheet = ss.getSheetByName(HS_SHEET);
  var rollupSheet = ss.getSheetByName(ROLLUP_SHEET);
  if (!metaSheet || !rollupSheet) {
    Logger.log('ERROR: rolling_data or weekly_rollup sheet not found.');
    return;
  }

  var tz = Session.getScriptTimeZone();
  var yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
  var ydStr = Utilities.formatDate(yesterday, tz, 'yyyy-MM-dd');
  var ydLabel = Utilities.formatDate(yesterday, tz, 'EEE MMM d');

  var metaData = metaSheet.getDataRange().getValues();
  var ydSpend = 0, ydConvs = 0, ydClicks = 0, ydFreqSum = 0, ydFreqCount = 0;
  var ydByCampaign = [];
  for (var mi = 1; mi < metaData.length; mi++) {
    if (dateToYMD_(metaData[mi][0]) !== ydStr) continue;
    var rs = parseFloat(metaData[mi][7]) || 0;
    var rc = parseInt(metaData[mi][9]) || 0;
    var rf = parseFloat(metaData[mi][10]) || 0;
    ydSpend += rs;
    ydConvs += rc;
    ydClicks += parseInt(metaData[mi][6]) || 0;
    if (rf > 0) { ydFreqSum += rf; ydFreqCount++; }
    ydByCampaign.push({ name: String(metaData[mi][3]), spend: rs, convs: rc, freq: rf });
  }

  var ydICPs = 0;
  if (hsSheet) {
    var hsData = hsSheet.getDataRange().getValues();
    for (var hi = 1; hi < hsData.length; hi++) {
      if (dateToYMD_(hsData[hi][1]) === ydStr) ydICPs++;
    }
  }

  var ydCPL = ydConvs > 0 ? '$' + (ydSpend / ydConvs).toFixed(2) : 'N/A';
  var ydCPICP = ydICPs > 0 ? '$' + (ydSpend / ydICPs).toFixed(0) : 'N/A';
  var ydFreq = ydFreqCount > 0 ? Math.round((ydFreqSum / ydFreqCount) * 100) / 100 : 'N/A';

  var rollupData = rollupSheet.getDataRange().getValues();
  var weekSet = {}, allWeeks = [];
  rollupData.slice(1).forEach(function (r) {
    var w = dateToYMD_(r[0]);
    if (w && !weekSet[w]) { weekSet[w] = true; allWeeks.push(w); }
  });
  allWeeks.sort();
  if (!allWeeks.length) { Logger.log('No rollup data.'); return; }

  var currentWeek = allWeeks[allWeeks.length - 1];
  var wtdRows = rollupData.slice(1).filter(function (r) { return dateToYMD_(r[0]) === currentWeek; });

  var wtdSpend = 0, wtdICPs = 0, wtdConvs = 0;
  wtdRows.forEach(function (r) {
    wtdSpend += r[3] || 0;
    wtdICPs += r[12] || 0;
    wtdConvs += r[9] || 0;
  });
  var wtdCPICP = wtdICPs > 0 ? '$' + (wtdSpend / wtdICPs).toFixed(0) : 'N/A';
  var wtdCPL = wtdConvs > 0 ? '$' + (wtdSpend / wtdConvs).toFixed(2) : 'N/A';

  var wsParts = currentWeek.split('-');
  var wsDate = new Date(parseInt(wsParts[0]), parseInt(wsParts[1]) - 1, parseInt(wsParts[2]));
  var ydDate = new Date(); ydDate.setDate(ydDate.getDate() - 1);
  var daysElapsed = Math.max(1, Math.round((ydDate - wsDate) / 86400000) + 1);
  var pacedSpend = Math.round(wtdSpend / daysElapsed * 7);
  var pacedICPs = Math.round(wtdICPs / daysElapsed * 7 * 10) / 10;
  var wtdPaceLine = 'Pacing → $' + pacedSpend.toLocaleString() + ' spend  |  ' + pacedICPs + ' ICPs  (' + daysElapsed + ' of 7 days)';

  var completed = allWeeks.filter(function (w) { return w < currentWeek; });
  var wtdWoWStr = '';
  if (completed.length > 0) {
    var priorWeek = completed[completed.length - 1];
    var priorRows = rollupData.slice(1).filter(function (r) { return dateToYMD_(r[0]) === priorWeek; });
    var priorSpend = 0, priorICPs = 0;
    priorRows.forEach(function (r) { priorSpend += r[3] || 0; priorICPs += r[12] || 0; });
    if (priorICPs > 0 && wtdICPs > 0) {
      var priorCPICP = priorSpend / priorICPs;
      var wtdCPICPNum = wtdSpend / wtdICPs;
      var wowPct = Math.round(((wtdCPICPNum - priorCPICP) / priorCPICP) * 1000) / 10;
      wtdWoWStr = '  |  WoW: ' + (wowPct > 0 ? '+' : '') + wowPct + '%';
    }
  }

  var last4 = completed.slice(-4);
  var d30Rows = rollupData.slice(1).filter(function (r) { return last4.indexOf(dateToYMD_(r[0])) > -1; });
  var d30Spend = 0, d30ICPs = 0, d30Convs = 0;
  d30Rows.forEach(function (r) { d30Spend += r[3] || 0; d30ICPs += r[12] || 0; d30Convs += r[9] || 0; });
  var d30CPICP = d30ICPs > 0 ? '$' + (d30Spend / d30ICPs).toFixed(0) : 'N/A';
  var d30CPL = d30Convs > 0 ? '$' + (d30Spend / d30Convs).toFixed(2) : 'N/A';

  var d30WeeklySpend = last4.length > 0 ? Math.round(d30Spend / last4.length) : 0;
  var d30WeeklyICPs = last4.length > 0 ? Math.round(d30ICPs / last4.length * 10) / 10 : 0;
  var d30PaceLine = 'Run rate → $' + d30WeeklySpend.toLocaleString() + '/week  |  ' + d30WeeklyICPs + ' ICPs/week';

  var watchLine = '';
  var highFreq = ydByCampaign.filter(function (c) { return c.freq > 3.5; })
    .sort(function (a, b) { return b.freq - a.freq; });
  var zeroConv = ydByCampaign.filter(function (c) { return c.convs === 0 && c.spend > 50; })
    .sort(function (a, b) { return b.spend - a.spend; });
  if (highFreq.length > 0) {
    watchLine = '⚠️ ' + highFreq[0].name + ': freq ' + Math.round(highFreq[0].freq * 100) / 100;
  } else if (zeroConv.length > 0) {
    watchLine = '⚠️ ' + zeroConv[0].name + ': $' + zeroConv[0].spend.toFixed(0) + ' spend, 0 conversions';
  }

  var commentary = '';
  try {
    var ctx = [
      'Yesterday (' + ydStr + '): spend $' + ydSpend.toFixed(0) +
      ', conversions ' + ydConvs + ', ICPs ' + ydICPs +
      ', CPICP ' + ydCPICP + ', CPL ' + ydCPL + ', avg freq ' + ydFreq,
      'WTD (week of ' + currentWeek + '): spend $' + wtdSpend.toFixed(0) +
      ', est. ICPs ' + Math.round(wtdICPs * 10) / 10 +
      ', CPICP ' + wtdCPICP + (wtdWoWStr ? ', ' + wtdWoWStr.trim() : ''),
      'Last 30 days: spend $' + d30Spend.toFixed(0) +
      ', est. ICPs ' + Math.round(d30ICPs * 10) / 10 +
      ', CPICP ' + d30CPICP,
      watchLine ? 'Watch: ' + watchLine.replace('⚠️ ', '') : ''
    ].filter(Boolean).join('\n');

    var llmResp = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
      },
      payload: JSON.stringify({
        model: ANTHROPIC_MODEL, max_tokens: 200,
        system: 'You are a terse performance marketing analyst for Honeycomb Credit. ' +
          'Write exactly 2 sentences assessing yesterday\'s ad performance. ' +
          'First sentence: verdict on yesterday (good/bad/neutral) and the key reason. ' +
          'Second sentence: one specific implication or action. ' +
          'No fluff. Numbers only from the data provided. Plain text, no markdown.',
        messages: [{ role: 'user', content: ctx }]
      }),
      muteHttpExceptions: true
    });
    var llmJson = JSON.parse(llmResp.getContentText());
    if (llmResp.getResponseCode() === 200 && llmJson.content && llmJson.content[0]) {
      commentary = llmJson.content[0].text.trim();
    }
  } catch (e) {
    Logger.log('Daily commentary LLM exception: ' + e.message);
  }

  var text = '*Honeycomb Ads — ' + ydLabel + '*\n\n';

  text += '*Yesterday*\n';
  text += 'Spend: $' + ydSpend.toFixed(0) +
    '  |  ICPs: ' + ydICPs +
    '  |  CPICP: ' + ydCPICP +
    '  |  CPL: ' + ydCPL +
    '  |  Freq: ' + ydFreq + '\n\n';

  text += '*WTD (week of ' + currentWeek + ')*\n';
  text += 'Spend: $' + wtdSpend.toFixed(0) +
    '  |  ICPs: ' + Math.round(wtdICPs * 10) / 10 +
    '  |  CPICP: ' + wtdCPICP +
    '  |  CPL: ' + wtdCPL +
    wtdWoWStr + '\n';
  text += '_' + wtdPaceLine + '_\n\n';

  text += '*30-day*\n';
  text += 'Spend: $' + d30Spend.toFixed(0) +
    '  |  ICPs: ' + Math.round(d30ICPs * 10) / 10 +
    '  |  CPICP: ' + d30CPICP +
    '  |  CPL: ' + d30CPL + '\n';
  text += '_' + d30PaceLine + '_\n';

  if (watchLine) text += '\n' + watchLine + '\n';
  if (commentary) text += '\n' + commentary;

  postToSlack_(text);
  Logger.log('Daily digest posted to Slack');
}


// ============================================================
// MASTER DAILY PIPELINE
// ============================================================

function runDailyPipeline() {
  Logger.log('=== runDailyPipeline START ===');
  fetchMetaAdsData();
  Utilities.sleep(3000);
  fetchHubspotICPs();
  Utilities.sleep(3000);
  buildWeeklyRollup();
  Utilities.sleep(2000);
  postDailyDigest();
  Logger.log('=== runDailyPipeline COMPLETE ===');
}


// ============================================================
// TRIGGER SETUP — REPORTING PIPELINE
// ============================================================

function createAllTriggers() {
  Logger.log('=== createAllTriggers ===');

  // List of all trigger handler function names this project owns. Any
  // existing trigger pointing at one of these is deleted before new ones
  // are created — keeps re-running this function idempotent.
  var ownedHandlers = {
    runDailyPipeline: true,
    generateWeeklyNarrative: true,
    triggerAgentPipelineHealthIfNeeded: true,
    triggerAgentDailyCheckIfNeeded: true,
    triggerAgentFatigueMonitorIfNeeded: true,
    triggerAgentCreativeIntelligenceIfNeeded: true,
    triggerAgentPortfolioScalingIfNeeded: true
  };

  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (ownedHandlers[t.getHandlerFunction()]) {
      ScriptApp.deleteTrigger(t);
    }
  });

  ScriptApp.newTrigger('runDailyPipeline')
    .timeBased().everyDays(1).atHour(7).create();

  ScriptApp.newTrigger('generateWeeklyNarrative')
    .timeBased().onWeekDay(ScriptApp.WeekDay.MONDAY).atHour(8).create();

  // Agent workflow fallback triggers. GitHub Actions cron runs each
  // skill earlier in the morning; these fire ~noon-2 PM ET as a
  // safety net and dispatch via API only if no recent successful run
  // exists. See AGENT WORKFLOW DISPATCH section below.
  ScriptApp.newTrigger('triggerAgentPipelineHealthIfNeeded')
    .timeBased().everyDays(1).atHour(12).create();
  ScriptApp.newTrigger('triggerAgentDailyCheckIfNeeded')
    .timeBased().everyDays(1).atHour(12).create();
  ScriptApp.newTrigger('triggerAgentFatigueMonitorIfNeeded')
    .timeBased().everyDays(1).atHour(13).create();
  ScriptApp.newTrigger('triggerAgentCreativeIntelligenceIfNeeded')
    .timeBased().everyDays(1).atHour(13).create();
  ScriptApp.newTrigger('triggerAgentPortfolioScalingIfNeeded')
    .timeBased().everyDays(1).atHour(13).create();

  var triggers = ScriptApp.getProjectTriggers();
  Logger.log('Triggers active: ' + triggers.length);
  triggers.forEach(function (t) {
    Logger.log('  ' + t.getHandlerFunction() + ' — ' + t.getEventType());
  });
}


// ============================================================
// AGENT WORKFLOW DISPATCH (fallback for GitHub Actions cron)
// ============================================================
//
// GitHub Actions cron is best-effort: runs can be delayed up to 30+
// minutes under load, occasionally skipped during GitHub incidents,
// and silently disabled after 60 days of zero pushes to the repo.
//
// These functions use Apps Script's reliable time-based triggers as a
// fallback. Each `*IfNeeded` function checks whether the corresponding
// workflow already ran successfully (or is in-progress) within a recent
// window via the GitHub API; if so, it skips. If GitHub's cron missed
// the morning run, the Apps Script trigger fires at noon-2 PM ET and
// dispatches via the workflow_dispatch API.
//
// Setup:
//   1. Run testAgentDispatch() from the Apps Script editor to verify
//      the GITHUB_PAT in Script Properties has the right scopes
//      (classic PAT with `repo` works; fine-grained needs Actions:
//      Read AND Write).
//   2. Run createAllTriggers() to install the three time-based triggers.
//   3. Verify in Project Triggers that the three triggerAgent*IfNeeded
//      handlers are scheduled.

const AGENT_WORKFLOW_REPO = 'tylerhoneycomb/marketing-claude-honeycomb';


// Calls the GitHub workflow_dispatch endpoint. Returns true on 204 No
// Content (the success response). All errors are logged + return false.
function triggerAgentWorkflow_(filename) {
  var pat = PROPS.getProperty('GITHUB_PAT');
  if (!pat) {
    Logger.log('triggerAgentWorkflow_: GITHUB_PAT not set; skipping ' + filename);
    return false;
  }
  var url = 'https://api.github.com/repos/' + AGENT_WORKFLOW_REPO +
            '/actions/workflows/' + filename + '/dispatches';
  var resp = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      'Authorization': 'Bearer ' + pat,
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  if (code === 204) {
    Logger.log('triggerAgentWorkflow_: dispatched ' + filename);
    return true;
  }
  Logger.log('triggerAgentWorkflow_: FAIL ' + filename + ' HTTP ' + code +
             ' — ' + resp.getContentText().substring(0, 300));
  return false;
}


// Returns true if the workflow has a recent run within `hours` that
// either succeeded or is currently running/queued. Failed runs do NOT
// count — those should be retried by the fallback. Returns false on
// any API error so we err on the side of dispatching (the workflow's
// own concurrency group prevents true duplicates if both fire).
function workflowRanWithinHours_(filename, hours) {
  var pat = PROPS.getProperty('GITHUB_PAT');
  if (!pat) return false;
  var url = 'https://api.github.com/repos/' + AGENT_WORKFLOW_REPO +
            '/actions/workflows/' + filename + '/runs?per_page=10';
  var resp = UrlFetchApp.fetch(url, {
    headers: {
      'Authorization': 'Bearer ' + pat,
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    muteHttpExceptions: true
  });
  if (resp.getResponseCode() !== 200) {
    Logger.log('workflowRanWithinHours_: list runs HTTP ' +
               resp.getResponseCode() + ' for ' + filename +
               ' — assuming no recent run');
    return false;
  }
  var json = JSON.parse(resp.getContentText());
  var runs = json.workflow_runs || [];
  var cutoffMs = Date.now() - (hours * 3600 * 1000);
  for (var i = 0; i < runs.length; i++) {
    var createdMs = new Date(runs[i].created_at).getTime();
    if (createdMs < cutoffMs) continue;
    var status = runs[i].status;
    var conclusion = runs[i].conclusion;
    if (status === 'in_progress' || status === 'queued' ||
        status === 'pending' || conclusion === 'success') {
      return true;
    }
  }
  return false;
}


// Daily fallback for agent-pipeline-health.yml. GitHub cron is set for
// 9 AM ET; this trigger fires noon-1 PM ET. 18-hour lookback window
// catches today's morning run if it succeeded.
function triggerAgentPipelineHealthIfNeeded() {
  Logger.log('=== triggerAgentPipelineHealthIfNeeded ===');
  if (workflowRanWithinHours_('agent-pipeline-health.yml', 18)) {
    Logger.log('Recent successful or in-progress run exists — skipping.');
    return;
  }
  triggerAgentWorkflow_('agent-pipeline-health.yml');
}


// Daily fallback for agent-daily-check.yml. GitHub cron 8:30 AM ET;
// fallback fires noon-1 PM ET. 18-hour window.
function triggerAgentDailyCheckIfNeeded() {
  Logger.log('=== triggerAgentDailyCheckIfNeeded ===');
  if (workflowRanWithinHours_('agent-daily-check.yml', 18)) {
    Logger.log('Recent successful or in-progress run exists — skipping.');
    return;
  }
  triggerAgentWorkflow_('agent-daily-check.yml');
}


// Twice-weekly fallback for agent-fatigue-monitor.yml. GitHub cron is
// Mon + Thu 9:30 AM ET. The Apps Script trigger fires daily at hour 13
// (1-2 PM ET), but we early-out on non-Mon/Thu days so the fallback
// only kicks in on the days the GitHub cron is supposed to run.
// 12-hour window = "did it run today?"
function triggerAgentFatigueMonitorIfNeeded() {
  Logger.log('=== triggerAgentFatigueMonitorIfNeeded ===');
  // getDay(): 0=Sun, 1=Mon, ..., 4=Thu. Use script timezone for the
  // day boundary so the check reflects ET, not the runner's UTC.
  var dow = parseInt(Utilities.formatDate(
    new Date(), Session.getScriptTimeZone(), 'u'), 10);
  // Note: 'u' returns 1=Mon..7=Sun (ISO). Convert: 1=Mon, 4=Thu.
  if (dow !== 1 && dow !== 4) {
    Logger.log('Not Mon or Thu (ISO dow=' + dow + ') — skipping.');
    return;
  }
  if (workflowRanWithinHours_('agent-fatigue-monitor.yml', 12)) {
    Logger.log('Recent successful or in-progress run exists — skipping.');
    return;
  }
  triggerAgentWorkflow_('agent-fatigue-monitor.yml');
}


// Weekly Monday fallback for agent-creative-intelligence.yml. The
// GitHub cron is Monday 14:00 UTC (10 AM EDT / 9 AM EST). This
// Apps Script trigger fires daily and early-outs unless it's Monday,
// then dispatches via workflow_dispatch only if no recent successful
// run exists.
function triggerAgentCreativeIntelligenceIfNeeded() {
  Logger.log('=== triggerAgentCreativeIntelligenceIfNeeded ===');
  var dow = parseInt(Utilities.formatDate(
    new Date(), Session.getScriptTimeZone(), 'u'), 10);
  if (dow !== 1) {
    Logger.log('Not Monday (ISO dow=' + dow + ') — skipping.');
    return;
  }
  if (workflowRanWithinHours_('agent-creative-intelligence.yml', 12)) {
    Logger.log('Recent successful or in-progress run exists — skipping.');
    return;
  }
  triggerAgentWorkflow_('agent-creative-intelligence.yml');
}


// Weekly Tuesday fallback for agent-portfolio-scaling.yml. GitHub cron
// is Tue 13:30 UTC (9:30 AM EDT / 8:30 AM EST). The Apps Script trigger
// fires daily and early-outs unless it's Tuesday. 12-hour window =
// "did it run today?" — short window because Tuesday is the only day
// the workflow runs and we want to fire the fallback within ~3 hours
// of the GitHub cron's intended slot.
function triggerAgentPortfolioScalingIfNeeded() {
  Logger.log('=== triggerAgentPortfolioScalingIfNeeded ===');
  var dow = parseInt(Utilities.formatDate(
    new Date(), Session.getScriptTimeZone(), 'u'), 10);
  if (dow !== 2) {
    Logger.log('Not Tuesday (ISO dow=' + dow + ') — skipping.');
    return;
  }
  if (workflowRanWithinHours_('agent-portfolio-scaling.yml', 12)) {
    Logger.log('Recent successful or in-progress run exists — skipping.');
    return;
  }
  triggerAgentWorkflow_('agent-portfolio-scaling.yml');
}


// Diagnostic: verify the GITHUB_PAT can list workflows. Run from the
// Apps Script editor before calling createAllTriggers() so you know
// the token has the right scope (classic with `repo`, or fine-grained
// with Actions: Read + Write).
function testAgentDispatch() {
  Logger.log('=== testAgentDispatch ===');
  var pat = PROPS.getProperty('GITHUB_PAT');
  if (!pat) {
    Logger.log('FAIL: GITHUB_PAT not set in Script Properties.');
    return;
  }
  var url = 'https://api.github.com/repos/' + AGENT_WORKFLOW_REPO + '/actions/workflows';
  var resp = UrlFetchApp.fetch(url, {
    headers: {
      'Authorization': 'Bearer ' + pat,
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  if (code !== 200) {
    Logger.log('FAIL: HTTP ' + code + ' — ' +
               resp.getContentText().substring(0, 300));
    Logger.log('Likely cause: PAT lacks Actions scope. Classic PAT needs `repo`; ' +
               'fine-grained needs Actions: Read + Write on this repo.');
    return;
  }
  var json = JSON.parse(resp.getContentText());
  Logger.log('PASS: PAT can list workflows. ' + json.total_count + ' found:');
  (json.workflows || []).forEach(function (w) {
    Logger.log('  ' + w.path + ' (state: ' + w.state + ')');
  });
  Logger.log('Run createAllTriggers() to install the three fallback triggers.');
}


// ============================================================
// DIAGNOSTICS
// ============================================================

function testMetaConnection() {
  Logger.log('=== testMetaConnection ===');
  if (!ACCESS_TOKEN) {
    Logger.log('FAIL: META_ACCESS_TOKEN is not set in Script Properties.');
    return;
  }
  var url = 'https://graph.facebook.com/' + API_VERSION + '/' + AD_ACCOUNT_ID +
    '?fields=name,account_status&access_token=' + ACCESS_TOKEN;
  var response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  var code = response.getResponseCode();
  if (code === 200) {
    var json = JSON.parse(response.getContentText());
    Logger.log('PASS: Account = "' + json.name + '", status = ' + json.account_status);
  } else {
    Logger.log('FAIL (HTTP ' + code + '): ' + response.getContentText());
  }
}

function testHubspotConnection() {
  Logger.log('=== testHubspotConnection ===');
  if (!HUBSPOT_API_KEY) {
    Logger.log('FAIL: HUBSPOT_API_KEY is not set in Script Properties.');
    return;
  }
  var url = 'https://api.hubapi.com/crm/v3/objects/contacts?limit=1' +
    '&properties=prequal_decision,prequal_utm_campaign,prequal_submitted';
  var response = UrlFetchApp.fetch(url, {
    headers: { 'Authorization': 'Bearer ' + HUBSPOT_API_KEY },
    muteHttpExceptions: true
  });
  var code = response.getResponseCode();
  if (code === 200) {
    var json = JSON.parse(response.getContentText());
    var sample = json.results && json.results[0] && json.results[0].properties;
    Logger.log('PASS: HubSpot connected. Sample contact properties: ' + JSON.stringify(sample || {}));
  } else {
    Logger.log('FAIL (HTTP ' + code + '): ' + response.getContentText());
  }
}

function testSlackWebhook() {
  Logger.log('=== testSlackWebhook ===');
  if (!SLACK_WEBHOOK_URL) {
    Logger.log('FAIL: SLACK_WEBHOOK_URL is not set in Script Properties.');
    return;
  }
  var response = UrlFetchApp.fetch(SLACK_WEBHOOK_URL, {
    method: 'POST',
    contentType: 'application/json',
    payload: JSON.stringify({ text: 'Honeycomb Ads script: connection test successful.' }),
    muteHttpExceptions: true
  });
  var code = response.getResponseCode();
  Logger.log(code === 200
    ? 'PASS: Message sent — check your Slack channel.'
    : 'FAIL (HTTP ' + code + '): ' + response.getContentText());
}

function testAnthropicConnection() {
  Logger.log('=== testAnthropicConnection ===');
  if (!ANTHROPIC_API_KEY) {
    Logger.log('FAIL: ANTHROPIC_API_KEY is not set in Script Properties.');
    return;
  }
  var response = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
      'Content-Type': 'application/json'
    },
    payload: JSON.stringify({
      model: ANTHROPIC_MODEL,
      max_tokens: 20,
      messages: [{ role: 'user', content: 'Reply with only the word: connected' }]
    }),
    muteHttpExceptions: true
  });
  var code = response.getResponseCode();
  if (code === 200) {
    var json = JSON.parse(response.getContentText());
    Logger.log('PASS: ' + (json.content && json.content[0] && json.content[0].text));
  } else {
    Logger.log('FAIL (HTTP ' + code + '): ' + response.getContentText());
  }
}

function runFullDiagnostic() {
  Logger.log('==============================');
  Logger.log('     FULL DIAGNOSTIC          ');
  Logger.log('==============================');
  testMetaConnection();
  Utilities.sleep(500);
  testHubspotConnection();
  Utilities.sleep(500);
  testSlackWebhook();
  Utilities.sleep(500);
  testAnthropicConnection();
  Utilities.sleep(500);
  var triggers = ScriptApp.getProjectTriggers();
  Logger.log('Active triggers: ' + triggers.length);
  triggers.forEach(function (t) { Logger.log('  ' + t.getHandlerFunction()); });
  Logger.log('==============================');
  Logger.log('     DIAGNOSTIC COMPLETE      ');
  Logger.log('==============================');
}


// ============================================================
// ============================================================
// BUDGET AUTOMATION SYSTEM
// ============================================================
// ============================================================


// ============================================================
// STEP 1 — ANALYSIS
// ============================================================

function runBudgetAnalysis() {
  Logger.log('=== runBudgetAnalysis ===');
  // Record timestamp so the dashboard can show "last run".
  PROPS.setProperty('BUDGET_LAST_RUN_AT', new Date().toISOString());
  validateTokens_();

  var priorTokenExists = !!PROPS.getProperty('BUDGET_PENDING_TOKEN');
  if (priorTokenExists) {
    Logger.log('WARNING: Overwriting existing pending token.');
  }

  var currentBudgets = getCurrentMetaBudgets_();
  if (Object.keys(currentBudgets).length === 0) {
    Logger.log('ERROR: Could not fetch Meta campaign budgets. Aborting.');
    return;
  }

  var signals = computeBudgetSignals_();
  var icpPace = computeWeeklyICPPace_();

  var recommendations = computeRecommendations_(currentBudgets, signals);

  if (recommendations.length === 0) {
    postToSlack_('*Honeycomb Budget Check — ' +
      Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'EEE MMM d') +
      '*\nAll campaigns within normal range. No changes recommended this cycle.');
    Logger.log('No changes recommended.');
    return;
  }

  var token = writeToQueue_(recommendations);
  postBudgetProposalToSlack_(recommendations, token, icpPace, currentBudgets, priorTokenExists);
  Logger.log('=== runBudgetAnalysis complete. Token: ' + token + ' ===');
}


// ============================================================
// SIGNAL COMPUTATION — HYBRID ATTRIBUTION (v3)
// Rolling 14-day window from rolling_data. Uses IC conversions
// as the attribution foundation + share of unattributed pool.
// Same model as buildWeeklyRollup (v3).
// ============================================================

function computeBudgetSignals_() {
  Logger.log('--- computeBudgetSignals_ ---');
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var metaSheet = ss.getSheetByName(META_SHEET);
  var hsSheet = ss.getSheetByName(HS_SHEET);
  if (!metaSheet) { Logger.log('ERROR: rolling_data not found.'); return {}; }

  var tz = Session.getScriptTimeZone();
  var today = new Date();
  var cutoff = new Date(today);
  cutoff.setDate(cutoff.getDate() - ROLLING_DAYS);
  var cutoffStr = Utilities.formatDate(cutoff, tz, 'yyyy-MM-dd');

  var midpoint = new Date(today);
  midpoint.setDate(midpoint.getDate() - Math.floor(ROLLING_DAYS / 2));
  var midpointStr = Utilities.formatDate(midpoint, tz, 'yyyy-MM-dd');

  var metaData = metaSheet.getDataRange().getValues();

  // Daily ICP totals (ground truth)
  var totalIcpsByDate = {};
  if (hsSheet) {
    var hsData = hsSheet.getDataRange().getValues();
    for (var hi = 1; hi < hsData.length; hi++) {
      var sub = dateToYMD_(hsData[hi][1]);
      if (sub) totalIcpsByDate[sub] = (totalIcpsByDate[sub] || 0) + 1;
    }
  }

  // Daily Meta conversion totals + lifetime conversions per campaign
  var totalConvByDate = {};
  var lifetimeConvByCampaign = {};
  for (var ri0 = 1; ri0 < metaData.length; ri0++) {
    var dStr0 = dateToYMD_(metaData[ri0][0]);
    if (!dStr0) continue;
    var cid0 = String(metaData[ri0][4]).trim();
    var conv0 = parseInt(metaData[ri0][9]) || 0;
    totalConvByDate[dStr0] = (totalConvByDate[dStr0] || 0) + conv0;
    lifetimeConvByCampaign[cid0] = (lifetimeConvByCampaign[cid0] || 0) + conv0;
  }

  // ── IC-based attribution: daily IC conversion totals ────────
  var totalICConvByDate = {};
  for (var icPass = 1; icPass < metaData.length; icPass++) {
    var icDate = dateToYMD_(metaData[icPass][0]);
    if (!icDate) continue;
    var icConvBudget = parseInt(metaData[icPass][12]) || 0;
    totalICConvByDate[icDate] = (totalICConvByDate[icDate] || 0) + icConvBudget;
  }
  // ───────────────────────────────────────────────────────────

  // Aggregate within rolling window only
  var signals = {};
  for (var ri = 1; ri < metaData.length; ri++) {
    var dStr = dateToYMD_(metaData[ri][0]);
    if (!dStr || dStr <= cutoffStr) continue;

    var cid = String(metaData[ri][4]).trim();
    var cname = String(metaData[ri][3]).trim();
    var spend = parseFloat(metaData[ri][7]) || 0;
    var convs = parseInt(metaData[ri][9]) || 0;
    var freq = parseFloat(metaData[ri][10]) || 0;

    var totalConvDay = totalConvByDate[dStr] || 0;
    var totalIcpDay = totalIcpsByDate[dStr] || 0;

    if (!signals[cid]) {
      signals[cid] = {
        campaignId: cid, name: cname,
        spend: 0, estimatedIcps: 0,
        recentIcps: 0, priorIcps: 0,
        freqImpressions: 0, freqReach: 0,
        lifetimeConversions: lifetimeConvByCampaign[cid] || 0
      };
    }
    signals[cid].spend += spend;

    // ── Hybrid attribution v3 (same logic as buildWeeklyRollup) ──
    var sigDailyICConv = parseInt(metaData[ri][12]) || 0;
    var sigTotalICConvDay = totalICConvByDate[dStr] || 0;
    var sigUnattributed = Math.max(0, totalIcpDay - sigTotalICConvDay);
    var sigShareUnattrib = totalConvDay > 0
      ? convs / totalConvDay * sigUnattributed : 0;
    var sigHybridCredit = sigDailyICConv + sigShareUnattrib;

    signals[cid].estimatedIcps += sigHybridCredit;
    if (dStr > midpointStr) {
      signals[cid].recentIcps += sigHybridCredit;
    } else {
      signals[cid].priorIcps += sigHybridCredit;
    }
    // ──────────────────────────────────────────────────────────

    var sigImpr = parseInt(metaData[ri][5]) || 0;
    if (freq > 0 && sigImpr > 0) {
      signals[cid].freqImpressions += sigImpr;
      signals[cid].freqReach += sigImpr / freq;
    }
  }

  Object.values(signals).forEach(function (s) {
    s.cpicp = s.estimatedIcps > 0 ? s.spend / s.estimatedIcps : null;
    s.avgFreq = s.freqReach > 0 ? Math.round((s.freqImpressions / s.freqReach) * 100) / 100 : 0;
    s.icpTrend = s.priorIcps > 0 ? Math.round((s.recentIcps - s.priorIcps) * 10) / 10 : null;
  });

  Logger.log('Signals computed for ' + Object.keys(signals).length + ' campaigns.');
  return signals;
}


// ============================================================
// ICP PACE CHECK
// ============================================================

function computeWeeklyICPPace_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var hsSheet = ss.getSheetByName(HS_SHEET);
  if (!hsSheet) return { rolling7dICPs: 0, label: '0 ICPs in last 7 days' };

  var tz = Session.getScriptTimeZone();
  var today = new Date();
  var cutoff = new Date(today);
  cutoff.setDate(cutoff.getDate() - 7);
  var cutoffStr = Utilities.formatDate(cutoff, tz, 'yyyy-MM-dd');

  var hsData = hsSheet.getDataRange().getValues();
  var rolling7dICPs = 0;
  for (var hi = 1; hi < hsData.length; hi++) {
    var sub = dateToYMD_(hsData[hi][1]);
    if (sub && sub > cutoffStr) rolling7dICPs++;
  }

  var label = rolling7dICPs + ' ICPs in last 7 days';
  Logger.log('ICP pace: ' + label);
  return { rolling7dICPs: rolling7dICPs, label: label };
}


// ============================================================
// PORTFOLIO SCALING — profile loader + headroom helpers (Session 2)
// ============================================================
//
// Used by runBudgetAnalysis to (a) tag proposals with the per-campaign
// vertical classification, (b) enforce the 12% weekly cap on cumulative
// budget movement summed across all sources, and (c) skip campaigns the
// strategic reallocation just touched (Wed-Mon lockout window).
//
// All three behaviors degrade gracefully when scaling_profiles.json is
// missing or stale: tagging is omitted (no scaling_classification field),
// the 12% cap still runs (it's a hard rail, not a feature gated on the
// data file), and lockout is empty if the property isn't set.

function loadScalingProfiles_() {
  // First check the Script Property cache to avoid hammering GitHub on
  // every run. Cache is overwritten with each successful fetch.
  var cached = PROPS.getProperty('SCALING_PROFILES_CACHE');
  var cachedAt = PROPS.getProperty('SCALING_PROFILES_CACHED_AT');
  var maxAgeMs = SCALING_PROFILES_MAX_AGE_DAYS * 86400 * 1000;

  if (cached && cachedAt) {
    var age = new Date().getTime() - new Date(cachedAt).getTime();
    if (age < 3600 * 1000) {  // <1hr-old cache: just use it.
      try { return JSON.parse(cached); }
      catch (parseErr) {
        Logger.log('SCALING_PROFILES_CACHE corrupt, refetching: ' + parseErr.message);
      }
    }
  }

  try {
    var resp = UrlFetchApp.fetch(SCALING_PROFILES_URL, {
      muteHttpExceptions: true,
      followRedirects: true
    });
    if (resp.getResponseCode() !== 200) {
      Logger.log('loadScalingProfiles_: HTTP ' + resp.getResponseCode() +
        ' from ' + SCALING_PROFILES_URL);
      return cached ? safeJsonParse_(cached) : null;
    }
    var body = resp.getContentText();
    var parsed = JSON.parse(body);
    if (parsed && parsed.computed_at) {
      var ageDays = (new Date().getTime() - new Date(parsed.computed_at).getTime()) / 86400000;
      if (ageDays > SCALING_PROFILES_MAX_AGE_DAYS) {
        Logger.log('loadScalingProfiles_: profiles stale (' +
          ageDays.toFixed(1) + ' days). Skipping scaling overlay.');
        return null;
      }
    }
    PROPS.setProperty('SCALING_PROFILES_CACHE', body);
    PROPS.setProperty('SCALING_PROFILES_CACHED_AT', new Date().toISOString());
    return parsed;
  } catch (e) {
    Logger.log('loadScalingProfiles_ failed: ' + e.message);
    return cached ? safeJsonParse_(cached) : null;
  }
}

function safeJsonParse_(s) {
  try { return JSON.parse(s); } catch (e) { return null; }
}

function getScalingLockoutSet_() {
  // Returns a campaign_id → true map of campaigns currently subject to
  // the strategic-reallocation lockout. Empty map when lockout has
  // expired or was never set. The optimizer will skip these campaigns.
  var until = PROPS.getProperty('SCALING_LOCKOUT_UNTIL');
  if (!until) return {};
  if (new Date() >= new Date(until)) {
    Logger.log('Scaling lockout expired (' + until + '). Optimizer free to act.');
    return {};
  }
  var idsCsv = PROPS.getProperty('SCALING_AFFECTED_CAMPAIGN_IDS') || '';
  var set = {};
  idsCsv.split(',').forEach(function (id) {
    var trimmed = String(id).trim();
    if (trimmed) set[trimmed] = true;
  });
  Logger.log('Scaling lockout active until ' + until + ' (' +
    Object.keys(set).length + ' campaigns).');
  return set;
}

function previousTuesdayUTC_() {
  // Most recent Tuesday strictly before now (or 7 days ago if today is
  // Tuesday). Mirrors the Python `previous_tuesday` in
  // compute_scaling_profiles.py so the headroom window aligns.
  var now = new Date();
  var day = now.getUTCDay();   // 0=Sun, 1=Mon, 2=Tue
  var daysSince = (day - 2 + 7) % 7;
  if (daysSince === 0) daysSince = 7;
  var prev = new Date(now.getTime() - daysSince * 86400000);
  return new Date(Date.UTC(prev.getUTCFullYear(),
                           prev.getUTCMonth(),
                           prev.getUTCDate()));
}

function getCampaignWeeklyConsumed_(campaignId, sinceDate) {
  // Sum |change_pct| (as a fraction) across all `executed` budget_queue
  // rows for this campaign with analysis_date >= sinceDate. Counts
  // optimizer movements + knockdown + any prior strategic — every
  // source contributes against the shared 12% cap. Mirrors the Python
  // compute_per_campaign_headroom in compute_scaling_profiles.py.
  // Reads BUDGET_SHEET directly (no /exec hop — same Apps Script project).
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var qSheet = ss.getSheetByName(BUDGET_SHEET);
  if (!qSheet || qSheet.getLastRow() < 2) return 0;

  var sinceStr = Utilities.formatDate(
    sinceDate, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  var data = qSheet.getDataRange().getValues();
  var sum = 0;
  for (var i = 1; i < data.length; i++) {
    var r = data[i];
    if (String(r[4]).trim() !== String(campaignId)) continue;
    if (String(r[11]).toLowerCase() !== 'executed') continue;
    var analysisDate = r[2];
    var dStr = (analysisDate instanceof Date)
      ? Utilities.formatDate(analysisDate, Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : String(analysisDate).substring(0, 10);
    if (dStr < sinceStr) continue;
    // sheet stores change_pct as a percent value (e.g. 2.0 = 2%) per
    // writeToQueue_ at Code.js:3185.
    sum += Math.abs(Number(r[9]) || 0) / 100;
  }
  return sum;
}

function getCampaignVertical_(profiles, campaignId) {
  if (!profiles || !profiles.campaigns) return null;
  var c = profiles.campaigns[String(campaignId)];
  return c ? c.vertical : null;
}

function getVerticalClassification_(profiles, vertical) {
  if (!profiles || !vertical || !profiles.verticals) return null;
  var v = profiles.verticals[vertical];
  if (!v) return null;
  return {
    classification: v.classification,
    confidence: v.confidence,
    new_audience_needed: !!v.new_audience_needed,
    cpicp: v.cpicp,
    elasticity_r: v.elasticity_r,
    spend_share_pct: v.spend_share_pct
  };
}


// ============================================================
// META BUDGET FETCH
// ============================================================

function getCurrentMetaBudgets_() {
  Logger.log('--- getCurrentMetaBudgets_ ---');
  var url = 'https://graph.facebook.com/' + API_VERSION + '/' + AD_ACCOUNT_ID +
    '/campaigns?fields=id,name,daily_budget,status&limit=100&access_token=' + ACCESS_TOKEN;

  try {
    var budgets = {};
    var nextUrl = url;

    while (nextUrl) {
      var resp = fetchWithRetry_(nextUrl, { muteHttpExceptions: true });
      var json = JSON.parse(resp.getContentText());

      if (resp.getResponseCode() !== 200 || json.error) {
        Logger.log('Meta budget fetch error: ' +
          (json.error ? json.error.message : resp.getContentText()));
        return {};
      }

      (json.data || []).forEach(function (c) {
        if (c.status === 'ACTIVE' && c.daily_budget) {
          budgets[c.id] = {
            name: c.name,
            dailyBudgetCents: parseInt(c.daily_budget, 10),
            status: c.status
          };
        }
      });

      nextUrl = (json.paging && json.paging.next) ? json.paging.next : null;
    }

    Logger.log('Fetched budgets for ' + Object.keys(budgets).length + ' active CBO campaigns.');
    return budgets;

  } catch (e) {
    Logger.log('getCurrentMetaBudgets_ exception: ' + e.message);
    return {};
  }
}


// ============================================================
// RECOMMENDATION ENGINE
// ============================================================

function computeRecommendations_(currentBudgets, signals) {
  Logger.log('--- computeRecommendations_ ---');

  // ── Session 2: portfolio-scaling overlay loads ────────────────
  // Profiles, lockout, headroom-window date. All three degrade
  // gracefully — missing profiles means no tags but cap + lockout
  // still run; missing lockout means open optimizer; cap is always on.
  var scalingProfiles = loadScalingProfiles_();
  var lockoutSet = getScalingLockoutSet_();
  var prevTue = previousTuesdayUTC_();
  var lockedOutCount = 0;
  // ──────────────────────────────────────────────────────────────

  var eligible = [];
  Object.keys(currentBudgets).forEach(function (cid) {
    var budget = currentBudgets[cid];
    var signal = signals[cid];

    if (!signal) {
      Logger.log('No rolling-window signal for ' + budget.name + ' — skipping.');
      return;
    }
    if (signal.lifetimeConversions < LIFETIME_MIN_CONVERSIONS) {
      Logger.log(budget.name + ': ' + signal.lifetimeConversions +
        ' lifetime conversions (min ' + LIFETIME_MIN_CONVERSIONS + ') — ineligible.');
      return;
    }
    // Strategic-reallocation lockout (Session 2): Wed-Mon, this campaign
    // was just touched by the strategic reallocation and needs a
    // stabilization window before the optimizer acts on it again.
    if (lockoutSet[cid]) {
      Logger.log(budget.name + ': locked out by strategic reallocation ' +
        '(SCALING_LOCKOUT_UNTIL=' + PROPS.getProperty('SCALING_LOCKOUT_UNTIL') +
        ') — skipping.');
      lockedOutCount++;
      return;
    }

    eligible.push({
      campaignId: cid,
      name: budget.name,
      currentDailyBudgetCents: budget.dailyBudgetCents,
      cpicp: signal.cpicp,
      avgFreq: signal.avgFreq,
      estimatedIcps: signal.estimatedIcps,
      icpTrend: signal.icpTrend,
      lifetimeConversions: signal.lifetimeConversions,
      spend7d: signal.spend
    });
  });

  Logger.log('Eligible campaigns: ' + eligible.length +
    (lockedOutCount > 0 ? ' (' + lockedOutCount + ' locked out)' : ''));
  if (eligible.length === 0) return [];

  eligible.forEach(function (c) {
    c.direction = null;
    c.reasons = [];

    if (c.avgFreq >= FREQ_HIGH_THRESHOLD) {
      c.direction = -1;
      c.reasons.push('Freq ' + c.avgFreq.toFixed(1) + ' ≥ ' + FREQ_HIGH_THRESHOLD +
        ' — audience saturation (overrides rank)');
      return;
    }

    if (c.cpicp === null) {
      c.direction = -1;
      c.reasons.push('0 est. ICPs in 7-day window ($' + c.spend7d.toFixed(0) + ' spend)');
      return;
    }
  });

  var rankable = eligible.filter(function (c) {
    return c.direction === null && c.cpicp !== null;
  });

  var n = rankable.length;
  if (n === 0) {
    eligible.forEach(function (c) {
      if (c.direction === null) { c.direction = 0; c.reasons.push('No rankable signal — hold'); }
    });
  } else {

    var medianTrendRank = Math.ceil(n / 2);

    var byCPICP = rankable.slice().sort(function (a, b) { return a.cpicp - b.cpicp; });
    byCPICP.forEach(function (c, idx) { c._cpicpRank = idx + 1; });

    var byTrend = rankable.slice().sort(function (a, b) {
      var ta = a.icpTrend !== null ? a.icpTrend : 0;
      var tb = b.icpTrend !== null ? b.icpTrend : 0;
      return tb - ta;
    });
    byTrend.forEach(function (c, idx) {
      c._trendRank = c.icpTrend !== null ? idx + 1 : medianTrendRank;
    });

    rankable.forEach(function (c) {
      c._compositeScore = (0.70 * c._cpicpRank) + (0.30 * c._trendRank);
    });

    rankable.sort(function (a, b) { return a._compositeScore - b._compositeScore; });

    var quartileCut = Math.max(1, Math.floor(n / 4));

    rankable.forEach(function (c, idx) {
      var trendLabel = c.icpTrend !== null
        ? (c.icpTrend >= 0 ? '+' + c.icpTrend.toFixed(1) : c.icpTrend.toFixed(1)) + ' ICP trend'
        : 'no trend data';

      if (idx < quartileCut) {
        if (c.avgFreq >= FREQ_WATCH_THRESHOLD) {
          c.direction = 0;
          c.reasons.push('Composite rank ' + (idx + 1) + '/' + n +
            ': CPICP $' + c.cpicp.toFixed(0) + ' | ' + trendLabel +
            ' | freq ' + c.avgFreq.toFixed(1) + ' — hold, monitor audience');
        } else {
          c.direction = 1;
          c.reasons.push('Composite rank ' + (idx + 1) + '/' + n +
            ': CPICP $' + c.cpicp.toFixed(0) + ' | ' + trendLabel);
        }
      } else if (idx >= n - quartileCut) {
        c.direction = -1;
        c.reasons.push('Composite rank ' + (idx + 1) + '/' + n +
          ': CPICP $' + c.cpicp.toFixed(0) + ' | ' + trendLabel);
        if (c.avgFreq >= FREQ_WATCH_THRESHOLD) {
          c.reasons.push('Freq ' + c.avgFreq.toFixed(1) + ' watch');
        }
      } else {
        c.direction = 0;
        c.reasons.push('Composite rank ' + (idx + 1) + '/' + n +
          ': CPICP $' + c.cpicp.toFixed(0) + ' | ' + trendLabel + ' — hold');
      }
    });
  }

  eligible.forEach(function (c) {
    if (c.direction === null) { c.direction = 0; c.reasons.push('No signal — hold'); }
  });

  var currentTotal = Object.values(currentBudgets)
    .reduce(function (s, b) { return s + b.dailyBudgetCents; }, 0);
  // Read target from Script Properties (dashboard override)
  // with fallback to the hardcoded constant.
  var effectiveTarget = getTargetWeeklySpend_();
  var effectiveTolerance = getWeeklySpendTolerance_();
  var targetDaily = Math.round(effectiveTarget * 100 / 7);
  var toleranceDaily = Math.round(effectiveTolerance * 100 / 7);
  Logger.log('Using target $' + effectiveTarget + '/week ± $' + effectiveTolerance +
    (PROPS.getProperty('DASHBOARD_TARGET_WEEKLY_SPEND') ? ' (dashboard override)' : ' (default)'));

  var eligibleIds = eligible.reduce(function (m, c) { m[c.campaignId] = true; return m; }, {});
  var ineligibleTotal = Object.keys(currentBudgets).reduce(function (s, cid) {
    return eligibleIds[cid] ? s : s + currentBudgets[cid].dailyBudgetCents;
  }, 0);
  Logger.log('Ineligible campaign budget: $' + (ineligibleTotal / 100).toFixed(0) + '/day ($' +
    (ineligibleTotal * 7 / 100).toFixed(0) + '/week) — excluded from correction.');

  var currentEligibleTotal = eligible.reduce(function (s, c) {
    return s + c.currentDailyBudgetCents;
  }, 0);
  var trueCurrentTotal = currentEligibleTotal + ineligibleTotal;
  var portfolioExcess = trueCurrentTotal - targetDaily;

  var knockdownApplied = false;
  if (portfolioExcess > toleranceDaily) {
    Logger.log('Portfolio over by $' + (portfolioExcess * 7 / 100).toFixed(0) +
      '/week. Applying 1% knockdown to all eligible campaigns before performance adjustments.');
    eligible.forEach(function (c) {
      var cut = Math.round(c.currentDailyBudgetCents * 0.01);
      c.knockdownBudgetCents = Math.max(c.currentDailyBudgetCents - cut, CAMPAIGN_DAILY_MIN_CENTS);
    });
    knockdownApplied = true;
  } else {
    eligible.forEach(function (c) {
      c.knockdownBudgetCents = c.currentDailyBudgetCents;
    });
    Logger.log('Pool check: $' + (trueCurrentTotal * 7 / 100).toFixed(0) +
      '/week — within tolerance, no knockdown.');
  }

  var toReduce = eligible.filter(function (c) { return c.direction === -1; });
  var toIncrease = eligible.filter(function (c) { return c.direction === 1; });
  var toHold = eligible.filter(function (c) { return c.direction === 0; });

  var totalFreedCents = 0;

  toReduce.forEach(function (c) {
    var cut = Math.round(c.knockdownBudgetCents * MAX_CHANGE_PCT);
    var proposed = Math.max(c.knockdownBudgetCents - cut, CAMPAIGN_DAILY_MIN_CENTS);
    c.proposedDailyBudgetCents = proposed;
    c.changeCents = proposed - c.currentDailyBudgetCents;
    totalFreedCents += Math.abs(c.knockdownBudgetCents - proposed);
  });

  if (toIncrease.length > 0 && totalFreedCents > 0) {
    var totalIncreaseBudget = toIncrease.reduce(function (s, c) {
      return s + c.knockdownBudgetCents;
    }, 0);

    var totalIncreaseApplied = 0;
    toIncrease.forEach(function (c) {
      var share = totalIncreaseBudget > 0
        ? c.knockdownBudgetCents / totalIncreaseBudget
        : 1 / toIncrease.length;
      var increase = Math.min(
        Math.round(totalFreedCents * share),
        Math.round(c.knockdownBudgetCents * MAX_CHANGE_PCT)
      );
      c.proposedDailyBudgetCents = c.knockdownBudgetCents + increase;
      c.changeCents = c.proposedDailyBudgetCents - c.currentDailyBudgetCents;
      totalIncreaseApplied += increase;
    });

    Logger.log('Freed: $' + (totalFreedCents / 100).toFixed(2) +
      '/day | Redistributed: $' + (totalIncreaseApplied / 100).toFixed(2) + '/day');
  } else if (toIncrease.length === 0 && totalFreedCents > 0) {
    Logger.log('No campaigns eligible for increase. Freed budget not redistributed.');
  }

  toHold.forEach(function (c) {
    c.proposedDailyBudgetCents = c.knockdownBudgetCents;
    c.changeCents = c.proposedDailyBudgetCents - c.currentDailyBudgetCents;
  });

  if (knockdownApplied) {
    eligible.forEach(function (c) {
      if (c.knockdownBudgetCents < c.currentDailyBudgetCents) {
        c.reasons.push('1% portfolio knockdown: spend above $10,500/week');
      }
    });
  }

  eligible.forEach(function (c) {
    if (c.changeCents === undefined || c.changeCents >= 0) return;
    var maxAllowedCutCents = Math.round(c.currentDailyBudgetCents * MAX_REDUCTION_PCT);
    if (Math.abs(c.changeCents) > maxAllowedCutCents) {
      var capped = Math.max(c.currentDailyBudgetCents - maxAllowedCutCents, CAMPAIGN_DAILY_MIN_CENTS);
      Logger.log('4% cap applied to ' + c.name + ': ' +
        (c.proposedDailyBudgetCents / 100).toFixed(0) + ' → ' +
        (capped / 100).toFixed(0) + '/day');
      c.reasons.push('4% reduction cap applied');
      c.changeCents = capped - c.currentDailyBudgetCents;
      c.proposedDailyBudgetCents = capped;
    }
  });

  // ── Session 2: 12% weekly cap (hard rail across all sources) ─────
  // After all other adjustments (knockdown + 4% cap + redistribution),
  // check each campaign's cumulative consumed pct since previous Tuesday.
  // If proposed change would breach the 12% cap, scale it down to the
  // remaining headroom, or skip entirely if no headroom remains. This
  // sums optimizer + knockdown + strategic reallocation movements.
  eligible.forEach(function (c) {
    if (c.changeCents === undefined || c.changeCents === 0) return;
    var consumed = getCampaignWeeklyConsumed_(c.campaignId, prevTue);
    var proposedPct = Math.abs(c.changeCents / c.currentDailyBudgetCents);
    if (consumed + proposedPct <= SCALING_MAX_WEEKLY_PCT) return;

    var allowed = Math.max(0, SCALING_MAX_WEEKLY_PCT - consumed);
    if (allowed <= 0) {
      Logger.log('12% cap: ' + c.name + ' already at ' +
        (consumed * 100).toFixed(1) + '% — proposal dropped.');
      c.reasons.push('12% weekly cap reached (' +
        (consumed * 100).toFixed(1) + '% consumed) — change suppressed');
      c.changeCents = 0;
      c.proposedDailyBudgetCents = c.currentDailyBudgetCents;
      return;
    }
    var sign = c.changeCents > 0 ? 1 : -1;
    var allowedCents = Math.round(c.currentDailyBudgetCents * allowed) * sign;
    Logger.log('12% cap on ' + c.name + ': consumed ' +
      (consumed * 100).toFixed(1) + '% + proposed ' +
      (proposedPct * 100).toFixed(1) + '% > 12%. Scaled to ±' +
      (allowed * 100).toFixed(1) + '%.');
    c.reasons.push('12% weekly cap: ±' +
      (proposedPct * 100).toFixed(1) + '% → ±' +
      (allowed * 100).toFixed(1) + '% (' +
      (consumed * 100).toFixed(1) + '% already consumed this week)');
    c.changeCents = allowedCents;
    c.proposedDailyBudgetCents = c.currentDailyBudgetCents + allowedCents;
  });

  // ── Session 2: tag each eligible campaign with its vertical's
  // scaling classification (informational only — no logic change).
  // Held campaigns get tagged too so the Slack summary can show them.
  if (scalingProfiles) {
    eligible.forEach(function (c) {
      var vertical = getCampaignVertical_(scalingProfiles, c.campaignId);
      var cls = getVerticalClassification_(scalingProfiles, vertical);
      if (cls) {
        c.scalingClassification = cls.classification;
        c.scalingConfidence = cls.confidence;
        c.scalingNewAudienceNeeded = cls.new_audience_needed;
        c.scalingVertical = vertical;
        var tag = vertical + ': ' + cls.classification +
          (cls.confidence === 'directional' ? ' [directional]' : '') +
          (cls.new_audience_needed ? ' + new-audience-needed' : '');
        c.reasons.push(tag);
      }
    });
  }

  var changed = eligible.filter(function (c) {
    return c.changeCents !== undefined && c.changeCents !== 0;
  });

  var finalProposedEligible = eligible.reduce(function (s, c) {
    return s + (c.proposedDailyBudgetCents !== undefined
      ? c.proposedDailyBudgetCents : c.currentDailyBudgetCents);
  }, 0);
  var trueProposedTotal = finalProposedEligible + ineligibleTotal;

  changed._poolWarning = Math.abs(trueProposedTotal - targetDaily) > toleranceDaily;
  changed._currentTotal = currentTotal;
  changed._proposedTotal = trueProposedTotal;
  changed._targetTotal = targetDaily;
  changed._effectiveTolerance = effectiveTolerance;

  Logger.log('Recommendations: ' + changed.length + ' campaigns to change.');
  return changed;
}


// ============================================================
// QUEUE WRITE
// ============================================================

function writeToQueue_(recommendations, source) {
  // `source` (Session 2 schema addition): 'optimizer' for daily budget
  // optimizer rows, 'strategic' for portfolio-scaling weekly reallocations.
  // Existing rows from before this column existed are read as 'optimizer'
  // by both readers; defaults to 'optimizer' if unspecified at write.
  source = source || 'optimizer';

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var qSheet = ss.getSheetByName(BUDGET_SHEET);
  if (!qSheet) qSheet = ss.insertSheet(BUDGET_SHEET);

  if (qSheet.getLastRow() === 0) {
    qSheet.appendRow([
      'token', 'created_at', 'analysis_date', 'execution_scheduled',
      'campaign_id', 'campaign_name',
      'current_budget_cents', 'proposed_budget_cents', 'change_cents', 'change_pct',
      'signal_reasons', 'status', 'source'
    ]);
  } else {
    // Backfill the `source` header on first run after this code ships
    // (existing sheets created before Session 2 only have 12 columns).
    var headerLastCol = qSheet.getLastColumn();
    if (headerLastCol < 13) {
      qSheet.getRange(1, 13).setValue('source');
    }
  }

  var token = Utilities.getUuid().replace(/-/g, '').substring(0, 16);
  var now = new Date();
  var tz = Session.getScriptTimeZone();
  var todayStr = Utilities.formatDate(now, tz, 'yyyy-MM-dd');
  var execStr = Utilities.formatDate(
    new Date(now.getTime() + 86400000), tz, 'yyyy-MM-dd') + ' 03:00';

  var rows = recommendations.map(function (r) {
    var pct = r.currentDailyBudgetCents > 0
      ? Math.round((r.changeCents / r.currentDailyBudgetCents) * 10000) / 100
      : 0;
    return [
      token, now.toISOString(), todayStr, execStr,
      r.campaignId, r.name,
      r.currentDailyBudgetCents, r.proposedDailyBudgetCents,
      r.changeCents, pct,
      r.reasons.join(' | '),
      'pending', source
    ];
  });

  qSheet.getRange(qSheet.getLastRow() + 1, 1, rows.length, 13).setValues(rows);
  PROPS.setProperty('BUDGET_PENDING_TOKEN', token);
  Logger.log('Queue written. ' + rows.length + ' rows. Token: ' + token +
    ' source: ' + source);
  return token;
}


// ============================================================
// BUDGET PROPOSAL WITH AI COMMENTARY
// ============================================================

function postBudgetProposalToSlack_(recs, token, icpPace, allBudgets, replacedPrior) {
  if (!WEB_APP_URL) {
    Logger.log('ERROR: WEB_APP_URL not set in Script Properties.');
    postToSlack_('⚠️ *Honeycomb Budget* — WEB_APP_URL not set in Script Properties. ' +
      'Approve/reject links will not work until this is configured.');
  }
  var scriptUrl = WEB_APP_URL || ScriptApp.getService().getUrl();
  var approveUrl = scriptUrl + '?action=approve&token=' + token;
  var rejectUrl = scriptUrl + '?action=reject&token=' + token;

  var tz = Session.getScriptTimeZone();
  var today = Utilities.formatDate(new Date(), tz, 'EEE MMM d');
  var execDay = Utilities.formatDate(
    new Date(new Date().getTime() + 86400000), tz, 'EEE MMM d');

  var reductions = recs.filter(function (r) { return r.changeCents < 0; });
  var increases = recs.filter(function (r) { return r.changeCents > 0; });

  var currentTotalStr = '$' + (recs._currentTotal / 100).toFixed(0);
  var proposedTotalStr = '$' + (recs._proposedTotal / 100).toFixed(0);
  var weeklyCurrentStr = '$' + (recs._currentTotal * 7 / 100).toFixed(0);
  var weeklyProposedStr = '$' + (recs._proposedTotal * 7 / 100).toFixed(0);

  var contextLines = [
    'BUDGET PROPOSAL — ' + today,
    'Execution scheduled: ' + execDay + ' at 3:00 AM if approved.',
    '',
    'PORTFOLIO',
    'Daily budget pool: ' + currentTotalStr + ' → ' + proposedTotalStr +
    ' | Weekly: ' + weeklyCurrentStr + ' → ' + weeklyProposedStr,
    'ICP pace (rolling 7 days): ' + icpPace.label + ' (reference only — no kill switch)',
    recs._poolWarning
      ? 'WARNING: Proposed pool is outside ±$' + (recs._effectiveTolerance || WEEKLY_SPEND_TOLERANCE) + '/week tolerance.'
      : 'Pool within tolerance.',
    '',
    'PROPOSED CHANGES (' + recs.length + ' campaigns):'
  ];

  recs.forEach(function (r) {
    var dir = r.changeCents > 0 ? 'INCREASE' : 'DECREASE';
    var pct = Math.abs(Math.round((r.changeCents / r.currentDailyBudgetCents) * 1000) / 10);
    contextLines.push(
      dir + ' | ' + r.name +
      ' | $' + (r.currentDailyBudgetCents / 100).toFixed(0) + '/day → ' +
      '$' + (r.proposedDailyBudgetCents / 100).toFixed(0) + '/day (' +
      (r.changeCents > 0 ? '+' : '-') + pct + '%)' +
      ' | Signals: ' + r.reasons.join(' | ')
    );
  });

  contextLines.push('');
  contextLines.push('NOTE: Campaigns not listed above are held — no change recommended.');

  var contextBlock = contextLines.join('\n');
  Logger.log('Budget LLM context:\n' + contextBlock);

  var aiCommentary = '';
  try {
    var systemPrompt = [
      'You are a performance marketing analyst for Honeycomb Credit, a fintech providing investment crowdfunding capital to small food and beverage businesses.',
      'You are reviewing a proposed budget reallocation across Meta ad campaigns.',
      'The recommendations were generated by a deterministic rules engine — your job is to explain them clearly, not re-derive them.',
      '',
      'ICP = contact decisioned as investment_crowdfunding in HubSpot. CPICP = cost per ICP. Lower is better.',
      'ICP estimates use hybrid attribution (v3): IC conversions (deduplicated) + proportional share of unattributed pool.',
      'Budget changes are capped at ±2% per cycle. Portfolio total must stay within $500/week of $10,000 target.',
      'Optimization runs every cycle. ICP pace is shown as context only.',
      '',
      'Write a SHORT plain-text commentary for a Slack message. 3 sections, total 120-160 words maximum.',
      'Plain text only. No markdown headers, no bold (**), no bullet symbols (use - if needed).',
      '',
      'SITUATION',
      '2-3 sentences. What is the portfolio doing this week and why are these changes being proposed?',
      '',
      'CHANGES',
      'One line per campaign being changed. State what is happening and the single most important reason.',
      '',
      'WATCH',
      '1-2 sentences. What should be monitored after these changes execute?',
      '',
      'Rules: numbers only from the data. No invented figures. No hedging language.'
    ].join('\n');

    var llmResp = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
      },
      payload: JSON.stringify({
        model: ANTHROPIC_MODEL,
        max_tokens: 500,
        system: systemPrompt,
        messages: [{ role: 'user', content: contextBlock }]
      }),
      muteHttpExceptions: true
    });

    var llmCode = llmResp.getResponseCode();
    var llmJson = JSON.parse(llmResp.getContentText());

    if (llmCode === 200 && llmJson.content && llmJson.content[0]) {
      aiCommentary = llmJson.content[0].text.trim();
      Logger.log('Budget AI commentary generated.');
    } else {
      Logger.log('Anthropic error (HTTP ' + llmCode + '): ' + llmResp.getContentText());
    }
  } catch (e) {
    Logger.log('Budget LLM exception: ' + e.message);
  }

  var text = '*Honeycomb Budget Proposal — ' + today + '*\n';
  text += 'If approved, executes *' + execDay + ' at 3:00 AM*\n\n';

  text += '*Rolling 7-Day ICP Pace*\n';
  text += icpPace.label + '\n\n';

  text += '*Budget Pool*\n';
  text += 'Daily: ' + currentTotalStr + ' → ' + proposedTotalStr +
    '  |  Weekly: ' + weeklyCurrentStr + ' → ' + weeklyProposedStr;
  if (recs._poolWarning) {
    text += ' ⚠️ *Outside ±$' + (recs._effectiveTolerance || WEEKLY_SPEND_TOLERANCE) + ' tolerance*';
  }
  text += '\n\n';

  if (reductions.length > 0) {
    text += '*Reductions (' + reductions.length + ')*\n';
    reductions.forEach(function (r) {
      var pct = Math.abs(Math.round((r.changeCents / r.currentDailyBudgetCents) * 1000) / 10);
      text += '↓ *' + r.name + '*\n';
      text += '   $' + (r.currentDailyBudgetCents / 100).toFixed(0) + '/day → $' +
        (r.proposedDailyBudgetCents / 100).toFixed(0) + '/day (-' + pct + '%)\n';
      text += '   _' + r.reasons.join(' | ') + '_\n';
    });
    text += '\n';
  }

  if (increases.length > 0) {
    text += '*Increases (' + increases.length + ')*\n';
    increases.forEach(function (r) {
      var pct = Math.round((r.changeCents / r.currentDailyBudgetCents) * 1000) / 10;
      text += '↑ *' + r.name + '*\n';
      text += '   $' + (r.currentDailyBudgetCents / 100).toFixed(0) + '/day → $' +
        (r.proposedDailyBudgetCents / 100).toFixed(0) + '/day (+' + pct + '%)\n';
      text += '   _' + r.reasons.join(' | ') + '_\n';
    });
    text += '\n';
  }

  if (aiCommentary) {
    text += '──────────────────\n';
    text += aiCommentary + '\n\n';
  }

  if (replacedPrior) {
    text += '_Note: this proposal replaces an earlier proposal from today that was not actioned. The previous proposal will be marked expired at next execution._\n\n';
  }
  text += '──────────────────\n';
  text += '✅  Approve: ' + approveUrl + '\n';
  text += '❌  Reject:  ' + rejectUrl;

  postToSlack_(text);
  Logger.log('Budget proposal posted to Slack.');
}


// ============================================================
// STEP 2 — EXECUTION
// ============================================================

function executeBudgetChanges() {
  Logger.log('=== executeBudgetChanges ===');
  validateTokens_();

  var pendingToken = PROPS.getProperty('BUDGET_PENDING_TOKEN');
  var approvedToken = PROPS.getProperty('BUDGET_APPROVED_TOKEN');
  var rejectedToken = PROPS.getProperty('BUDGET_REJECTED_TOKEN');

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var qSheet = ss.getSheetByName(BUDGET_SHEET);

  if (qSheet) {
    var qAll = qSheet.getDataRange().getValues();
    for (var qi0 = 1; qi0 < qAll.length; qi0++) {
      var rowToken = String(qAll[qi0][0]);
      var rowStatus = String(qAll[qi0][11]);
      if (rowStatus === 'pending' && rowToken !== pendingToken) {
        qSheet.getRange(qi0 + 1, 12).setValue('expired');
        Logger.log('Expired orphaned row: token ' + rowToken + ' campaign ' + String(qAll[qi0][5]));
      }
    }
    Logger.log('Orphan expiry pass complete.');
  }

  if (!pendingToken) {
    Logger.log('No pending token found. Nothing scheduled.');
    return;
  }

  if (approvedToken !== pendingToken) {
    var reason = (rejectedToken === pendingToken) ? 'rejected' : 'no approval received';
    Logger.log('Token ' + pendingToken + ' was ' + reason + '. Skipping execution.');

    if (qSheet) {
      var qData0 = qSheet.getDataRange().getValues();
      var termStatus = (rejectedToken === pendingToken) ? 'rejected' : 'expired';
      for (var qi1 = 1; qi1 < qData0.length; qi1++) {
        if (String(qData0[qi1][0]) === pendingToken && String(qData0[qi1][11]) === 'pending') {
          qSheet.getRange(qi1 + 1, 12).setValue(termStatus);
        }
      }
    }

    postToSlack_('*Honeycomb Budget — ' +
      Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'EEE MMM d') + '*\n' +
      (rejectedToken === pendingToken
        ? '❌ Changes rejected. No updates applied.'
        : '⏰ No approval received by 3:00 AM. Changes not applied this cycle.'));
    PROPS.deleteProperty('BUDGET_PENDING_TOKEN');
    PROPS.deleteProperty('BUDGET_REJECTED_TOKEN');
    return;
  }

  if (!qSheet) { Logger.log('ERROR: budget_queue not found.'); return; }

  var results = applyBudgetQueueRows_(qSheet, function (row) {
    return String(row[0]) === pendingToken && String(row[11]) === 'pending';
  });

  PROPS.deleteProperty('BUDGET_PENDING_TOKEN');
  PROPS.deleteProperty('BUDGET_APPROVED_TOKEN');

  postExecutionSummaryToSlack_(results);
  Logger.log('=== executeBudgetChanges complete. ' + results.length + ' campaigns processed. ===');
}


// ============================================================
// SHARED EXECUTION LOOP (used by executeBudgetChanges AND
// executeStrategicChanges). The filter callback decides which rows
// to apply this run; everything else — Meta API write, status
// transition to 'executed' / 'failed', the inter-row sleep, the
// results array — is identical for both.
// ============================================================

function applyBudgetQueueRows_(qSheet, filterFn) {
  var qData = qSheet.getDataRange().getValues();
  var results = [];

  for (var qi = 1; qi < qData.length; qi++) {
    if (!filterFn(qData[qi])) continue;

    var campaignId = String(qData[qi][4]);
    var campaignName = String(qData[qi][5]);
    var currentCents = parseInt(qData[qi][6]);
    var proposedCents = parseInt(qData[qi][7]);

    var success = applyBudgetChange_(campaignId, proposedCents);
    var newStatus = success ? 'executed' : 'failed';

    // Status is column 12 (1-indexed). The new `source` column is 13;
    // we don't touch it here — it was set at write time.
    qSheet.getRange(qi + 1, 12).setValue(newStatus);
    results.push({
      name: campaignName,
      current: currentCents,
      proposed: proposedCents,
      success: success
    });

    Utilities.sleep(300);
  }
  return results;
}


// ============================================================
// META API WRITE
// ============================================================

function applyBudgetChange_(campaignId, newBudgetCents) {
  var url = 'https://graph.facebook.com/' + API_VERSION + '/' + campaignId;
  try {
    var resp = UrlFetchApp.fetch(url, {
      method: 'POST',
      payload: 'daily_budget=' + newBudgetCents + '&access_token=' + ACCESS_TOKEN,
      muteHttpExceptions: true
    });
    var code = resp.getResponseCode();
    var json = JSON.parse(resp.getContentText());

    if (code === 200 && json.success) {
      Logger.log('✓ ' + campaignId + ' → ' + newBudgetCents + ' cents/day');
      return true;
    } else {
      Logger.log('✗ ' + campaignId + ' failed (HTTP ' + code + '): ' +
        (json.error ? json.error.message : resp.getContentText()));
      return false;
    }
  } catch (e) {
    Logger.log('applyBudgetChange_ exception (' + campaignId + '): ' + e.message);
    return false;
  }
}


// ============================================================
// EXECUTION SUMMARY TO SLACK
// ============================================================

function postExecutionSummaryToSlack_(results) {
  var successes = results.filter(function (r) { return r.success; });
  var failures = results.filter(function (r) { return !r.success; });

  var text = '*Honeycomb Budget — Changes Applied (' +
    Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'EEE MMM d') + ')*\n';
  text += successes.length + ' of ' + results.length + ' campaigns updated.\n\n';

  successes.forEach(function (r) {
    var arrow = r.proposed > r.current ? '↑' : '↓';
    text += arrow + ' ' + r.name + ':  $' + (r.current / 100).toFixed(0) +
      ' → $' + (r.proposed / 100).toFixed(0) + '/day\n';
  });

  if (failures.length > 0) {
    text += '\n⚠️ Failed (' + failures.length + ') — check Apps Script logs:\n';
    failures.forEach(function (r) { text += '• ' + r.name + '\n'; });
  }

  postToSlack_(text);
}


// ============================================================
// WEEKLY BUDGET SUMMARY
// ============================================================

function buildBudgetWeeklySummary_() {
  Logger.log('--- buildBudgetWeeklySummary_ ---');
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var qSheet = ss.getSheetByName(BUDGET_SHEET);

  if (!qSheet || qSheet.getLastRow() < 2) {
    Logger.log('No budget_queue data found.');
    return null;
  }

  var tz = Session.getScriptTimeZone();
  var today = new Date();
  var cutoff = new Date(today);
  cutoff.setDate(cutoff.getDate() - 7);
  var cutoffStr = Utilities.formatDate(cutoff, tz, 'yyyy-MM-dd');

  var qData = qSheet.getDataRange().getValues();

  var executed = [];
  var tokenOutcomes = {};

  for (var qi = 1; qi < qData.length; qi++) {
    var row = qData[qi];
    var execDate = String(row[3]).substring(0, 10);
    var status = String(row[11]);
    var token = String(row[0]);

    if (execDate <= cutoffStr) continue;
    if (!tokenOutcomes[token]) tokenOutcomes[token] = status;

    if (status === 'executed') {
      executed.push({
        campaignId: String(row[4]).trim(),
        name: String(row[5]),
        currentCents: parseInt(row[6]) || 0,
        proposedCents: parseInt(row[7]) || 0,
        changeCents: parseInt(row[8]) || 0,
        execDate: execDate
      });
    }
  }

  var rejected = 0, expired = 0;
  Object.values(tokenOutcomes).forEach(function (s) {
    if (s === 'failed' || s === 'pending') expired++;
    if (s === 'rejected') rejected++;
  });

  var netDailyChangeCents = 0;
  var totalIncreasedCents = 0;
  var totalReducedCents = 0;

  executed.forEach(function (r) {
    netDailyChangeCents += r.changeCents;
    if (r.changeCents > 0) totalIncreasedCents += r.changeCents;
    else totalReducedCents += Math.abs(r.changeCents);
  });

  var netWeeklyDollars = (netDailyChangeCents * 7 / 100);
  var totalIncreaseDollars = (totalIncreasedCents * 7 / 100);
  var totalReduceDollars = (totalReducedCents * 7 / 100);

  var currentBudgets = getCurrentMetaBudgets_();
  var currentTotalDailyCents = Object.values(currentBudgets)
    .reduce(function (s, b) { return s + b.dailyBudgetCents; }, 0);
  var anticipatedWeeklySpend = (currentTotalDailyCents * 7 / 100).toFixed(0);

  var lines = [];
  lines.push('*Budget Activity — Week of ' +
    Utilities.formatDate(cutoff, tz, 'MMM d') + ' to ' +
    Utilities.formatDate(today, tz, 'MMM d') + '*');

  var batchCount = Object.keys(tokenOutcomes).length;
  if (batchCount === 0) {
    lines.push('No budget proposals were scheduled this week.');
  } else {
    var executedBatches = Object.values(tokenOutcomes)
      .filter(function (s) { return s === 'executed'; }).length;
    var outcomeStr = executedBatches + ' of ' + batchCount +
      ' proposal' + (batchCount !== 1 ? 's' : '') + ' executed';
    if (rejected > 0) outcomeStr += ', ' + rejected + ' rejected';
    if (expired > 0) outcomeStr += ', ' + expired + ' expired';
    lines.push(outcomeStr);
  }

  if (executed.length > 0) {
    // Collapse multiple changes per campaign into one net line.
    // Key by campaign_id so renames don't create duplicate lines;
    // fall back to name for legacy rows without an ID.
    var byCampaign = {};
    executed.forEach(function (r) {
      var key = r.campaignId || r.name;
      if (!byCampaign[key]) {
        byCampaign[key] = {
          name: r.name,
          firstCents: r.currentCents,
          lastCents: r.proposedCents,
          netChangeCents: 0
        };
      }
      byCampaign[key].name = r.name;
      byCampaign[key].lastCents = r.proposedCents;
      byCampaign[key].netChangeCents += r.changeCents;
    });

    var collapsed = Object.values(byCampaign)
      .filter(function (c) { return c.netChangeCents !== 0; })
      .sort(function (a, b) { return Math.abs(b.netChangeCents) - Math.abs(a.netChangeCents); });

    var uniqueCount = collapsed.length;
    var maxShow = 10;
    var shown = collapsed.slice(0, maxShow);

    lines.push('');
    lines.push('Net changes across ' + uniqueCount + ' campaigns (' +
      Object.keys(tokenOutcomes).filter(function (t) { return tokenOutcomes[t] === 'executed'; }).length +
      ' cycles):');
    shown.forEach(function (c) {
      var arrow = c.netChangeCents > 0 ? '↑' : '↓';
      var weekly = Math.abs(c.netChangeCents * 7 / 100).toFixed(0);
      lines.push(arrow + ' ' + c.name + ':  $' +
        (c.firstCents / 100).toFixed(0) + ' → $' +
        (c.lastCents / 100).toFixed(0) + '/day  ($' + weekly + '/week)');
    });
    if (uniqueCount > maxShow) {
      lines.push('  _...and ' + (uniqueCount - maxShow) + ' more with smaller changes_');
    }

    lines.push('');
    lines.push('Net weekly spend impact:  ' +
      (netWeeklyDollars >= 0 ? '+' : '') + '$' + netWeeklyDollars.toFixed(0) +
      '  (↑$' + totalIncreaseDollars.toFixed(0) +
      ' reallocated from ↓$' + totalReduceDollars.toFixed(0) + ')');
  } else {
    lines.push('No budget changes were applied this week.');
  }

  lines.push('');
  lines.push('*Anticipated spend this week:*  $' + anticipatedWeeklySpend +
    '/week  ($' + (currentTotalDailyCents / 100).toFixed(0) + '/day current)');
  lines.push('_Next proposal: Wednesday morning_');

  return lines.join('\n');
}


// ============================================================
// WEB APP — APPROVAL HANDLER
// ============================================================

function doGet(e) {
  var dashboardResponse = handleDashboardApi_(e);
  if (dashboardResponse) return dashboardResponse;

  var action = e && e.parameter && e.parameter.action;
  var token = e && e.parameter && e.parameter.token;

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

  if (action === 'approve' || action === 'reject') {
    return showBudgetConfirmationPage_(e, action);
  }

  // Strategic reallocation approval (Session 2). Parallel to the
  // optimizer flow above but operates on SCALING_PENDING_TOKEN; the
  // approved-token property and execution trigger are also separate.
  if (action === 'approve_scaling' || action === 'reject_scaling') {
    return showScalingConfirmationPage_(e, action.replace('_scaling', ''));
  }
  if (action === 'confirm_approve_scaling' || action === 'confirm_reject_scaling') {
    var pendingScalingToken = PROPS.getProperty('SCALING_PENDING_TOKEN');
    if (!pendingScalingToken || token !== pendingScalingToken) {
      return HtmlService.createHtmlOutput(
        '<h2>Token mismatch.</h2>' +
        '<p>This strategic proposal has already been actioned, expired, or ' +
        'replaced. No changes were made.</p>');
    }
    var scalingConfirmAction = action === 'confirm_approve_scaling' ? 'approve' : 'reject';
    var sUser = Session.getActiveUser().getEmail() || 'unknown user';

    if (scalingConfirmAction === 'approve') {
      PROPS.setProperty('SCALING_APPROVED_TOKEN', token);
      PROPS.setProperty('SCALING_LAST_APPROVED_BY', sUser);
      PROPS.setProperty('SCALING_LAST_APPROVED_AT', new Date().toISOString());
      postToSlack_('*Honeycomb Scaling* ✅ Strategic reallocation approved by ' +
        sUser + '. Changes will execute Wednesday at 3:00 AM. ' +
        'Optimizer locked out on affected campaigns until Tuesday.');
      return HtmlService.createHtmlOutput(
        '<h2>✅ Strategic reallocation approved.</h2>' +
        '<p>Changes will execute Wednesday at 3:00 AM. Optimizer is now ' +
        'paused on affected campaigns through end-of-Monday.</p>');
    }

    PROPS.setProperty('SCALING_REJECTED_TOKEN', token);
    postToSlack_('*Honeycomb Scaling* ❌ Strategic reallocation rejected by ' +
      sUser + '. Optimizer continues normal cadence.');
    return HtmlService.createHtmlOutput(
      '<h2>❌ Strategic reallocation rejected.</h2>' +
      '<p>No strategic changes will be applied this week. Daily optimizer ' +
      'continues normally.</p>');
  }

  if (action === 'confirm_approve' || action === 'confirm_reject') {
    // Re-validate the token against the current pending. Without this,
    // a confirmation page rendered for token tA can outlive a daily-
    // cadence rotation: by the time the user clicks the confirm button,
    // pending may already be tB, and writing APPROVED/REJECTED for tA
    // causes the next 3 AM run to silently expire tB.
    var pendingForConfirm = PROPS.getProperty('BUDGET_PENDING_TOKEN');
    if (!pendingForConfirm || token !== pendingForConfirm) {
      return HtmlService.createHtmlOutput(
        '<h2>Token mismatch.</h2>' +
        '<p>This proposal has already been actioned, expired, or replaced ' +
        'by a newer one. No changes were made.</p>');
    }

    var confirmAction = action === 'confirm_approve' ? 'approve' : 'reject';

    var user = Session.getActiveUser().getEmail() || 'unknown user';

    if (confirmAction === 'approve') {
      PROPS.setProperty('BUDGET_APPROVED_TOKEN', token);
      PROPS.setProperty('BUDGET_LAST_APPROVED_BY', user);
      PROPS.setProperty('BUDGET_LAST_APPROVED_AT', new Date().toISOString());
      postToSlack_('*Honeycomb Budget* ✅ Approved by ' + user +
        '. Changes will execute tonight at 3:00 AM.');
      return HtmlService.createHtmlOutput(
        '<h2>✅ Budget changes approved.</h2>' +
        '<p>Changes will execute tonight at 3:00 AM. ' +
        'You\'ll receive a Slack confirmation when complete.</p>');
    }

    PROPS.setProperty('BUDGET_REJECTED_TOKEN', token);
    postToSlack_('*Honeycomb Budget* ❌ Rejected by ' + user +
      '. No changes will be applied this cycle.');
    return HtmlService.createHtmlOutput(
      '<h2>❌ Budget changes rejected.</h2>' +
      '<p>No budget changes will be made this cycle.</p>');
  }

  return HtmlService.createHtmlOutput('<h2>Unknown action.</h2>');
}


// Returns an HTML confirmation page with a button. Slack's
// link-unfurling bot will fetch this page but won't click the
// button, so the approval can only be completed by a human.
function showBudgetConfirmationPage_(e, action) {
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
  var color     = isApprove ? '#10b981' : '#ef4444';
  var label     = isApprove ? 'APPROVE BUDGET CHANGES' : 'REJECT BUDGET CHANGES';
  var description = isApprove
    ? 'This will approve the pending budget changes. They will execute tonight at 3:00 AM.'
    : 'This will reject the pending budget changes. No campaign budgets will be modified.';

  var baseUrl    = WEB_APP_URL || ScriptApp.getService().getUrl();
  var confirmUrl = baseUrl + '?action=confirm_' + action + '&token=' + token;

  var html =
    '<!doctype html><html><head>' +
    '<meta charset="utf-8">' +
    '<title>Honeycomb Budget — Confirm</title>' +
    '<meta name="robots" content="noindex">' +
    '<style>body{font-family:system-ui,sans-serif;max-width:480px;margin:60px auto;padding:0 20px;text-align:center}' +
    'h2{margin-bottom:8px}p{color:#555;margin:12px 0 24px}' +
    'a.btn{display:inline-block;padding:14px 32px;background:' + color +
    ';color:#fff;text-decoration:none;border-radius:8px;font-size:16px;font-weight:600}' +
    'a.btn:hover{opacity:0.9}</style></head><body>' +
    '<h2>Honeycomb Budget</h2>' +
    '<p>' + description + '</p>' +
    '<a class="btn" href="' + confirmUrl + '" target="_top">' + label + '</a>' +
    '<p style="margin-top:32px;font-size:13px;color:#999">Click the button above to confirm. ' +
    'This page exists to prevent automated systems from accidentally approving changes.</p>' +
    '</body></html>';

  return HtmlService.createHtmlOutput(html);
}


// Confirmation page for the strategic-reallocation flow (Session 2).
// Mirror of showBudgetConfirmationPage_ but on SCALING_PENDING_TOKEN.
function showScalingConfirmationPage_(e, action) {
  var token = e && e.parameter && e.parameter.token;
  if (!token) {
    return HtmlService.createHtmlOutput('<h2>Invalid link.</h2><p>Missing token.</p>');
  }
  var pendingToken = PROPS.getProperty('SCALING_PENDING_TOKEN');
  if (!pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>No pending strategic proposal.</h2>' +
      '<p>This proposal may have already been actioned or expired.</p>');
  }
  if (token !== pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>Token mismatch.</h2>' +
      '<p>This link is invalid or has already been used.</p>');
  }

  var isApprove = action === 'approve';
  var color = isApprove ? '#10b981' : '#ef4444';
  var label = isApprove ? 'APPROVE STRATEGIC REALLOCATION' : 'REJECT STRATEGIC REALLOCATION';
  var description = isApprove
    ? 'This will approve the pending strategic reallocation. Changes will ' +
      'execute Wednesday at 3:00 AM. The optimizer will be paused on ' +
      'affected campaigns through end-of-Monday.'
    : 'This will reject the pending strategic reallocation. No reallocation ' +
      'will be applied. The daily optimizer continues normal cadence.';

  var baseUrl = WEB_APP_URL || ScriptApp.getService().getUrl();
  var confirmUrl = baseUrl + '?action=confirm_' + action + '_scaling&token=' + token;

  var html =
    '<!doctype html><html><head>' +
    '<meta charset="utf-8">' +
    '<title>Honeycomb Scaling — Confirm</title>' +
    '<meta name="robots" content="noindex">' +
    '<style>body{font-family:system-ui,sans-serif;max-width:480px;margin:60px auto;padding:0 20px;text-align:center}' +
    'h2{margin-bottom:8px}p{color:#555;margin:12px 0 24px}' +
    'a.btn{display:inline-block;padding:14px 32px;background:' + color +
    ';color:#fff;text-decoration:none;border-radius:8px;font-size:16px;font-weight:600}' +
    'a.btn:hover{opacity:0.9}</style></head><body>' +
    '<h2>Honeycomb Strategic Reallocation</h2>' +
    '<p>' + description + '</p>' +
    '<a class="btn" href="' + confirmUrl + '" target="_top">' + label + '</a>' +
    '<p style="margin-top:32px;font-size:13px;color:#999">Click the button above to confirm. ' +
    'This page exists to prevent automated systems from accidentally approving changes.</p>' +
    '</body></html>';

  return HtmlService.createHtmlOutput(html);
}


// ============================================================
// TRIGGER SETUP — BUDGET SYSTEM
// ============================================================

function createBudgetTriggers() {
  Logger.log('=== createBudgetTriggers ===');

  ScriptApp.getProjectTriggers().forEach(function (t) {
    var fn = t.getHandlerFunction();
    if (fn === 'runBudgetAnalysis' || fn === 'executeBudgetChanges' ||
        fn === 'executeStrategicChanges') {
      ScriptApp.deleteTrigger(t);
      Logger.log('Removed existing trigger: ' + fn);
    }
  });

  // Daily cadence: analysis at 6 AM, execution at 3 AM the next day.
  // Each proposal posts to Slack at ~6 AM and either gets approved
  // before 3 AM the next day (~21-hour approval window) or expires
  // unprocessed. The previous Wed/Fri + Thu/Sat schedule gave a
  // longer approval window but only ran the optimizer twice a week;
  // daily means proposals reflect the most recent ad performance.
  ScriptApp.newTrigger('runBudgetAnalysis')
    .timeBased().everyDays(1).atHour(6).create();

  ScriptApp.newTrigger('executeBudgetChanges')
    .timeBased().everyDays(1).atHour(3).create();

  // Strategic reallocation execution. The Tuesday-morning agent posts
  // the proposal (writing SCALING_PENDING_TOKEN); Tyler approves Tuesday
  // afternoon; Wed 3 AM applies the changes and writes lockout properties.
  // The trigger fires daily at 3 AM and no-ops cheaply when there's no
  // pending token (most days) — the per-day cost is one Script Property
  // read. We don't restrict to Wednesdays at the trigger level because
  // a manual workflow_dispatch on a non-Tuesday should still execute on
  // the next 3 AM after approval.
  ScriptApp.newTrigger('executeStrategicChanges')
    .timeBased().everyDays(1).atHour(3).create();

  var triggers = ScriptApp.getProjectTriggers();
  Logger.log('Total active triggers after setup: ' + triggers.length);
  triggers.forEach(function (t) { Logger.log('  ' + t.getHandlerFunction()); });
}


// ============================================================
// STRATEGIC EXECUTION — Wed 3 AM (Session 2)
// ============================================================
//
// Mirror of executeBudgetChanges but for strategic-reallocation rows.
// Both functions delegate per-row Meta API writes to applyBudgetQueueRows_
// so the execution loop is shared.
//
// On successful approval-and-execution, writes the lockout window into
// SCALING_LOCKOUT_UNTIL + SCALING_AFFECTED_CAMPAIGN_IDS so the daily
// optimizer sees the locked campaigns and skips them through end-of-Monday.

function executeStrategicChanges() {
  Logger.log('=== executeStrategicChanges ===');
  validateTokens_();

  var pendingToken = PROPS.getProperty('SCALING_PENDING_TOKEN');
  var approvedToken = PROPS.getProperty('SCALING_APPROVED_TOKEN');
  var rejectedToken = PROPS.getProperty('SCALING_REJECTED_TOKEN');

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var qSheet = ss.getSheetByName(BUDGET_SHEET);

  // Orphan expiry pass — strategic rows from a prior pending token that
  // was superseded but never executed. Mirror of the optimizer's pass at
  // executeBudgetChanges. Without this, stale pending strategic rows
  // accumulate in budget_queue forever.
  if (qSheet) {
    var qAll = qSheet.getDataRange().getValues();
    for (var qi0 = 1; qi0 < qAll.length; qi0++) {
      var rowToken = String(qAll[qi0][0]);
      var rowStatus = String(qAll[qi0][11]);
      var rowSource = String(qAll[qi0][12] || '').toLowerCase();
      if (rowSource === 'strategic' && rowStatus === 'pending' &&
          rowToken !== pendingToken) {
        qSheet.getRange(qi0 + 1, 12).setValue('expired');
        Logger.log('Expired orphaned strategic row: token ' + rowToken +
          ' campaign ' + String(qAll[qi0][5]));
      }
    }
  }

  if (!pendingToken) {
    Logger.log('No pending strategic token. Nothing scheduled.');
    return;
  }

  if (!qSheet) {
    Logger.log('ERROR: budget_queue not found.');
    return;
  }

  if (approvedToken !== pendingToken) {
    var reason = (rejectedToken === pendingToken) ? 'rejected' : 'no approval received';
    Logger.log('Strategic token ' + pendingToken + ' was ' + reason + '. Skipping execution.');

    // Mark all pending strategic rows for this token as rejected/expired.
    var qData0 = qSheet.getDataRange().getValues();
    var termStatus = (rejectedToken === pendingToken) ? 'rejected' : 'expired';
    for (var qi1 = 1; qi1 < qData0.length; qi1++) {
      if (String(qData0[qi1][0]) === pendingToken && String(qData0[qi1][11]) === 'pending') {
        qSheet.getRange(qi1 + 1, 12).setValue(termStatus);
      }
    }

    postToSlack_('*Honeycomb Scaling — ' +
      Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'EEE MMM d') + '*\n' +
      (rejectedToken === pendingToken
        ? '❌ Strategic reallocation rejected. No reallocation applied.'
        : '⏰ No approval received by 3:00 AM Wednesday. Reallocation expired.'));
    PROPS.deleteProperty('SCALING_PENDING_TOKEN');
    PROPS.deleteProperty('SCALING_REJECTED_TOKEN');
    PROPS.deleteProperty('SCALING_PENDING_LOCKOUT_UNTIL');
    PROPS.deleteProperty('SCALING_PENDING_AFFECTED_IDS');
    return;
  }

  // Approved AND it's the active pending token — execute.
  var results = applyBudgetQueueRows_(qSheet, function (row) {
    return String(row[0]) === pendingToken &&
           String(row[11]) === 'pending' &&
           String(row[12] || '').toLowerCase() === 'strategic';
  });

  // Promote stashed lockout metadata to the live keys.
  var lockoutUntil = PROPS.getProperty('SCALING_PENDING_LOCKOUT_UNTIL');
  var affectedIds = PROPS.getProperty('SCALING_PENDING_AFFECTED_IDS');
  if (lockoutUntil) {
    PROPS.setProperty('SCALING_LOCKOUT_UNTIL', lockoutUntil);
  }
  if (affectedIds) {
    PROPS.setProperty('SCALING_AFFECTED_CAMPAIGN_IDS', affectedIds);
  }

  PROPS.deleteProperty('SCALING_PENDING_TOKEN');
  PROPS.deleteProperty('SCALING_APPROVED_TOKEN');
  PROPS.deleteProperty('SCALING_PENDING_LOCKOUT_UNTIL');
  PROPS.deleteProperty('SCALING_PENDING_AFFECTED_IDS');

  postStrategicExecutionSummaryToSlack_(results, lockoutUntil);
  Logger.log('=== executeStrategicChanges complete. ' + results.length +
    ' campaigns processed. Lockout until ' + lockoutUntil + ' ===');
}


function postStrategicExecutionSummaryToSlack_(results, lockoutUntil) {
  var successes = results.filter(function (r) { return r.success; });
  var failures = results.filter(function (r) { return !r.success; });

  var text = '*Honeycomb Scaling — Strategic Reallocation Applied (' +
    Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'EEE MMM d') + ')*\n';
  text += successes.length + ' of ' + results.length + ' campaigns updated.\n\n';

  successes.forEach(function (r) {
    var arrow = r.proposed > r.current ? '↑' : '↓';
    text += arrow + ' ' + r.name + ':  $' + (r.current / 100).toFixed(0) +
      ' → $' + (r.proposed / 100).toFixed(0) + '/day\n';
  });

  if (failures.length > 0) {
    text += '\n⚠️ Failed (' + failures.length + ') — check Apps Script logs:\n';
    failures.forEach(function (r) { text += '  ' + r.name + '\n'; });
  }

  if (lockoutUntil) {
    text += '\nOptimizer locked out on these campaigns until ' +
      String(lockoutUntil).substring(0, 10) + '.';
  }
  postToSlack_(text);
}


// ============================================================
// BUDGET SYSTEM DIAGNOSTIC
// ============================================================

function testBudgetSystem() {
  Logger.log('=== testBudgetSystem ===');
  validateTokens_();

  Logger.log('--- Testing Meta budget read ---');
  var budgets = getCurrentMetaBudgets_();
  if (Object.keys(budgets).length === 0) {
    Logger.log('FAIL: No budgets returned. Check META_ACCESS_TOKEN and account ID.');
  } else {
    Logger.log('PASS: ' + Object.keys(budgets).length + ' active CBO campaigns found:');
    Object.values(budgets).forEach(function (b) {
      Logger.log('  ' + b.name + ': $' + (b.dailyBudgetCents / 100).toFixed(2) + '/day');
    });
  }

  Logger.log('--- Testing signal computation ---');
  var signals = computeBudgetSignals_();
  Logger.log('Signals: ' + Object.keys(signals).length + ' campaigns in 7-day window.');

  Logger.log('--- Testing ICP pace ---');
  var pace = computeWeeklyICPPace_();

  Logger.log('--- Web App URL ---');
  var url = WEB_APP_URL;
  if (url) {
    Logger.log('PASS: WEB_APP_URL = ' + url);
  } else {
    Logger.log('FAIL: WEB_APP_URL not set in Script Properties.');
  }

  Logger.log('=== testBudgetSystem complete ===');
}
// ââââââââââââââââââââââââââââ BEGIN PASTE ââââââââââââââââââââââââââââ


// âââ DASHBOARD API ROUTER âââââââââââââââââââââââââââââââââââ
// Returns a Response object for dashboard actions, or null
// for anything else (so the existing doGet can keep handling
// approve/reject links unchanged).
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
    get_campaign_budgets: true,
    propose_spend_target: true,
    // Two-step Slack-safe approval for spend target changes.
    // Same pattern as the budget proposal flow: bare URLs show
    // an HTML confirmation page, actual state change only on
    // the confirm_ variant.
    approve_target: true,
    reject_target: true,
    confirm_approve_target: true,
    confirm_reject_target: true,
    // Agent skills surface area. Read-only ones go through the switch
    // at the bottom; writers (`*-write`) handle their own response.
    'rolling-latest-date': true,
    'health-write': true,
    'daily-check-write': true,
    'budget-queue-read': true,
    'fatigue-write': true,
    'creative-intelligence-write': true
  };

  if (!action || !dashboardActions[action]) return null;

  // Chat returns its own Response object.
  if (action === 'chat') {
    return handleChatRequest_(e);
  }

  // Budget analysis trigger — calls the existing
  // runBudgetAnalysis() function from the intelligence
  // layer. This is the same function the daily cron
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

  // Fetch current daily budgets from Meta for all active
  // campaigns. Returns { campaignId: { name, dailyBudgetCents, status } }.
  // Used by the dashboard to compute budget pacing/utilization.
  // This makes a live Meta API call, so it's not called on every
  // page load — only when the dashboard needs it.
  if (action === 'get_campaign_budgets') {
    try {
      var budgets = getCurrentMetaBudgets_();
      // Convert to array for easier frontend consumption,
      // including campaign_id for matching.
      var budgetList = Object.keys(budgets).map(function(cid) {
        return {
          campaign_id: cid,
          campaign_name: budgets[cid].name,
          daily_budget_cents: budgets[cid].dailyBudgetCents,
          status: budgets[cid].status
        };
      });
      return jsonResponse_(budgetList);
    } catch (err) {
      Logger.log('get_campaign_budgets error: ' + err.message);
      return jsonResponse_({ error: 'Failed to fetch Meta budgets: ' + err.message });
    }
  }

  // Read the current spend goal + tolerance. These
  // default to the hardcoded constants but can be
  // overridden via Script Properties so the dashboard
  // can adjust them without a code change. Also returns
  // any pending (unapproved) proposal so the dashboard
  // can show its status.
  if (action === 'get_spend_goal') {
    var goalOverride      = PROPS.getProperty('DASHBOARD_TARGET_WEEKLY_SPEND');
    var toleranceOverride = PROPS.getProperty('DASHBOARD_WEEKLY_SPEND_TOLERANCE');
    var pendingTarget     = PROPS.getProperty('PENDING_SPEND_TARGET');
    var pendingTolerance  = PROPS.getProperty('PENDING_SPEND_TOLERANCE');
    var pendingAt         = PROPS.getProperty('PENDING_SPEND_TARGET_AT');
    var response = {
      target_weekly_spend:    goalOverride      ? Number(goalOverride)      : TARGET_WEEKLY_SPEND,
      weekly_spend_tolerance: toleranceOverride  ? Number(toleranceOverride) : WEEKLY_SPEND_TOLERANCE,
      source: goalOverride ? 'script_property_override' : 'hardcoded_default',
      pending: null,
      // Budget operation context for the dashboard's
      // Budget Controls panel.
      budget_context: {
        slack_channel: PROPS.getProperty('SLACK_CHANNEL') || '#marketing-ads-budget',
        last_run_at:      PROPS.getProperty('BUDGET_LAST_RUN_AT') || null,
        last_approved_by: PROPS.getProperty('BUDGET_LAST_APPROVED_BY') || null,
        last_approved_at: PROPS.getProperty('BUDGET_LAST_APPROVED_AT') || null
      }
    };
    if (pendingTarget) {
      response.pending = {
        target:    Number(pendingTarget),
        tolerance: pendingTolerance ? Number(pendingTolerance) : null,
        proposed_at: pendingAt || null
      };
    }
    return jsonResponse_(response);
  }

  // Propose a spend target change. Does NOT take effect
  // immediately — saves the proposed values as pending and
  // posts a Slack message with approve/reject links. The
  // target only becomes active after Slack approval.
  if (action === 'propose_spend_target') {
    var newTarget    = e.parameter.target;
    var newTolerance = e.parameter.tolerance;

    if (!newTarget || isNaN(Number(newTarget)) || Number(newTarget) <= 0) {
      return jsonResponse_({ error: 'Invalid target value. Must be a positive number.' });
    }

    var targetVal    = Math.round(Number(newTarget));
    var toleranceVal = (newTolerance != null && !isNaN(Number(newTolerance)) && Number(newTolerance) >= 0)
      ? Math.round(Number(newTolerance)) : null;

    // Generate a unique token for this proposal.
    var token = Utilities.getUuid().replace(/-/g, '').substring(0, 16);

    // Save pending values to Script Properties.
    PROPS.setProperty('SPEND_TARGET_PENDING_TOKEN', token);
    PROPS.setProperty('PENDING_SPEND_TARGET', String(targetVal));
    if (toleranceVal !== null) {
      PROPS.setProperty('PENDING_SPEND_TOLERANCE', String(toleranceVal));
    } else {
      PROPS.deleteProperty('PENDING_SPEND_TOLERANCE');
    }
    PROPS.setProperty('PENDING_SPEND_TARGET_AT', new Date().toISOString());

    // Current values for context in the Slack message.
    var currentTarget    = getTargetWeeklySpend_();
    var currentTolerance = getWeeklySpendTolerance_();

    var baseUrl    = WEB_APP_URL || ScriptApp.getService().getUrl();
    var approveUrl = baseUrl + '?action=approve_target&token=' + token;
    var rejectUrl  = baseUrl + '?action=reject_target&token=' + token;

    var slackText = '*Honeycomb Spend Target Change*\n\n';
    slackText += '*Proposed target:* $' + targetVal.toLocaleString() + '/week';
    slackText += '  (currently $' + currentTarget.toLocaleString() + '/week)\n';
    if (toleranceVal !== null && toleranceVal !== currentTolerance) {
      slackText += '*Proposed tolerance:* \u00b1$' + toleranceVal + '/week';
      slackText += '  (currently \u00b1$' + currentTolerance + '/week)\n';
    } else {
      slackText += '*Tolerance:* \u00b1$' + currentTolerance + '/week (unchanged)\n';
    }
    slackText += '\nThis changes the weekly spend target the budget optimizer aims at. ';
    slackText += 'Takes effect on the next optimization run after approval.\n\n';
    slackText += '\u2705  Approve: ' + approveUrl + '\n';
    slackText += '\u274c  Reject:  ' + rejectUrl;

    try {
      postToSlack_(slackText);
    } catch (slackErr) {
      Logger.log('propose_spend_target: Slack post failed: ' + slackErr.message);
      // Still return success — the proposal is saved even if Slack fails.
    }

    return jsonResponse_({
      ok: true,
      message: 'Spend target change proposed. Check Slack for approval.'
    });
  }

  // Spend target approval — two-step Slack-safe flow.
  // Bare approve/reject URLs show an HTML confirmation page
  // (so Slack's link-unfurl crawler can't accidentally fire them).
  // The confirm_ variants do the actual state change.
  if (action === 'approve_target' || action === 'reject_target') {
    return showTargetApprovalPage_(e, action === 'approve_target' ? 'approve' : 'reject');
  }
  if (action === 'confirm_approve_target' || action === 'confirm_reject_target') {
    return applyTargetDecision_(e, action === 'confirm_approve_target' ? 'approve' : 'reject');
  }

  // Agent skills — `pipeline-health` skill calls `health-write` to append
  // check results to the `pipeline_health` tab. Accepts rows via a `rows`
  // query/POST param (JSON-encoded array) or repeated check params.
  if (action === 'health-write') {
    return handleHealthWrite_(e);
  }

  // `daily-check` skill writes a single summary row per run to
  // `daily_check_log`. Accepts JSON body {row:{...}} or query params.
  if (action === 'daily-check-write') {
    return handleDailyCheckWrite_(e);
  }

  // `fatigue-monitor` skill writes one row per evaluated ad to
  // `fatigue_log`. Accepts a JSON body {rows:[...]} batch payload or a
  // form-encoded `rows=<json>` param.
  if (action === 'fatigue-write') {
    return handleFatigueWrite_(e);
  }

  // `creative-intelligence` skill writes one row per vertical per
  // weekly run to `creative_intelligence_log`. Same payload shape as
  // fatigue-write.
  if (action === 'creative-intelligence-write') {
    return handleCreativeIntelligenceWrite_(e);
  }

  // `portfolio-scaling` skill writes one scaling_log row per vertical
  // per Tuesday run. Same dual-wire pattern.
  if (action === 'scaling-write') {
    return handleScalingWrite_(e);
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
      case 'rolling-latest-date':
        result = getRollingLatestDate_();
        break;
      case 'budget-queue-read':
        result = getBudgetQueuePending_(e.parameter);
        break;
      case 'scaling-queue-read':
        result = getScalingQueueRows_(e.parameter);
        break;
      case 'scaling-log-read':
        result = getScalingLogRows_(e.parameter);
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


// ─── AGENT SKILLS — read/write helpers ──────────────────────
// These are surfaced by the `pipeline-health` skill. Other skills
// will follow the same pattern in their respective sessions.

// Read-only: returns the most recent date present in rolling_data.
// Used by the pipeline-health data-freshness check. Counts only rows
// with a parseable date value in column A.
function getRollingLatestDate_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(META_SHEET);
  if (!sheet) {
    return { latest_date: null, total_rows: 0,
             error: 'Sheet "' + META_SHEET + '" not found' };
  }
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return { latest_date: null, total_rows: 0 };
  }
  var dates = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  var latest = null;
  var counted = 0;
  for (var i = 0; i < dates.length; i++) {
    var v = dates[i][0];
    if (!v) continue;
    counted++;
    var d = (v instanceof Date) ? v : new Date(v);
    if (isNaN(d.getTime())) continue;
    if (latest === null || d > latest) latest = d;
  }
  return {
    latest_date: latest
      ? Utilities.formatDate(latest, Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : null,
    total_rows: counted
  };
}


// Append-only: writes pipeline_health rows. Creates the tab on first call.
// Accepts either:
//   - `rows` parameter (JSON-encoded array of {date, check, status, detail})
//   - or four parameters `date`, `check`, `status`, `detail` for a single row
// Returns { ok, written } or { error }.
function handleHealthWrite_(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('pipeline_health');
  if (!sheet) {
    sheet = ss.insertSheet('pipeline_health');
    sheet.appendRow(['date', 'check', 'status', 'detail', 'recorded_at']);
  }

  var rows = [];
  var p = (e && e.parameter) || {};

  // Wire formats supported, in priority order:
  //   1. JSON body POST: {"rows": [{date, check, status, detail}, ...]}
  //   2. Form-encoded `rows` param holding a JSON array
  //   3. Single row via `check`/`status`/`detail`/`date` query params
  var bodyRows = null;
  if (e && e.postData && e.postData.contents) {
    try {
      var parsed = JSON.parse(e.postData.contents);
      if (parsed && Array.isArray(parsed.rows)) bodyRows = parsed.rows;
    } catch (parseErr) {
      // Not JSON — fall through to form/query handling.
    }
  }

  if (bodyRows) {
    rows = bodyRows;
  } else if (p.rows) {
    try {
      rows = JSON.parse(p.rows);
      if (!Array.isArray(rows)) {
        return jsonResponse_({ error: 'rows must be a JSON array' });
      }
    } catch (err) {
      return jsonResponse_({ error: 'rows is not valid JSON: ' + err.message });
    }
  } else if (p.check) {
    rows = [{
      date: p.date,
      check: p.check,
      status: p.status,
      detail: p.detail
    }];
  } else {
    return jsonResponse_({ error: 'no rows provided (use JSON body, rows=<json>, or check/status/detail)' });
  }

  var nowIso = new Date().toISOString();
  var written = 0;
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i] || {};
    if (!r.check) continue;
    sheet.appendRow([
      r.date || '',
      String(r.check),
      String(r.status || ''),
      String(r.detail || ''),
      nowIso
    ]);
    written++;
  }

  return jsonResponse_({ ok: true, written: written });
}


// Append-only: writes one daily-check summary row. Creates the tab on first
// call. Accepts:
//   - JSON body POST: {"row": {date, pacing_status, total_spend, total_icps,
//                              portfolio_cpicp, fatigue_flag_count}}
//   - or those fields as form/query params for ad-hoc testing
function handleDailyCheckWrite_(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('daily_check_log');
  if (!sheet) {
    sheet = ss.insertSheet('daily_check_log');
    sheet.appendRow(['date', 'pacing_status', 'total_spend', 'total_icps',
      'portfolio_cpicp', 'fatigue_flag_count', 'recorded_at']);
  }

  var row = null;
  var p = (e && e.parameter) || {};

  if (e && e.postData && e.postData.contents) {
    try {
      var parsed = JSON.parse(e.postData.contents);
      if (parsed && parsed.row && typeof parsed.row === 'object') {
        row = parsed.row;
      }
    } catch (parseErr) {
      // Fall through to form/query handling.
    }
  }

  if (!row && p.date) {
    row = {
      date: p.date,
      pacing_status: p.pacing_status,
      total_spend: p.total_spend,
      total_icps: p.total_icps,
      portfolio_cpicp: p.portfolio_cpicp,
      fatigue_flag_count: p.fatigue_flag_count
    };
  }

  if (!row || !row.date) {
    return jsonResponse_({ error: 'no row provided (use JSON body {row:{...}} or date/pacing_status/... params)' });
  }

  var nowIso = new Date().toISOString();
  sheet.appendRow([
    String(row.date || ''),
    String(row.pacing_status || ''),
    Number(row.total_spend) || 0,
    Number(row.total_icps) || 0,
    row.portfolio_cpicp == null ? '' : Number(row.portfolio_cpicp),
    Number(row.fatigue_flag_count) || 0,
    nowIso
  ]);

  return jsonResponse_({ ok: true, written: 1 });
}


// Read-only: returns currently `pending` rows from `budget_queue`. The
// `fatigue-monitor` skill calls this so it can flag conflicts (e.g. a
// fatiguing ad in a campaign with a pending budget INCREASE). Optional
// `campaign_id` param narrows the result; otherwise returns all pending.
function getBudgetQueuePending_(params) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(BUDGET_SHEET);
  if (!sheet || sheet.getLastRow() < 2) return { pending: [] };

  var data = sheet.getDataRange().getValues();
  // Schema (writeToQueue_):
  //   0:token 1:created_at 2:analysis_date 3:execution_scheduled
  //   4:campaign_id 5:campaign_name
  //   6:current_budget_cents 7:proposed_budget_cents
  //   8:change_cents 9:change_pct
  //   10:signal_reasons 11:status 12:source (added Session 2)
  var filterCampaignId = params && params.campaign_id
    ? String(params.campaign_id).trim() : null;

  var pending = [];
  for (var i = 1; i < data.length; i++) {
    var r = data[i];
    if (String(r[11]).toLowerCase() !== 'pending') continue;
    var campaignId = String(r[4]).trim();
    if (filterCampaignId && campaignId !== filterCampaignId) continue;

    var changeCents = Number(r[8]) || 0;
    pending.push({
      token: String(r[0]),
      created_at: r[1] instanceof Date ? r[1].toISOString() : String(r[1]),
      analysis_date: String(r[2]).substring(0, 10),
      execution_scheduled: String(r[3]),
      campaign_id: campaignId,
      campaign_name: String(r[5]),
      current_budget_cents: Number(r[6]) || 0,
      proposed_budget_cents: Number(r[7]) || 0,
      change_cents: changeCents,
      change_pct: Number(r[9]) || 0,
      direction: changeCents > 0 ? 'increase' : (changeCents < 0 ? 'decrease' : 'flat'),
      signal_reasons: String(r[10]),
      status: 'pending',
      // Pre-Session-2 rows have no column 13; default to 'optimizer'.
      source: r.length > 12 && r[12] ? String(r[12]) : 'optimizer'
    });
  }
  return { pending: pending, count: pending.length };
}


// Append-only: writes per-ad fatigue classifications. Creates the tab on
// first call. The `fatigue-monitor` skill writes ALL evaluated ads
// (including healthy) for the historical record.
function handleFatigueWrite_(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('fatigue_log');
  if (!sheet) {
    sheet = ss.insertSheet('fatigue_log');
    sheet.appendRow([
      'date', 'ad_id', 'ad_name', 'campaign',
      'classification',
      'ctr_baseline', 'ctr_current', 'ctr_decline_pct',
      'frequency',
      'cpc_baseline', 'cpc_current',
      'days_active', 'baseline_type',
      'budget_conflict',
      'recorded_at'
    ]);
  }

  var rows = [];
  var p = (e && e.parameter) || {};

  if (e && e.postData && e.postData.contents) {
    try {
      var parsed = JSON.parse(e.postData.contents);
      if (parsed && Array.isArray(parsed.rows)) rows = parsed.rows;
    } catch (parseErr) {
      // fall through
    }
  }
  if (!rows.length && p.rows) {
    try {
      rows = JSON.parse(p.rows);
      if (!Array.isArray(rows)) {
        return jsonResponse_({ error: 'rows must be a JSON array' });
      }
    } catch (err) {
      return jsonResponse_({ error: 'rows is not valid JSON: ' + err.message });
    }
  }

  if (!rows.length) {
    return jsonResponse_({ error: 'no rows provided (use JSON body {rows:[...]} or rows=<json>)' });
  }

  var nowIso = new Date().toISOString();
  var written = 0;
  for (var ri = 0; ri < rows.length; ri++) {
    var row = rows[ri] || {};
    if (!row.ad_id) continue;
    sheet.appendRow([
      String(row.date || ''),
      String(row.ad_id),
      String(row.ad_name || ''),
      String(row.campaign || ''),
      String(row.classification || ''),
      row.ctr_baseline == null ? '' : Number(row.ctr_baseline),
      row.ctr_current == null ? '' : Number(row.ctr_current),
      row.ctr_decline_pct == null ? '' : Number(row.ctr_decline_pct),
      row.frequency == null ? '' : Number(row.frequency),
      row.cpc_baseline == null ? '' : Number(row.cpc_baseline),
      row.cpc_current == null ? '' : Number(row.cpc_current),
      row.days_active == null ? '' : Number(row.days_active),
      String(row.baseline_type || ''),
      String(row.budget_conflict || ''),
      nowIso
    ]);
    written++;
  }
  return jsonResponse_({ ok: true, written: written });
}


// Append-only: writes one creative_intelligence row per vertical
// per weekly run. Creates the tab on first call. The
// `creative-intelligence` skill writes rows for ALL verticals so the
// per-vertical trend over weeks is preserved for week-over-week
// deltas. Same dual-wire pattern as the other *-write handlers.
function handleCreativeIntelligenceWrite_(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('creative_intelligence_log');
  if (!sheet) {
    sheet = ss.insertSheet('creative_intelligence_log');
    sheet.appendRow([
      'date', 'vertical',
      'ad_count', 'median_cpicp',
      'spend_total', 'ic_total',
      'top_body_variant_id', 'top_body_text', 'top_body_cpicp',
      'top_visual_hash', 'top_visual_style',
      'bottom_decile_count',
      'recorded_at'
    ]);
  }

  var rows = [];
  var p = (e && e.parameter) || {};

  if (e && e.postData && e.postData.contents) {
    try {
      var parsed = JSON.parse(e.postData.contents);
      if (parsed && Array.isArray(parsed.rows)) rows = parsed.rows;
    } catch (parseErr) {
      // fall through to query-string parsing
    }
  }
  if (!rows.length && p.rows) {
    try {
      rows = JSON.parse(p.rows);
      if (!Array.isArray(rows)) {
        return jsonResponse_({ error: 'rows must be a JSON array' });
      }
    } catch (err) {
      return jsonResponse_({ error: 'rows is not valid JSON: ' + err.message });
    }
  }

  if (!rows.length) {
    return jsonResponse_({ error: 'no rows provided (use JSON body {rows:[...]} or rows=<json>)' });
  }

  var nowIso = new Date().toISOString();
  var written = 0;
  for (var ri = 0; ri < rows.length; ri++) {
    var row = rows[ri] || {};
    if (!row.vertical) continue;
    sheet.appendRow([
      String(row.date || ''),
      String(row.vertical),
      Number(row.ad_count) || 0,
      row.median_cpicp == null ? '' : Number(row.median_cpicp),
      row.spend_total == null ? '' : Number(row.spend_total),
      Number(row.ic_total) || 0,
      String(row.top_body_variant_id || ''),
      String(row.top_body_text || ''),
      row.top_body_cpicp == null ? '' : Number(row.top_body_cpicp),
      String(row.top_visual_hash || ''),
      String(row.top_visual_style || ''),
      Number(row.bottom_decile_count) || 0,
      nowIso
    ]);
    written++;
  }
  return jsonResponse_({ ok: true, written: written });
}


// Receives the Tuesday-morning strategic-reallocation proposal from the
// portfolio-scaling agent workflow. Writes one row per affected campaign
// to budget_queue with status=pending, source=strategic, and a freshly
// minted token. Stashes the lockout metadata under that token's
// Script Properties for executeStrategicChanges to apply on Wed 3 AM.
//
// Payload (POST JSON body):
//   {
//     "rows": [{ campaign_id, campaign_name, current_daily_cents,
//                proposed_daily_cents, change_cents, change_pct,
//                signal_reasons }, ...],
//     "lockout_until": "2026-05-19T00:00:00Z",
//     "affected_campaign_ids": ["...", "..."]
//   }
//
// Returns: { ok, token, written, approve_url, reject_url }
function handleScalingQueueWrite_(e) {
  if (!e || !e.postData || !e.postData.contents) {
    return jsonResponse_({ error: 'POST body required' });
  }
  var payload;
  try { payload = JSON.parse(e.postData.contents); }
  catch (parseErr) {
    return jsonResponse_({ error: 'Invalid JSON: ' + parseErr.message });
  }
  var rows = payload && payload.rows;
  if (!Array.isArray(rows) || rows.length === 0) {
    return jsonResponse_({ error: 'rows must be a non-empty array' });
  }
  var lockoutUntil = payload.lockout_until;
  var affectedIds = payload.affected_campaign_ids;
  if (!lockoutUntil || !Array.isArray(affectedIds)) {
    return jsonResponse_({
      error: 'lockout_until (ISO string) and affected_campaign_ids ' +
             '(array) required'
    });
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var qSheet = ss.getSheetByName(BUDGET_SHEET);
  if (!qSheet) qSheet = ss.insertSheet(BUDGET_SHEET);

  // Auto-init header on first run (matches writeToQueue_).
  if (qSheet.getLastRow() === 0) {
    qSheet.appendRow([
      'token', 'created_at', 'analysis_date', 'execution_scheduled',
      'campaign_id', 'campaign_name',
      'current_budget_cents', 'proposed_budget_cents',
      'change_cents', 'change_pct',
      'signal_reasons', 'status', 'source'
    ]);
  } else if (qSheet.getLastColumn() < 13) {
    qSheet.getRange(1, 13).setValue('source');
  }

  var token = Utilities.getUuid().replace(/-/g, '').substring(0, 16);
  var now = new Date();
  var tz = Session.getScriptTimeZone();
  var todayStr = Utilities.formatDate(now, tz, 'yyyy-MM-dd');
  // Strategic execution runs Wed 3 AM regardless of when this is
  // written (Tuesday morning). Compute the next Wednesday.
  var nextWedDate = new Date(now.getTime());
  while (nextWedDate.getDay() !== 3) {
    nextWedDate = new Date(nextWedDate.getTime() + 86400000);
  }
  var execStr = Utilities.formatDate(nextWedDate, tz, 'yyyy-MM-dd') + ' 03:00';

  var sheetRows = rows.map(function (r) {
    return [
      token, now.toISOString(), todayStr, execStr,
      String(r.campaign_id || ''), String(r.campaign_name || ''),
      Number(r.current_daily_cents) || 0,
      Number(r.proposed_daily_cents) || 0,
      Number(r.change_cents) || 0,
      Number(r.change_pct) || 0,
      String(r.signal_reasons || ''),
      'pending', 'strategic'
    ];
  });

  qSheet.getRange(qSheet.getLastRow() + 1, 1, sheetRows.length, 13)
    .setValues(sheetRows);

  // Stash lockout metadata under token-scoped properties.
  PROPS.setProperty('SCALING_PENDING_TOKEN', token);
  PROPS.setProperty('SCALING_PENDING_LOCKOUT_UNTIL', String(lockoutUntil));
  PROPS.setProperty('SCALING_PENDING_AFFECTED_IDS',
    affectedIds.map(String).join(','));

  var baseUrl = WEB_APP_URL || ScriptApp.getService().getUrl();
  Logger.log('Strategic queue written. ' + sheetRows.length +
    ' rows. Token: ' + token + ' lockout_until=' + lockoutUntil);

  return jsonResponse_({
    ok: true,
    token: token,
    written: sheetRows.length,
    approve_url: baseUrl + '?action=approve_scaling&token=' + token,
    reject_url: baseUrl + '?action=reject_scaling&token=' + token,
    lockout_until: lockoutUntil
  });
}


// Append-only: writes one scaling_log row per vertical per Tuesday run.
// Creates the tab on first call. The `portfolio-scaling` skill writes
// rows for every vertical (including insufficient-data ones) so the
// week-over-week structural trend is preserved.
function handleScalingWrite_(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('scaling_log');
  if (!sheet) {
    sheet = ss.insertSheet('scaling_log');
    sheet.appendRow([
      'date', 'vertical',
      'classification', 'confidence',
      'elasticity_r', 'ic_rate', 'cpicp', 'spend_share_pct',
      'avg_frequency', 'frequency_trend', 'cpm_trend',
      'new_audience_needed',
      'weeks_with_conversions',
      'contributed_to_pool', 'received_from_pool',
      'recorded_at'
    ]);
  }

  var rows = [];
  var p = (e && e.parameter) || {};

  if (e && e.postData && e.postData.contents) {
    try {
      var parsed = JSON.parse(e.postData.contents);
      if (parsed && Array.isArray(parsed.rows)) rows = parsed.rows;
    } catch (parseErr) {
      // fall through
    }
  }
  if (!rows.length && p.rows) {
    try {
      rows = JSON.parse(p.rows);
      if (!Array.isArray(rows)) {
        return jsonResponse_({ error: 'rows must be a JSON array' });
      }
    } catch (err) {
      return jsonResponse_({ error: 'rows is not valid JSON: ' + err.message });
    }
  }
  if (!rows.length) {
    return jsonResponse_({ error: 'no rows provided (use JSON body {rows:[...]} or rows=<json>)' });
  }

  var nowIso = new Date().toISOString();
  var written = 0;
  for (var ri = 0; ri < rows.length; ri++) {
    var row = rows[ri] || {};
    if (!row.vertical) continue;
    sheet.appendRow([
      String(row.date || ''),
      String(row.vertical),
      String(row.classification || ''),
      String(row.confidence || ''),
      row.elasticity_r == null ? '' : Number(row.elasticity_r),
      row.ic_rate == null ? '' : Number(row.ic_rate),
      row.cpicp == null ? '' : Number(row.cpicp),
      row.spend_share_pct == null ? '' : Number(row.spend_share_pct),
      row.avg_frequency == null ? '' : Number(row.avg_frequency),
      String(row.frequency_trend || ''),
      String(row.cpm_trend || ''),
      row.new_audience_needed === true ? 'TRUE' : 'FALSE',
      Number(row.weeks_with_conversions) || 0,
      row.contributed_to_pool === true ? 'TRUE' : 'FALSE',
      row.received_from_pool === true ? 'TRUE' : 'FALSE',
      nowIso
    ]);
    written++;
  }
  return jsonResponse_({ ok: true, written: written });
}


// Read-only: returns budget_queue rows whose `analysis_date` is on or
// after `since` (YYYY-MM-DD). Unlike `budget-queue-read` (which filters
// to `pending` only), this returns ALL statuses — the caller filters
// to `executed` for headroom math, or inspects `signal_reasons` for
// knockdown attribution. Optional `campaign_id` narrows the result.
function getScalingQueueRows_(params) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(BUDGET_SHEET);
  if (!sheet || sheet.getLastRow() < 2) return { rows: [], count: 0 };

  // Schema (writeToQueue_, Code.js:3150):
  //   0:token 1:created_at 2:analysis_date 3:execution_scheduled
  //   4:campaign_id 5:campaign_name
  //   6:current_budget_cents 7:proposed_budget_cents
  //   8:change_cents 9:change_pct
  //   10:signal_reasons 11:status 12:source

  var sinceStr = params && params.since
    ? String(params.since).trim().substring(0, 10)
    : null;
  var filterCampaignId = params && params.campaign_id
    ? String(params.campaign_id).trim() : null;
  var filterSource = params && params.source
    ? String(params.source).trim().toLowerCase() : null;

  var data = sheet.getDataRange().getValues();
  var rows = [];
  for (var i = 1; i < data.length; i++) {
    var r = data[i];

    var analysisDate = r[2];
    var dateStr = (analysisDate instanceof Date)
      ? Utilities.formatDate(analysisDate, Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : String(analysisDate).substring(0, 10);
    if (sinceStr && dateStr < sinceStr) continue;

    var campaignId = String(r[4]).trim();
    if (filterCampaignId && campaignId !== filterCampaignId) continue;

    var rowSource = r.length > 12 && r[12]
      ? String(r[12]).toLowerCase() : 'optimizer';
    if (filterSource && rowSource !== filterSource) continue;

    var changeCents = Number(r[8]) || 0;
    rows.push({
      token: String(r[0]),
      created_at: r[1] instanceof Date ? r[1].toISOString() : String(r[1]),
      analysis_date: dateStr,
      execution_scheduled: String(r[3]),
      campaign_id: campaignId,
      campaign_name: String(r[5]),
      current_budget_cents: Number(r[6]) || 0,
      proposed_budget_cents: Number(r[7]) || 0,
      change_cents: changeCents,
      change_pct: Number(r[9]) || 0,
      direction: changeCents > 0 ? 'increase' : (changeCents < 0 ? 'decrease' : 'flat'),
      signal_reasons: String(r[10]),
      status: String(r[11]),
      source: rowSource
    });
  }
  return { rows: rows, count: rows.length, since: sinceStr };
}


// Read-only: returns scaling_log rows (most recent first), optionally
// filtered by `since` (YYYY-MM-DD) and/or `vertical`. Surfaced to the
// Hive Mind chat handler so the system prompt can pull historical
// scaling classifications on demand without inflating every chat turn's
// context. See doGet system prompt addition for invocation guidance.
function getScalingLogRows_(params) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('scaling_log');
  if (!sheet || sheet.getLastRow() < 2) {
    return { rows: [], count: 0 };
  }

  var sinceStr = params && params.since
    ? String(params.since).trim().substring(0, 10) : null;
  var filterVertical = params && params.vertical
    ? String(params.vertical).trim().toLowerCase() : null;
  var limit = params && params.limit ? parseInt(params.limit) : 200;
  if (!(limit > 0 && limit <= 1000)) limit = 200;

  var data = sheet.getDataRange().getValues();
  var headers = data[0].map(function (h) { return String(h).trim(); });
  // Schema (handleScalingWrite_):
  //   0:date 1:vertical 2:classification 3:confidence
  //   4:elasticity_r 5:ic_rate 6:cpicp 7:spend_share_pct
  //   8:avg_frequency 9:frequency_trend 10:cpm_trend
  //   11:new_audience_needed 12:weeks_with_conversions
  //   13:contributed_to_pool 14:received_from_pool 15:recorded_at

  var rows = [];
  for (var i = data.length - 1; i >= 1; i--) {  // newest first
    var r = data[i];
    var dateStr = (r[0] instanceof Date)
      ? Utilities.formatDate(r[0], Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : String(r[0]).substring(0, 10);
    if (sinceStr && dateStr < sinceStr) continue;
    var v = String(r[1]).trim().toLowerCase();
    if (filterVertical && v !== filterVertical) continue;
    rows.push({
      date: dateStr,
      vertical: v,
      classification: String(r[2]),
      confidence: String(r[3]),
      elasticity_r: r[4] === '' ? null : Number(r[4]),
      ic_rate: r[5] === '' ? null : Number(r[5]),
      cpicp: r[6] === '' ? null : Number(r[6]),
      spend_share_pct: r[7] === '' ? null : Number(r[7]),
      avg_frequency: r[8] === '' ? null : Number(r[8]),
      frequency_trend: String(r[9]),
      cpm_trend: String(r[10]),
      new_audience_needed: String(r[11]).toUpperCase() === 'TRUE',
      weeks_with_conversions: Number(r[12]) || 0,
      contributed_to_pool: String(r[13]).toUpperCase() === 'TRUE',
      received_from_pool: String(r[14]).toUpperCase() === 'TRUE',
      recorded_at: r[15] instanceof Date
        ? r[15].toISOString() : String(r[15])
    });
    if (rows.length >= limit) break;
  }
  return { rows: rows, count: rows.length };
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

  var headers = data[0].map(function (h) {
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
  var endStr = end || '2999-12-31';

  var rows = [];
  for (var i = 1; i < data.length; i++) {
    var dateStr = dateToYMD_(data[i][0]);
    if (!dateStr || dateStr < startStr || dateStr > endStr) continue;

    rows.push({
      date: dateStr,
      month: String(data[i][1] || ''),
      week: Number(data[i][2]) || 0,
      campaign_name: String(data[i][3] || ''),
      campaign_id: String(data[i][4] || ''),
      impressions: Number(data[i][5]) || 0,
      clicks: Number(data[i][6]) || 0,
      spend: Number(data[i][7]) || 0,
      reach: Number(data[i][8]) || 0,
      conversions: Number(data[i][9]) || 0,
      frequency: Number(data[i][10]) || 0,
      cpl: (data[i][11] === '' || data[i][11] == null) ? null : Number(data[i][11]),
      // IC Conversions live in column 12 (0-indexed). Defaults
      // to 0 for older rows that predate the column.
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
    generated_at: row[0] instanceof Date ? row[0].toISOString() : String(row[0]),
    reporting_week: row[1] instanceof Date
      ? Utilities.formatDate(row[1], Session.getScriptTimeZone(), 'yyyy-MM-dd')
      : String(row[1]),
    total_spend: Number(row[2]) || 0,
    total_icps: Number(row[3]) || 0,
    overall_cpicp: String(row[4] || ''),
    narrative: String(row[6] || '')
  };
}


// ─── SUMMARY (aggregated totals for a date range) ───────────
// Server-side aggregation so the client can show topline
// numbers without pulling every row. The client also computes
// totals itself from /daily, so this is optional but cheap.
function getSummary_(start, end) {
  var tz = Session.getScriptTimeZone();
  if (!end) end = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  if (!start) {
    var s = new Date();
    s.setDate(s.getDate() - 30);
    start = Utilities.formatDate(s, tz, 'yyyy-MM-dd');
  }

  var daily = getDailyData_(start, end);
  var totals = { spend: 0, conversions: 0, clicks: 0, impressions: 0 };
  daily.forEach(function (r) {
    totals.spend += r.spend;
    totals.conversions += r.conversions;
    totals.clicks += r.clicks;
    totals.impressions += r.impressions;
  });

  return {
    start: start,
    end: end,
    rows: daily.length,
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
    var id = String(data[i][4] || '').trim();
    var date = dateToYMD_(data[i][0]);
    if (!name || !id) continue;
    if (!byCampaign[id] || date > byCampaign[id].last_active) {
      byCampaign[id] = byCampaign[id] || { campaign_id: id, campaign_name: name, last_active: date };
      byCampaign[id].last_active = date;
    }
  }
  return Object.keys(byCampaign).map(function (k) { return byCampaign[k]; });
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
//   4. Calls Claude (ANTHROPIC_MODEL) with system + history + user
//   5. Returns { reply: '...' } as JSON

function doPost(e) {
  var action = e && e.parameter && e.parameter.action;

  if (action === 'chat') {
    return handleChatRequest_(e);
  }
  // Agent skills can POST bulk payloads (a JSON array of rows) instead
  // of cramming everything into the query string. Same handler for GET
  // and POST so the skill can pick whichever fits.
  if (action === 'health-write') {
    return handleHealthWrite_(e);
  }
  if (action === 'daily-check-write') {
    return handleDailyCheckWrite_(e);
  }
  if (action === 'fatigue-write') {
    return handleFatigueWrite_(e);
  }
  if (action === 'creative-intelligence-write') {
    return handleCreativeIntelligenceWrite_(e);
  }
  if (action === 'scaling-write') {
    return handleScalingWrite_(e);
  }
  // Tuesday-morning agent workflow POSTs the strategic reallocation
  // proposal here. Returns a token; the agent embeds the token in the
  // approve/reject URLs of the Slack brief.
  if (action === 'scaling-queue-write') {
    return handleScalingQueueWrite_(e);
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

    // Conditionally augment the context with the latest scaling_log rows
    // when the user's question is structural in nature. Per directive B,
    // we don't bake these into every chat turn — only when the keywords
    // suggest the question needs structural capacity context.
    var msgLower = userMessage.toLowerCase();
    var SCALING_KEYWORDS = [
      'scaling', 'scalable', 'saturating', 'saturated',
      'over-invested', 'overinvested', 'elasticity',
      'headroom', 'capacity', 'classification',
      'structural', 'reallocation', 'audience-needed'
    ];
    var needsScalingContext = SCALING_KEYWORDS.some(function (kw) {
      return msgLower.indexOf(kw) >= 0;
    });
    if (needsScalingContext) {
      try {
        var scalingResp = getScalingLogRows_({ limit: 30 });
        if (scalingResp && scalingResp.rows && scalingResp.rows.length > 0) {
          // Group by date so the model sees the most recent week as a
          // single block, then prior weeks for trend.
          var scalingLines = scalingResp.rows.map(function (r) {
            return r.date + ' | ' + r.vertical + ' | ' + r.classification +
              ' (' + r.confidence + ')' +
              (r.elasticity_r != null ? ' r=' + r.elasticity_r : '') +
              (r.cpicp != null ? ' cpicp=$' + r.cpicp : '') +
              (r.new_audience_needed ? ' new-audience-needed' : '');
          });
          contextBlock += '\n\n' +
            '──────────────────────────────────────────\n' +
            'SCALING LOG (last ~30 vertical-classifications, newest first):\n' +
            '──────────────────────────────────────────\n' +
            scalingLines.join('\n');
        }
      } catch (scalingErr) {
        Logger.log('chat: scaling-log injection failed: ' + scalingErr.message);
      }
    }

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
      '  CPICP = Total Meta spend ÷ Estimated ICPs, where "Estimated ICPs" uses a hybrid attribution model (v3):',
      '    Estimated ICPs per campaign = (Meta IC conversions for that campaign, deduplicated)',
      "                                  + (that campaign's proportional share of the unattributed ICP pool,",
      '                                     weighted by its share of Meta conversion volume)',
      '',
      'Why hybrid attribution: IC conversions from Meta are the foundation — each ICP decision is attributed to exactly one campaign (last-click, deduplicated). The unattributed pool (HubSpot ICPs not traceable to any Meta campaign) is distributed proportionally by Meta conversion volume, capturing ICPs from organic, email, or lost-UTM paths.',
      '',
      'Lower CPICP is better. The team has a rough sense that CPICP under $120 is healthy and CPICP above $200 warrants investigation.',
      '',
      'SECONDARY METRICS (know what they mean, use them as supporting evidence, not headline):',
      '- Blended CPICP: same as CPICP above — "blended" just emphasizes the hybrid attribution model',
      '- Attributed CPICP: spend ÷ hard UTM-matched ICPs only. Less inclusive.',
      '- Attribution Rate: share of estimated ICPs backed by direct IC conversions from Meta.',
      '- CPL: Cost per Lead using Meta-reported conversions (spend ÷ Meta conversions). Less accurate than CPICP.',
      '- CTR: click-through rate. Creative quality signal.',
      '- Frequency: avg ad exposures per unique reach. Above 3.5 = audience saturation risk.',
      '',
      'DAILY DATA (available in the context block below):',
      'The context includes the last 30 days of daily per-campaign performance data (spend, impressions, clicks, conversions, IC conversions) plus a daily portfolio summary. Use this data to answer questions about recent daily trends, yesterday\'s performance, day-over-day changes, and intra-week patterns. For longer-term analysis (multi-week trends, CPICP, attribution), prefer the weekly rollup data which includes estimated_icps and attribution metrics that daily data does not have.',
      '',
      'PORTFOLIO SCALING DATA (conditionally appended below):',
      'When the user asks about scaling classifications (scalable / stable / saturating / over-invested), elasticity, vertical capacity / headroom, or structural questions about which verticals can absorb more spend, the SCALING LOG section will appear below the routine dashboard data. It carries per-vertical classification, confidence, elasticity_r, CPICP, and the new_audience_needed modifier across the last ~30 weekly rows (newest first). Use it to answer structural questions; do not infer scaling state from CPICP / spend trends alone when the SCALING LOG is present. If the section is absent, the question wasn\'t flagged as structural — answer using the routine data.',
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
    history.forEach(function (m) {
      if (m && (m.role === 'user' || m.role === 'assistant') && m.content) {
        messages.push({ role: m.role, content: String(m.content) });
      }
    });
    messages.push({ role: 'user', content: userMessage });

    var response = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
      },
      payload: JSON.stringify({
        model: ANTHROPIC_MODEL,
        max_tokens: 1500,
        system: systemPrompt,
        messages: messages
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
    rows.forEach(function (r) {
      lines.push(r.map(function (v) {
        if (v instanceof Date) {
          return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
        }
        return v === null || v === undefined ? '' : v;
      }).join('\t'));
    });
    lines.push('');
  }

  // ── Daily per-campaign data (last 30 days) ─────────
  var metaSheet = ss.getSheetByName(META_SHEET);
  if (metaSheet && metaSheet.getLastRow() > 1) {
    var tz = Session.getScriptTimeZone();
    var cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - 30);
    var cutoffStr = Utilities.formatDate(cutoff, tz, 'yyyy-MM-dd');

    var metaData = metaSheet.getDataRange().getValues();
    // Columns: 0=Date 1=Month 2=Week 3=CampaignName 4=CampaignID
    //   5=Impressions 6=Clicks 7=Spend 8=Reach 9=Conversions
    //   10=Frequency 11=CPL 12=IC Conversions
    var dailyRows = [];
    var portfolioByDate = {};

    for (var di = 1; di < metaData.length; di++) {
      var dateStr = metaData[di][0] instanceof Date
        ? Utilities.formatDate(metaData[di][0], tz, 'yyyy-MM-dd')
        : String(metaData[di][0]);
      if (!dateStr || dateStr < cutoffStr) continue;

      var spend = Number(metaData[di][7]) || 0;
      var impressions = Number(metaData[di][5]) || 0;
      var clicks = Number(metaData[di][6]) || 0;
      var conversions = Number(metaData[di][9]) || 0;
      var icConv = Number(metaData[di][12]) || 0;
      var campaignName = String(metaData[di][3] || '');

      dailyRows.push(
        dateStr + '\t' + campaignName + '\t' +
        spend + '\t' + impressions + '\t' + clicks + '\t' +
        conversions + '\t' + icConv
      );

      if (!portfolioByDate[dateStr]) {
        portfolioByDate[dateStr] = {
          spend: 0, conversions: 0, icConv: 0,
          impressions: 0, clicks: 0, campaigns: 0
        };
      }
      var pd = portfolioByDate[dateStr];
      pd.spend += spend;
      pd.conversions += conversions;
      pd.icConv += icConv;
      pd.impressions += impressions;
      pd.clicks += clicks;
      pd.campaigns++;
    }

    if (dailyRows.length > 0) {
      lines.push('DAILY PERFORMANCE (last 30 days, ' + dailyRows.length + ' rows, tab-separated):');
      lines.push('date\tcampaign_name\tspend\timpressions\tclicks\tconversions\tic_conversions');
      dailyRows.forEach(function (r) { lines.push(r); });
      lines.push('');
    }

    var sortedDates = Object.keys(portfolioByDate).sort();
    if (sortedDates.length > 0) {
      lines.push('DAILY PORTFOLIO SUMMARY (last 30 days, ' + sortedDates.length + ' days):');
      lines.push('date\ttotal_spend\ttotal_conversions\ttotal_ic_conversions\ttotal_impressions\ttotal_clicks\tcampaign_count');
      sortedDates.forEach(function (d) {
        var p = portfolioByDate[d];
        lines.push(
          d + '\t' + p.spend + '\t' + p.conversions + '\t' +
          p.icConv + '\t' + p.impressions + '\t' + p.clicks + '\t' +
          p.campaigns
        );
      });
      lines.push('');
    }
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


// ============================================================
// AUDIT SNAPSHOT EXPORT
// ============================================================
// Exports key sheets as JSON to the `audit-snapshots` branch in
// the GitHub repo. Run manually from Apps Script editor or on a
// weekly time trigger. Claude Code reads the snapshots to audit
// pipeline data without needing direct Sheets access.
//
// Setup (one-time):
//   1. Create a fine-grained GitHub PAT at
//      https://github.com/settings/tokens?type=beta
//      Scope: Contents (read/write) on
//      tylerhoneycomb/marketing-claude-honeycomb only.
//   2. In Apps Script editor: File → Project properties →
//      Script properties → Add: GITHUB_PAT = <your token>

function exportAuditSnapshot() {
  Logger.log('=== exportAuditSnapshot ===');
  var pat = PROPS.getProperty('GITHUB_PAT');
  if (!pat) {
    Logger.log('ERROR: GITHUB_PAT not set in Script Properties.');
    Logger.log('See setup instructions in Code.js above exportAuditSnapshot.');
    return;
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tz = Session.getScriptTimeZone();
  var dateStr = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  var owner = 'tylerhoneycomb';
  var repo = 'marketing-claude-honeycomb';
  var branch = 'audit-snapshots';

  var sheetConfigs = [
    { name: META_SHEET, maxDays: 90 },
    { name: ROLLUP_SHEET },
    { name: INTEL_SHEET },
    { name: MAPPING_SHEET }
  ];

  var files = {};
  var summarySheets = {};

  sheetConfigs.forEach(function (config) {
    var sheet = ss.getSheetByName(config.name);
    if (!sheet || sheet.getLastRow() < 1) {
      Logger.log('  Skipping empty/missing sheet: ' + config.name);
      return;
    }

    var data = sheet.getDataRange().getValues();
    var headers = data[0].map(function (h) { return String(h).trim(); });

    var cutoff = null;
    if (config.maxDays) {
      cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - config.maxDays);
    }

    var rows = [];
    for (var i = 1; i < data.length; i++) {
      if (cutoff && data[i][0] instanceof Date && data[i][0] < cutoff) continue;

      var obj = {};
      for (var j = 0; j < headers.length; j++) {
        var val = data[i][j];
        if (val instanceof Date) {
          val = Utilities.formatDate(val, tz, "yyyy-MM-dd'T'HH:mm:ss");
        }
        obj[headers[j]] = val;
      }
      rows.push(obj);
    }

    files['snapshots/' + config.name + '.json'] = JSON.stringify({
      sheet: config.name,
      exported_at: new Date().toISOString(),
      row_count: rows.length,
      total_rows_in_sheet: data.length - 1,
      columns: headers,
      data: rows
    }, null, 2);

    summarySheets[config.name] = {
      row_count: rows.length,
      total_rows: data.length - 1,
      columns: headers.length
    };
    Logger.log('  ' + config.name + ': ' + rows.length + ' rows');
  });

  files['snapshots/_manifest.json'] = JSON.stringify({
    exported_at: new Date().toISOString(),
    exported_by: 'exportAuditSnapshot',
    sheets: summarySheets
  }, null, 2);

  if (pushSnapshotToGitHub_(owner, repo, branch, files, 'audit snapshot ' + dateStr, pat)) {
    Logger.log('Snapshot pushed to branch "' + branch + '": ' + Object.keys(files).length + ' files');
  }
  Logger.log('=== exportAuditSnapshot complete ===');
}


function pushSnapshotToGitHub_(owner, repo, branch, files, message, pat) {
  var baseUrl = 'https://api.github.com/repos/' + owner + '/' + repo;
  var headers = {
    'Authorization': 'token ' + pat,
    'Accept': 'application/vnd.github.v3+json',
    'User-Agent': 'HoneycombAdsScript'
  };

  function ghFetch(path, opts) {
    opts = opts || {};
    opts.headers = headers;
    opts.muteHttpExceptions = true;
    var url = path.indexOf('http') === 0 ? path : baseUrl + path;
    var resp = UrlFetchApp.fetch(url, opts);
    var code = resp.getResponseCode();
    if (code >= 400) {
      Logger.log('GitHub API error (' + code + ') ' + (opts.method || 'GET') +
        ' ' + path + ': ' + resp.getContentText().substring(0, 200));
      return null;
    }
    return JSON.parse(resp.getContentText());
  }

  var ref = ghFetch('/git/ref/heads/' + branch);
  if (!ref) {
    Logger.log('  Branch "' + branch + '" not found, creating from main...');
    var mainRef = ghFetch('/git/ref/heads/main');
    if (!mainRef) { Logger.log('ERROR: Could not read main branch.'); return false; }
    ref = ghFetch('/git/refs', {
      method: 'POST', contentType: 'application/json',
      payload: JSON.stringify({ ref: 'refs/heads/' + branch, sha: mainRef.object.sha })
    });
    if (!ref) { Logger.log('ERROR: Could not create branch.'); return false; }
  }

  var parentSha = ref.object.sha;

  var commit = ghFetch('/git/commits/' + parentSha);
  if (!commit) return false;
  var baseTreeSha = commit.tree.sha;

  var treeEntries = [];
  var paths = Object.keys(files);
  for (var i = 0; i < paths.length; i++) {
    var blob = ghFetch('/git/blobs', {
      method: 'POST', contentType: 'application/json',
      payload: JSON.stringify({ content: files[paths[i]], encoding: 'utf-8' })
    });
    if (!blob) return false;
    treeEntries.push({ path: paths[i], mode: '100644', type: 'blob', sha: blob.sha });
    Logger.log('  blob: ' + paths[i] + ' (' + files[paths[i]].length + ' bytes)');
  }

  var tree = ghFetch('/git/trees', {
    method: 'POST', contentType: 'application/json',
    payload: JSON.stringify({ base_tree: baseTreeSha, tree: treeEntries })
  });
  if (!tree) return false;

  var newCommit = ghFetch('/git/commits', {
    method: 'POST', contentType: 'application/json',
    payload: JSON.stringify({ message: message, tree: tree.sha, parents: [parentSha] })
  });
  if (!newCommit) return false;

  var updated = ghFetch('/git/refs/heads/' + branch, {
    method: 'PATCH', contentType: 'application/json',
    payload: JSON.stringify({ sha: newCommit.sha })
  });
  if (!updated) return false;

  Logger.log('  Commit: ' + newCommit.sha.substring(0, 7) + ' → ' + branch);
  return true;
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


// ─── SPEND TARGET APPROVAL FLOW (two-step, Slack-safe) ──────
// Same pattern as the budget proposal approval: bare approve/
// reject URLs return an HTML confirmation page. Slack's
// link-unfurling crawler visits those and stops. Only a human
// clicking the "Confirm" button triggers the actual state write.

function showTargetApprovalPage_(e, decision) {
  var token = e && e.parameter && e.parameter.token;
  if (!token) {
    return HtmlService.createHtmlOutput('<h2>Invalid link.</h2><p>Missing token.</p>');
  }

  var pendingToken = PROPS.getProperty('SPEND_TARGET_PENDING_TOKEN');
  if (!pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>No pending spend target change.</h2>' +
      '<p>This proposal may have already been actioned or expired.</p>');
  }
  if (token !== pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>Token mismatch.</h2>' +
      '<p>This link is invalid or has already been used.</p>');
  }

  var pendingTarget    = PROPS.getProperty('PENDING_SPEND_TARGET');
  var pendingTolerance = PROPS.getProperty('PENDING_SPEND_TOLERANCE');
  var currentTarget    = getTargetWeeklySpend_();
  var currentTolerance = getWeeklySpendTolerance_();

  var isApprove = decision === 'approve';
  var color     = isApprove ? '#10b981' : '#ef4444';
  var label     = isApprove ? 'APPROVE' : 'REJECT';
  var description = isApprove
    ? 'This will change the weekly spend target from $' + currentTarget +
      ' to $' + pendingTarget + '. The optimizer will use the new target on its next run.'
    : 'This will cancel the proposed spend target change. The current target ($' +
      currentTarget + '/week) will remain in effect.';

  var baseUrl    = WEB_APP_URL || ScriptApp.getService().getUrl();
  var confirmUrl = baseUrl + '?action=confirm_' + decision + '_target&token=' + token;

  var html =
    '<!doctype html><html><head>' +
    '<meta charset="utf-8">' +
    '<title>Honeycomb Spend Target \u2014 Confirm</title>' +
    '<meta name="robots" content="noindex">' +
    '<style>' +
    'body{font-family:-apple-system,system-ui,"Segoe UI",sans-serif;max-width:560px;margin:60px auto;padding:24px;color:#1c1917;}' +
    'h1{font-size:22px;margin-bottom:8px;}' +
    'p{line-height:1.55;color:#57534e;}' +
    '.detail{margin:16px 0;padding:12px 16px;background:#f5f5f4;border-radius:6px;font-size:14px;line-height:1.6;}' +
    '.btn{display:inline-block;padding:14px 28px;color:white;text-decoration:none;' +
    'border-radius:8px;font-weight:600;font-size:15px;background:' + color + ';margin-top:16px;}' +
    '.btn:hover{opacity:.92}' +
    '.note{margin-top:24px;padding:12px 16px;background:#fef3c7;border:1px solid #fde68a;' +
    'border-radius:6px;font-size:13px;color:#78350f;line-height:1.5;}' +
    '</style></head><body>' +
    '<h1>\ud83d\udc1d Honeycomb Spend Target \u2014 Confirm ' + label + '</h1>' +
    '<div class="detail">' +
    '<strong>Current target:</strong> $' + currentTarget + '/week (\u00b1$' + currentTolerance + ')<br>' +
    '<strong>Proposed target:</strong> $' + pendingTarget + '/week' +
    (pendingTolerance ? ' (\u00b1$' + pendingTolerance + ')' : '') +
    '</div>' +
    '<p>' + description + '</p>' +
    '<a class="btn" href="' + confirmUrl + '" target="_top">Click to confirm ' + label + '</a>' +
    '<div class="note">This extra confirmation step exists so that link previews (like Slack unfurl) ' +
    'can\'t accidentally approve or reject changes on your behalf.</div>' +
    '</body></html>';

  return HtmlService.createHtmlOutput(html);
}


function applyTargetDecision_(e, decision) {
  var token = e && e.parameter && e.parameter.token;
  if (!token) {
    return HtmlService.createHtmlOutput('<h2>Invalid link.</h2><p>Missing token.</p>');
  }

  var pendingToken = PROPS.getProperty('SPEND_TARGET_PENDING_TOKEN');
  if (!pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>No pending spend target change.</h2>' +
      '<p>This proposal may have already been actioned or expired.</p>');
  }
  if (token !== pendingToken) {
    return HtmlService.createHtmlOutput(
      '<h2>Token mismatch.</h2>' +
      '<p>This link is invalid or has already been used.</p>');
  }

  var pendingTarget    = PROPS.getProperty('PENDING_SPEND_TARGET');
  var pendingTolerance = PROPS.getProperty('PENDING_SPEND_TOLERANCE');
  var user = Session.getActiveUser().getEmail() || 'unknown user';

  // Clean up pending state regardless of decision.
  PROPS.deleteProperty('SPEND_TARGET_PENDING_TOKEN');
  PROPS.deleteProperty('PENDING_SPEND_TARGET');
  PROPS.deleteProperty('PENDING_SPEND_TOLERANCE');
  PROPS.deleteProperty('PENDING_SPEND_TARGET_AT');

  if (decision === 'approve') {
    // Apply the new target.
    if (pendingTarget) {
      PROPS.setProperty('DASHBOARD_TARGET_WEEKLY_SPEND', pendingTarget);
    }
    if (pendingTolerance) {
      PROPS.setProperty('DASHBOARD_WEEKLY_SPEND_TOLERANCE', pendingTolerance);
    }
    var newTarget    = getTargetWeeklySpend_();
    var newTolerance = getWeeklySpendTolerance_();

    postToSlack_('*Honeycomb Spend Target* \u2705 Approved by ' + user +
      '. New target: $' + newTarget + '/week (\u00b1$' + newTolerance +
      '). Takes effect on the next budget optimization run.');

    return HtmlService.createHtmlOutput(
      '<h2>\u2705 Spend target updated.</h2>' +
      '<p>New target: <strong>$' + newTarget + '/week</strong> (\u00b1$' + newTolerance + ').</p>' +
      '<p>The budget optimizer will use this target on its next run.</p>');
  }

  if (decision === 'reject') {
    postToSlack_('*Honeycomb Spend Target* \u274c Rejected by ' + user +
      '. No changes to the spend target.');

    return HtmlService.createHtmlOutput(
      '<h2>\u274c Spend target change rejected.</h2>' +
      '<p>The current target remains unchanged.</p>');
  }

  return HtmlService.createHtmlOutput('<h2>Unknown decision.</h2>');
}


// ───────────────────────────── END PASTE ─────────────────────────────