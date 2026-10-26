#!/usr/bin/env node
'use strict';

// Offline checks of prepared, locked source files. No router URL or credentials
// are accepted. Browser networking is blocked by the DOM harness.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync, spawnSync } = require('node:child_process');

function argumentsFor(argv) {
  const args = {
    root: path.resolve(__dirname, '../..'),
    playwright: process.env.PLAYWRIGHT_MODULE,
    browser: process.env.BROWSER_EXECUTABLE,
    requireBrowser: false
  };
  for (let i = 0; i < argv.length; i++) {
    const option = argv[i];
    if (option === '--help' || option === '-h') {
      console.log('usage: node scripts/dicttech/test-ap-ui.cjs [--source-root PREPARED_BUILD_ROOT] [--output REPORT.json] [--playwright-module MODULE_OR_PATH] [--browser-executable PATH] [--require-browser]');
      return null;
    }
    if (option === '--require-browser' || option === '--requirebrowser') {
      args.requireBrowser = true;
      continue;
    }
    const keys = { '--source-root': 'root', '--output': 'output',
      '--playwright-module': 'playwright', '--browser-executable': 'browser' };
    if (!keys[option] || !argv[i + 1] || argv[i + 1].startsWith('--'))
      throw new Error('Unknown option or missing value: ' + option);
    args[keys[option]] = argv[++i];
  }
  args.root = path.resolve(args.root);
  if (args.output) args.output = path.resolve(args.output);
  if (args.browser) args.browser = path.resolve(args.browser);
  return args;
}

function sha(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }

function pinnedBlob(directory, revision, file) {
  assert(/^[0-9a-f]{40}$/.test(revision), 'A full pinned feed revision is required');
  // Even a partial checkout must fail locally rather than fetch missing blobs.
  return execFileSync('git', ['-c', 'protocol.allow=never', '-C', directory,
    'show', revision + ':' + file], {
    env: { ...process.env, GIT_NO_LAZY_FETCH: '1', GIT_TERMINAL_PROMPT: '0' },
    maxBuffer: 8 * 1024 * 1024
  });
}

function browserDependency(args) {
  let loaded, modulePath;
  const choices = args.playwright ? [args.playwright] : ['playwright', 'playwright-core'];
  for (const name of choices) {
    try {
      modulePath = require.resolve(name, { paths: [args.root, __dirname, process.cwd()] });
      loaded = require(modulePath);
      break;
    } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND') throw error;
    }
  }
  if (!loaded) return { available: false, reason: 'Playwright module is not installed; set PLAYWRIGHT_MODULE or --playwright-module.' };
  assert(loaded.chromium && typeof loaded.chromium.launch === 'function', 'Playwright module must provide Chromium');
  const executable = args.browser || loaded.chromium.executablePath();
  try { fs.accessSync(executable, fs.constants.X_OK); }
  catch { return { available: false, reason: 'Chromium executable is missing; set BROWSER_EXECUTABLE or --browser-executable.' }; }
  return { available: true, modulePath, executable };
}

function stageFiles(args, stage) {
  const manifest = JSON.parse(fs.readFileSync(path.join(args.root, 'configs/dicttech-ui-manifest.json')));
  assert.equal(manifest.schema_version, 1);
  const definitions = [
    ['device-settings', 'feeds/luci', 'modules/luci-mod-network/htdocs/luci-static/resources/view/network/wireless.js', 'www/luci-static/resources/view/network/wireless.js', manifest.feed_luci_revision],
    ['dropdown-ui', 'feeds/luci', 'modules/luci-base/htdocs/luci-static/resources/ui.js', 'www/luci-static/resources/ui.js', manifest.feed_luci_revision],
    ['dropdown-theme', 'feeds/glass', 'htdocs/luci-static/glass/css/cascade.css', 'www/luci-static/glass/css/cascade.css', manifest.feed_glass_revision],
    ['mlo-widths', '', 'package/emortal/luci-app-mlo/htdocs/luci-static/resources/view/mlo.js', 'www/luci-static/resources/view/mlo.js', null]
  ];
  const sourceSHA = {};
  for (const [name, feed, file, destination, revision] of definitions) {
    const source = path.join(args.root, feed, file);
    if (!fs.existsSync(source))
      throw new Error('Prepared source is missing: ' + path.join(feed, file) + '. Run build-ap.sh --prepare-only first or pass --source-root.');
    const candidate = fs.readFileSync(source);
    assert.equal(sha(candidate), manifest.patches[name].patched_sha256, 'Unreviewed candidate source: ' + name);
    const target = path.join(stage, 'candidate', destination);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, candidate);
    sourceSHA[name] = sha(candidate);
    if (revision) {
      const baseline = pinnedBlob(path.join(args.root, feed), revision, file);
      assert.equal(sha(baseline), manifest.patches[name].source_sha256, 'Wrong pinned baseline: ' + name);
      const original = path.join(stage, 'original', destination);
      fs.mkdirSync(path.dirname(original), { recursive: true });
      fs.writeFileSync(original, baseline);
    }
  }
  const fixtures = path.join(__dirname, 'tests/ui');
  for (const name of ['independent-check.cjs', 'mlo-form-check.cjs', 'dropdown-offline-dom.cjs',
    'browser-widget-regressions.cjs', 'board-capabilities.json'])
    fs.copyFileSync(path.join(fixtures, name), path.join(stage, name));
  return sourceSHA;
}

function runHarness(stage, name, env = {}) {
  const result = spawnSync(process.execPath, [path.join(stage, name)], {
    cwd: stage, env: { ...process.env, ...env }, encoding: 'utf8',
    timeout: 120000, maxBuffer: 8 * 1024 * 1024
  });
  if (result.error || result.status !== 0)
    throw new Error(name + ' failed: ' + (result.error?.message || result.stderr || result.stdout).trim());
  return result;
}

function outputReport(report, destination) {
  const text = JSON.stringify(report, null, 2) + '\n';
  if (destination) {
    if (fs.existsSync(destination) && fs.lstatSync(destination).isSymbolicLink())
      throw new Error('Refusing a symbolic-link report output');
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    const temp = destination + '.' + process.pid + '.tmp';
    fs.writeFileSync(temp, text, { flag: 'wx', mode: 0o600 });
    fs.renameSync(temp, destination);
  }
  console.log(text.trimEnd());
}

async function main() {
  const args = argumentsFor(process.argv.slice(2));
  if (!args) return;
  const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'dicttech-ap-ui-'));
  const report = { schema_version: 1, offline_only: true, router_access: false,
    uci_transport_calls: 0, source_root: args.root, passed: false, complete: false, skipped: [] };
  try {
    report.sourceSHA = stageFiles(args, stage);
    report.syntax = [];
    for (const file of ['ui.js', 'view/network/wireless.js', 'view/mlo.js']) {
      const source = fs.readFileSync(path.join(stage, 'candidate/www/luci-static/resources', file), 'utf8');
      // LuCI modules use top-level return; parse inside a function without executing.
      new vm.Script('(function(){\n' + source + '\n})', { filename: file });
      report.syntax.push({ file, passed: true });
    }
    report.radio = JSON.parse(runHarness(stage, 'independent-check.cjs').stdout);
    runHarness(stage, 'mlo-form-check.cjs');
    report.mlo = JSON.parse(fs.readFileSync(path.join(stage, 'mlo-form-result.json')));
    const dependency = browserDependency(args);
    if (dependency.available) {
      runHarness(stage, 'dropdown-offline-dom.cjs', {
        PLAYWRIGHT_MODULE: dependency.modulePath, BROWSER_EXECUTABLE: dependency.executable
      });
      report.browser = JSON.parse(fs.readFileSync(path.join(stage, 'dropdown-offline-dom-result.json')));
      report.complete = true;
    } else {
      report.browser = { status: 'SKIP', reason: dependency.reason };
      report.skipped.push('desktop_and_touch_real_Chromium_dropdown_DOM');
    }
    report.passed = report.radio.passed && report.mlo.passed &&
      (dependency.available ? report.browser.passed : !args.requireBrowser);
    if (!report.passed) process.exitCode = 1;
  } catch (error) {
    report.failure = error.message;
    process.exitCode = 1;
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
  outputReport(report, args.output);
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
