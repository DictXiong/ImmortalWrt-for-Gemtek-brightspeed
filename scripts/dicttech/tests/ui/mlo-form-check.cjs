'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, 'candidate/www/luci-static/resources/view/mlo.js'), 'utf8').replace(/\n\s*refreshRuntime\s*=\s*function\(nodes\)\s*\{/, '\nrefreshRuntime=function(nodes){');
const start = source.indexOf('{\n    const widthsByBand =');
const end = source.indexOf('\nrefreshRuntime=function(nodes){', start);
assert(start >= 0 && end > start);
const controls = [];
const radios = [
  { '.name': 'radio0', band: '2g', htmode: 'EHT40' },
  { '.name': 'radio1', band: '5g', htmode: 'HE160' },
  { '.name': 'radio2', band: '6g', htmode: 'EHT320' },
  { '.name': 'unknown-radio', band: '60g' }
];
const context = vm.createContext({
  radios, form: { NamedSection: 'NamedSection', ListValue: 'ListValue' },
  _: value => value, radioLabel: radio => radio['.name'],
  uci: { get: (config, sid, key) => {
    assert.equal(config, 'wireless'); assert.equal(key, 'htmode');
    return radios.find(radio => radio['.name'] === sid).htmode;
  } },
  m: { section(type, sid, kind) {
    assert.equal(type, 'NamedSection'); assert.equal(kind, 'wifi-device');
    return { option(valueType, key) {
      assert.equal(valueType, 'ListValue'); assert.equal(key, 'htmode');
      const option = { sid, key, values: [], value(value, label) { this.values.push([value, label]); } };
      controls.push(option); return option;
    } };
  } }
});
vm.runInContext(source.slice(start, end), context);
assert.equal(controls.length, 3);
assert.deepEqual(controls[0].values.map(row => row[0]), ['EHT20', 'EHT40']);
assert.deepEqual(controls[1].values.map(row => row[0]), ['HE160', 'EHT20', 'EHT40', 'EHT80', 'EHT160']);
assert.deepEqual(controls[2].values.map(row => row[0]), ['EHT20', 'EHT40', 'EHT80', 'EHT160', 'EHT320']);
assert(controls.every(control => control.rmempty === false));
const report = { passed: true, offlineOnly: true, preservedExistingLegacyWidth: true,
  onlyWifiDeviceHtmodeTargets: true, controls: controls.map(({ sid, key, values }) => ({ sid, key, values })) };
fs.writeFileSync(path.join(__dirname, 'mlo-form-result.json'), JSON.stringify(report, null, 2) + '\n');
console.log('PASS: three independent radio width controls and existing HE160 preserved');
