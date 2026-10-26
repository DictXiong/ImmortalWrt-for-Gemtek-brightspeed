'use strict';

// Actual naoki UIDropdown source in an offline Chromium DOM. LuCI transport and
// UCI are absent: this cannot read or change router configuration.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const widgetRegressions = require('./browser-widget-regressions.cjs');
const here = __dirname;

function sourceBetween(source, start, end) {
  source = source.replace(/const\s+(UIElement|UITextfield|UIDropdown|UICombobox)\s*=\s*/g, 'const $1=');
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a);
  return source.slice(a, b);
}

async function install(page, directory) {
  const source = fs.readFileSync(path.join(here, directory, 'www/luci-static/resources/ui.js'), 'utf8');
  const css = fs.readFileSync(path.join(here, directory, 'www/luci-static/glass/css/cascade.css'), 'utf8');
  await page.setContent('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="modal_overlay"></div></body></html>');
  await page.addStyleTag({ content: css });
  await page.evaluate(() => {
    function extend(definition) {
      const Parent = this;
      function Type(...args) { if (this.__init__) this.__init__(...args); }
      Type.prototype = Object.create(Parent.prototype || {});
      Object.assign(Type.prototype, definition);
      Type.prototype.constructor = Type;
      Type.extend = extend;
      return Type;
    }
    const Parent = function() {};
    Parent.extend = extend;
    window.baseclass = Parent;
    const instances = new WeakMap();
    function append(node, children) {
      for (const child of Array.isArray(children) ? children : [children]) {
        if (child == null || child === false) continue;
        if (Array.isArray(child)) append(node, child);
        else node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    }
    window.E = function(tag, attrs, children) {
      let node;
      if (String(tag).startsWith('<')) {
        const template = document.createElement('template');
        template.innerHTML = tag;
        node = template.content.firstElementChild;
      } else node = document.createElement(tag);
      if (attrs && (Array.isArray(attrs) || typeof attrs !== 'object' || attrs instanceof Node)) {
        children = attrs; attrs = {};
      }
      for (const [key, value] of Object.entries(attrs || {})) {
        if (value == null) continue;
        if (typeof value === 'function') node.addEventListener(key, value);
        else node.setAttribute(key, String(value));
      }
      append(node, children);
      return node;
    };
    window.dom = {
      elem: node => node instanceof Node,
      matches: (node, selector) => node.matches(selector),
      content: (node, children) => { node.replaceChildren(); append(node, children); },
      bindClassInstance: (node, instance) => instances.set(node, instance),
      findClassInstance: node => instances.get(node)
    };
    window.findParent = (node, selector) => node.closest(selector);
    window.matchesElem = (node, selector) => node instanceof Element && node.matches(selector);
    window._ = value => value;
    String.prototype.format = function(...values) {
      let index = 0;
      return this.replace(/%[sdh]/g, () => String(values[index++]));
    };
    window.L = {
      bind: (fn, self, ...args) => fn.bind(self, ...args),
      isObject: value => value != null && typeof value === 'object',
      toArray: value => Array.isArray(value) ? value : value == null ? [] : String(value).split(/\s+/),
      naturalCompare: (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true })
    };
  });
  const definitions = sourceBetween(source, 'const UIElement=', 'const UITextfield=') +
    '\n' + sourceBetween(source, 'const UIDropdown=', 'const UICombobox=') +
    '\nwindow.OfflineDropdown = UIDropdown;';
  await page.addScriptTag({ content: definitions });
  await page.evaluate(() => {
    const ui = {
      Dropdown: window.OfflineDropdown,
      showModal(title, children, className = '') {
        const overlay = document.querySelector('#modal_overlay');
        const modal = E('div', { class: 'modal ' + className }, [E('h4', {}, title), children]);
        modal.style.maxHeight = 'calc(100vh - 40px)';
        overlay.replaceChildren(modal);
        document.body.classList.add('modal-overlay-active');
        return modal;
      },
      hideModal() {
        document.body.classList.remove('modal-overlay-active');
        document.querySelector('#modal_overlay').replaceChildren();
      }
    };
    window.OfflineUI = ui;
    L.require = async name => name === 'ui' ? ui : name === 'dom' ? dom : Promise.reject(new Error('No network module'));
  });
}

async function geometry(page) {
  return page.evaluate(async () => {
    const ui = window.OfflineUI;
    const choices = Object.fromEntries(Array.from({ length: 10 }, (_, i) => ['value' + i, 'Interface ' + i]));
    const widget = new ui.Dropdown(['value0', 'value3'], choices, {
      id: 'offline-geometry', name: 'offline-geometry', multiple: true, optional: true,
      sort: Object.keys(choices), display_items: 3
    });
    const field = E('div', { class: 'cbi-value-field' }, widget.render());
    const filler = E('div', { style: 'height:1500px' });
    const tail = E('div', { style: 'height:70px' });
    const modal = ui.showModal('Offline dropdown geometry', [filler, field, tail], 'cbi-modal');
    modal.scrollTop = modal.scrollHeight;
    const before = modal.scrollTop;
    widget.node.querySelector(':scope > .open').click();
    await new Promise(resolve => setTimeout(resolve, 180));
    const after = modal.scrollTop;
    const menu = widget.node.querySelector('ul.dropdown').getBoundingClientRect();
    const bounds = modal.getBoundingClientRect();
    const state = {
      initialChecked: [...widget.node.querySelectorAll('ul:not(.preview) > li[selected] input')].every(input => input.checked),
      stableScroll: Math.abs(after - before) <= 2,
      withinViewport: menu.top >= -1 && menu.left >= -1 && menu.right <= innerWidth + 1 && menu.bottom <= innerHeight + 1,
      withinModal: menu.top >= bounds.top - 1 && menu.bottom <= bounds.bottom + 1,
      hasMenuHeight: menu.height > 10,
      before, after,
      menu: { top: menu.top, bottom: menu.bottom, left: menu.left, right: menu.right, height: menu.height }
    };
    state.passed = state.initialChecked && state.stableScroll && state.withinViewport && state.withinModal && state.hasMenuHeight;
    widget.closeDropdown(widget.node, true);
    state.temporaryStylesCleared = ['top', 'bottom', 'left', 'right', 'maxHeight', 'maxWidth', 'minWidth', 'boxSizing']
      .every(name => widget.node.querySelector('ul:not(.preview)').style[name] === '');
    state.valuesPreserved = JSON.stringify(widget.getValue()) === JSON.stringify(['value0', 'value3']);
    state.passed = state.passed && state.temporaryStylesCleared && state.valuesPreserved;
    ui.hideModal();
    return state;
  });
}

async function footerBranding(page) {
  const template = fs.readFileSync(path.join(here, 'candidate/usr/share/ucode/luci/template/themes/glass/footer.ut'), 'utf8');
  const footer = template.match(/<footer class="glass-footer">[\s\S]*?<\/footer>/)?.[0];
  assert(footer, 'The existing Glass footer must be present');
  const values = { glass_version: '1.1.6', 'version.luciname': 'LuCI', 'version.luciversion': 'fixture',
    'version.disturl': '#offline-firmware', 'version.distname': 'ImmortalWrt', 'version.distversion': 'fixture' };
  const html = footer.replace(/{{\s*([^}]+?)\s*}}/g, (_, key) => {
    assert(Object.hasOwn(values, key), 'Unknown footer template field: ' + key);
    return values[key];
  });
  await page.evaluate(html => document.body.insertAdjacentHTML('beforeend', html), html);
  return page.evaluate(() => {
    const footer = document.querySelector('.glass-footer');
    const labels = [...footer.querySelectorAll('span')].filter(node => node.textContent === 'DictTech AP · 定制版');
    const label = labels[0];
    const theme = [...footer.querySelectorAll('span')].find(node => node.textContent.startsWith('GlassTheme '));
    const style = label && getComputedStyle(label), original = getComputedStyle(theme);
    const result = { count: labels.length, existingFooter: label?.parentElement === footer,
      visible: !!label && getComputedStyle(footer).display !== 'none' && label.getClientRects().length > 0,
      inheritedThemeStyle: !!style && style.color === original.color && style.fontSize === original.fontSize && style.fontWeight === original.fontWeight,
      fontSize: style?.fontSize, color: style?.color };
    result.passed = result.count === 1 && result.existingFooter && result.visible && result.inheritedThemeStyle;
    footer.remove();
    return result;
  });
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.BROWSER_EXECUTABLE,
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run']
  });
  const report = { offlineOnly: true, routerAccess: false, uciApisCalled: false, cases: [] };
  try {
    for (const touch of [false, true]) {
      const context = await browser.newContext({ viewport: touch ? { width: 390, height: 844 } : { width: 1280, height: 800 }, hasTouch: touch, isMobile: touch, offline: true, serviceWorkers: 'block' });
      await context.route('**/*', route => route.abort());
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await install(page, 'candidate');
      const branding = await footerBranding(page);
      const dependencyHidden = await page.evaluate(() => {
        const row = E('div', { class: 'cbi-value hidden' }, 'Dependency-hidden row');
        document.body.appendChild(row);
        const result = getComputedStyle(row).display === 'none' && row.getClientRects().length === 0;
        row.remove();
        return result;
      });
      const widgets = await widgetRegressions(page);
      let layout;
      try { layout = await geometry(page); }
      catch (error) { layout = { passed: false, failure: error.message }; }
      report.cases.push({ touch, branding, dependencyHidden, widgets, geometry: layout, pageErrors: errors });
      await context.close();
    }
    const baselineContext = await browser.newContext({ viewport: { width: 1280, height: 800 }, offline: true, serviceWorkers: 'block' });
    await baselineContext.route('**/*', route => route.abort());
    const baselinePage = await baselineContext.newPage();
    await install(baselinePage, 'original');
    const baselineChecked = await baselinePage.evaluate(() => {
      const widget = new OfflineUI.Dropdown(['a'], { a: 'A', b: 'B' }, { multiple: true });
      const node = widget.render();
      const result = [...node.querySelectorAll('li[selected] input[type="checkbox"]')].every(input => input.checked);
      node.remove();
      return result;
    });
    let baselineGeometry;
    try { baselineGeometry = await geometry(baselinePage); }
    catch (error) { baselineGeometry = { passed: false, failure: error.message }; }
    report.baseline = { initialChecked: baselineChecked, geometry: baselineGeometry };
    assert.equal(baselineChecked, false, 'The unchanged naoki baseline must reproduce the initial-selection defect');
    await baselineContext.close();
    report.passed = report.cases.every(row => row.branding.passed && row.dependencyHidden && row.widgets.passed && row.geometry.passed && !row.pageErrors.length);
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(here, 'dropdown-offline-dom-result.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ passed: report.passed, cases: report.cases.map(row => ({ touch: row.touch, widgets: row.widgets.passed, geometry: row.geometry, pageErrors: row.pageErrors })) }));
  assert(report.passed, 'Offline actual-widget regressions must pass');
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
