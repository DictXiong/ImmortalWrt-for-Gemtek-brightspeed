'use strict';

// Local source checks only. This script never authenticates or makes requests.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'candidate/www/luci-static/resources/view/network/wireless.js'), 'utf8').replace(/var (CBIWifiTxPowerValue|CBIWifiCountryValue)\s*=\s*/g, 'var $1=');
const original = fs.readFileSync(path.join(__dirname, 'original/www/luci-static/resources/view/network/wireless.js'), 'utf8');
const board = JSON.parse(fs.readFileSync(path.join(__dirname, 'board-capabilities.json'), 'utf8'));
// Synthetic subradio mapping; the public fixture contains capabilities only.
board.wlan.phy0.info.radios = Object.keys(board.wlan.phy0.info.bands)
  .map((band, index) => ({ index, bands: { [band]: {} } }));
const hwModes = ['a', 'b', 'g', 'n', 'ac', 'ax', 'be'];
const htModes = ['HT20', 'HT40', 'VHT20', 'VHT40', 'VHT80', 'VHT160',
  'HE20', 'HE40', 'HE80', 'HE160', 'EHT20', 'EHT40', 'EHT80', 'EHT160', 'EHT320'];
const frequencies = [
  { band: 2, channel: 1, mhz: 2412, flags: [] },
  { band: 2, channel: 11, mhz: 2462, restricted: true, flags: ['no_ir'], no_outdoor: true },
  { band: 5, channel: 36, mhz: 5180, flags: [], no_outdoor: true },
  { band: 6, channel: 37, mhz: 6135, flags: [] }
];
const L = {
  bind: (fn, self, ...args) => fn.bind(self, ...args), toArray: value => Array.isArray(value) ? value
    : value == null ? [] : String(value).trim().split(/\s+/),
  hasSystemFeature: () => true, resolveDefault: (promise, fallback) => Promise.resolve(promise).catch(() => fallback)
};
const json = value => JSON.parse(JSON.stringify(value));
const values = triples => triples.filter((_, index) => index % 3 === 0);
const available = triples => triples.filter((_, index) => index % 3 === 0 && triples[index + 2].available);
const sourceBetween = (text, start, end) => {
  const a = text.indexOf(start), b = text.indexOf(end, a);
  assert(a >= 0 && b > a, 'Required source boundaries must exist');
  return text.slice(a, b);
};

function radio(config, runtimePhy) {
  return { get: key => config[key], ubus: (...args) => args.at(-1) === 'phy' ? runtimePhy : null,
    getHWModes: () => hwModes, getHTModes: () => htModes };
}

async function frequencyContext(boardData, device, config, sid) {
  const writes = [];
  const context = vm.createContext({ L,
    form: { Value: { extend: options => options } },
    rpc: { declare: declaration => () => Promise.resolve(declaration.object === 'luci-rpc' ? boardData : frequencies) },
    network: { getWifiDevice: name => { assert.equal(name, sid); return Promise.resolve(device); } },
    uci: { get: (name, section, key) => {
      assert.equal(name, 'wireless'); assert.equal(section, sid); return config[key];
    }, set: (...args) => writes.push(args) },
    E: (_tag, attrs) => ({ value: attrs.value })
  });
  vm.runInContext("String.prototype.format=function(...args){let index=0;return this.replace(/%[ds]/g,()=>String(args[index++]));};", context);
  vm.runInContext(sourceBetween(source, 'const callWifiBoardJSON', 'var CBIWifiTxPowerValue=') +
    '\nthis.frequency=CBIWifiFrequencyValue;this.capabilities=getWifiRadioCapabilities;', context);
  await context.frequency.load(sid);
  assert.equal(writes.length, 0, 'Loading radio capabilities must not write UCI');
  return { context, option: context.frequency, writes };
}

function select() {
  let index = -1;
  const result = { options: [], parentNode: { style: {} },
    add(option) { this.options.push(option); if (index < 0) index = 0; } };
  Object.defineProperties(result, {
    selectedIndex: { get: () => index, set: value => { index = value >= 0 && value < result.options.length ? value : -1; } },
    value: { get: () => result.options[index]?.value ?? '',
      set: value => { index = result.options.findIndex(option => String(option.value) === String(value)); } }
  });
  return result;
}

function exerciseInitialValues(option, config, sid) {
  const controls = Object.fromEntries(['mode', 'band', 'channel', 'htmode'].map(name => ['.' + name, select()]));
  controls['.restricted_channel'] = { style: {} };
  option.map = { checkDepends() {} };
  option.setInitialValues(sid, { querySelector: name => controls[name] });
  assert.equal(controls['.band'].value, config.band, 'Initial band must be preserved');
  assert.equal(controls['.htmode'].value, config.htmode, 'Initial width must be preserved');
  assert.equal(String(controls['.channel'].value), config.channel, 'Initial channel must be preserved');
  return controls;
}

function section(sid) {
  const fields = [];
  const nested = {
    tab() {}, taboption(tab, type, name, title, description) {
      const option = { tab, type, name, title, description, default: type === 'Flag' ? '0' : null,
        rmempty: true, choices: [], deps: [],
        value(value, label) { this.choices.push([value, label]); },
        depends(dep) { this.deps.push(dep); } };
      fields.push(option); return option;
    }
  };
  return { section: sid, map: {}, fields, option() { return { subsection: nested }; } };
}

function builderChecks() {
  const types = Object.fromEntries(['SectionValue', 'NamedSection', 'DummyValue', 'Button', 'Flag', 'ListValue', 'Value']
    .map(name => [name, name]));
  const context = vm.createContext({ form: types, CBIWifiFrequencyValue: 'Frequency', CBIWifiTxPowerValue: 'Power',
    CBIWifiCountryValue: 'Country', _: value => value, L, ui: { createHandlerFn() {} },
    network_updown() {}, render_modal_status() {}, uci: { get: (_name, _sid, key) => key === 'type' ? 'mac80211' : undefined } });
  const modalStart = original.indexOf('s.addModalOptions');
  const hwtype = /const\s+hwtype\s*=/.exec(original.slice(modalStart));
  assert(modalStart >= 0 && hwtype, 'Native radio form must exist in the pinned source');
  const nativeStart = modalStart + hwtype.index;
  const interfaceStart = /o\s*=\s*s\.option\(form\.SectionValue,\s*'_device',\s*form\.NamedSection,\s*radioNet\.getName\(\),\s*'wifi-iface'/.exec(original.slice(nativeStart));
  assert(interfaceStart, 'Native interface form boundary must exist');
  const nativeEnd = nativeStart + interfaceStart.index;
  vm.runInContext('function nativeBuilder(s,radioNet){' + original.slice(nativeStart, nativeEnd) + '}\n' +
    sourceBetween(source, 'function addWifiDeviceOptions(', 'return view.extend(') +
    '\nthis.build=addWifiDeviceOptions;this.native=nativeBuilder;', context);
  const wifi = { get: () => undefined, getWifiDeviceName: () => 'renamed-radio' };
  const old = section('single-ssid'), same = section('single-ssid'), direct = section('renamed-radio');
  context.native(old, wifi); context.build(same, 'renamed-radio', wifi); context.build(direct, 'renamed-radio', null);
  const metadata = field => json(Object.fromEntries(Object.entries(field).filter(([key, value]) =>
    !['wifiNetwork', 'ucisection'].includes(key) && typeof value !== 'function')));
  const expected = old.fields.map(metadata);
  expected.find(field => field.name === 'noscan').deps.push({ _freq: '2g', '!contains': true });
  assert.deepEqual(same.fields.map(metadata), expected, 'Shared builder must preserve all original field metadata');
  const settings = same.fields.filter(field => !['_wifistat_modal', '_toggle'].includes(field.name));
  assert.equal(settings.length, 13); assert.equal(direct.fields.length, 13);
  assert.deepEqual(direct.fields.map(metadata), settings.map(metadata), 'Both entry points must build identical settings');
  assert.equal(direct.fields.find(field => field.name === '_freq').ucisection, 'renamed-radio');
  assert.equal((source.match(/function addWifiDeviceOptions\(/g) || []).length, 1);
  assert.equal((source.match(/addWifiDeviceOptions\(\s*s,\s*radioName,\s*deviceOnly\s*\?\s*null\s*:\s*radioNet\s*\);/g) || []).length, 1);
  assert(!source.includes('fixedBand')); assert(!source.includes('const maxWidth='));
  return { oneBuilder: true, settings: settings.map(field => field.name), defaultsAndValidationPreserved: true };
}

function wifiNetwork({ devices, mlo = '0', up = true, power = 23, offset = 2 }) {
  return { get: key => key === 'device' ? devices : key === 'mlo' ? mlo : undefined,
    isUp: () => up, getTXPower: () => power, getTXPowerOffset: () => offset };
}

async function powerChecks() {
  const single = wifiNetwork({ devices: 'renamed-radio' });
  const mld = wifiNetwork({ devices: ['renamed-radio', 'other-radio'], mlo: '1', power: 0 });
  const cases = [
    ['matching-single', [single], null, 23],
    ['multi-radio-mlo', [mld], mld, null],
    ['single-radio-mlo', [wifiNetwork({ devices: 'renamed-radio', mlo: '1', power: 0 })], null, null],
    ['wrong-radio', [wifiNetwork({ devices: 'another-radio' })], null, null],
    ['down-single', [wifiNetwork({ devices: 'renamed-radio', up: false })], null, null],
    ['mlo-then-matching-single', [mld, single], mld, 23]
  ];
  for (const [name, networks, preferred, expected] of cases) {
    const context = vm.createContext({ L, form: { ListValue: { extend: options => options, prototype: { load() {} } } },
      rpc: { declare: () => () => Promise.resolve([{ dbm: 20, mw: 100 }]) },
      network: { getWifiNetworks: () => Promise.resolve(networks) }, _: value => value });
    vm.runInContext(sourceBetween(source, 'var CBIWifiTxPowerValue=', 'var CBIWifiCountryValue=') +
      '\nthis.power=CBIWifiTxPowerValue;', context);
    context.power.value = () => {};
    context.power.wifiNetwork = preferred;
    await context.power.load('renamed-radio');
    assert.equal(context.power.powerval, expected, name);
    assert.equal(context.power.poweroff, expected === null ? null : 2, name + ' offset');
  }
  return { testedCases: cases.map(item => item[0]), mloSharedPowerNotDisplayed: true };
}

(async () => {
  const frequenciesTested = [];
  const boardPath = board.wlan.phy0.path;
  for (const [band, index, width] of [['2g', 0, 'EHT40'], ['5g', 1, 'EHT160'], ['6g', 2, 'EHT320']]) {
    const sid = 'arbitrary-name-' + index;
    const config = { phy: 'phy0', radio: String(index), band, channel: band === '2g' ? '1' : band === '5g' ? '36' : '37', htmode: width };
    const { option, writes } = await frequencyContext(board, radio(config), config, sid);
    assert.deepEqual(json(available(option.bands.be)), [band]);
    const widths = json(available(option.bandHTModes[band].be));
    assert(widths.includes(width));
    assert(widths.every(mode => board.wlan.phy0.info.bands[band.toUpperCase()].modes.includes(mode)));
    const controls = exerciseInitialValues(option, config, sid);
    assert.equal(writes.length, 0);
    option.write(sid, [width, band, config.channel]);
    assert.deepEqual(json(writes), [['wireless', sid, 'htmode', width], ['wireless', sid, 'band', band], ['wireless', sid, 'channel', config.channel]]);
    frequenciesTested.push({ sid, band, widths, initialWidth: controls['.htmode'].value });
  }
  for (const [name, config, runtimePhy, band] of [
    ['path-lookup', { path: boardPath, radio: 1, band: '5g', channel: '36', htmode: 'EHT160' }, undefined, '5g'],
    ['runtime-phy-lookup', { radio: 0, band: '2g', channel: '1', htmode: 'EHT40' }, 'phy0', '2g']
  ]) {
    const { option } = await frequencyContext(board, radio(config, runtimePhy), config, 'renamed-device');
    assert.deepEqual(json(available(option.bands.be)), [band], name);
  }
  const fallbackConfig = { band: '6g', channel: '37', htmode: 'EHT320' };
  const fallback = await frequencyContext({}, radio(fallbackConfig), fallbackConfig, 'no-board-device');
  assert.deepEqual(json(available(fallback.option.bands.be)), ['2g', '5g', '6g']);
  assert.deepEqual(json(available(fallback.option.bandHTModes['6g'].be)), htModes.filter(value => value.startsWith('EHT')));
  exerciseInitialValues(fallback.option, fallbackConfig, 'no-board-device');
  assert.equal(fallback.writes.length, 0);
  console.log(JSON.stringify({ passed: true, localOnly: true, networkRequests: 0,
    frequenciesTested, pathAndRuntimePhyLookup: true, noBoardFallback: true,
    builder: builderChecks(), power: await powerChecks() }, null, 2));
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
