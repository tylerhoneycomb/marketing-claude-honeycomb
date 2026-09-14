/**
 * Tests for the /exec shared-secret gate in apps-script/Code.js.
 *
 * The gate cannot be exercised in situ without a live Apps Script runtime,
 * so this extracts the guard block and evaluates it against stubbed
 * PROPS / Logger / jsonResponse_ globals. It pins the two properties that
 * matter: the gate fails CLOSED when the secret is unset or too short, and
 * read-only dashboard actions are never affected.
 *
 * Run: node apps-script/tests/exec-auth.test.js
 */
const fs = require('fs');
const path = require('path');

// Deliberately NOT under apps-script/: that directory has no
// .claspignore and .clasp.json sets skipSubdirectories:false, so every
// .js file there is uploaded into the live Apps Script project by
// `clasp push`. A test file using require()/module would break the
// deployed script.
const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'apps-script', 'Code.js'), 'utf8');

const start = SRC.indexOf('var EXEC_SECRET_PROP');
const end = SRC.indexOf('// WEB APP — APPROVAL HANDLER');
if (start === -1 || end === -1 || end <= start) {
  console.error('Could not locate the shared-secret gate block in Code.js.');
  console.error('If the block moved, update the markers in this test.');
  process.exit(1);
}
const BLOCK = SRC.slice(start, end);

let store = {};
const PROPS = { getProperty: (k) => store[k] || null };
const Logger = { log: () => {} };
const jsonResponse_ = (o) => ({ json: o });
eval(BLOCK);

const SECRET = 'a-sufficiently-long-secret-value';
let pass = 0;
const failures = [];

function check(name, got, want) {
  if (JSON.stringify(got) === JSON.stringify(want)) {
    pass++;
    console.log('  PASS  ' + name);
  } else {
    failures.push(name);
    console.log('  FAIL  ' + name +
      '\n        got  ' + JSON.stringify(got) +
      '\n        want ' + JSON.stringify(want));
  }
}
const refused = (e, a) => execAuthFailure_(e, a) !== null;

console.log('fails closed when the secret is unset');
store = {};
check('chat refused', refused({ parameter: {} }, 'chat'), true);
check('scaling-queue-write refused', refused({ parameter: {} }, 'scaling-queue-write'), true);
check('read action still allowed', execAuthFailure_({ parameter: {} }, 'rollup'), null);

console.log('fails closed when the secret is too short');
store = { EXEC_SHARED_SECRET: 'tooshort' };
check('refused even with a matching key',
  refused({ parameter: { key: 'tooshort' } }, 'chat'), true);

console.log('enforces the secret when set');
store = { EXEC_SHARED_SECRET: SECRET };
check('no key refused', refused({ parameter: {} }, 'chat'), true);
check('wrong key refused', refused({ parameter: { key: 'wrong-but-long-enough-x' } }, 'chat'), true);
check('correct key allowed', execAuthFailure_({ parameter: { key: SECRET } }, 'chat'), null);
check('correct key in POST body allowed',
  execAuthFailure_({ postData: { contents: JSON.stringify({ key: SECRET }) } },
    'scaling-queue-write'), null);
check('wrong key in POST body refused',
  refused({ postData: { contents: JSON.stringify({ key: 'x' }) } }, 'scaling-queue-write'), true);
check('non-JSON POST body refused without throwing',
  refused({ postData: { contents: 'not json at all' } }, 'health-write'), true);

console.log('leaves unprotected actions alone');
['rollup', 'daily', 'mappings', 'narrative', 'summary', 'campaigns',
 'get_spend_goal', 'get_campaign_budgets', 'rolling-latest-date',
 'budget-queue-read', 'scaling-queue-read', 'scaling-log-read',
 'confirm_approve_target', 'confirm_reject_target'].forEach((a) => {
  check(a + ' not gated', execAuthFailure_({ parameter: {} }, a), null);
});

console.log('constant-time comparison');
check('equal strings match', constantTimeEquals_(SECRET, SECRET), true);
check('different lengths differ', constantTimeEquals_('abc', 'abcd'), false);
check('same length differs', constantTimeEquals_('abcd', 'abce'), false);

console.log('');
if (failures.length) {
  console.log(failures.length + ' FAILED: ' + failures.join(', '));
  process.exit(1);
}
console.log('all ' + pass + ' checks passed');
