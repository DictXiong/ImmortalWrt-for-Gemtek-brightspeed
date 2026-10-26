'use strict';

// Called by the offline DOM harness with stubbed UI/DOM modules only.
// All controls and values are temporary browser DOM; no UCI or transport APIs exist.
module.exports = async function browserWidgetRegressions(page) {
  return page.evaluate(async () => {
    const [ui, dom] = await Promise.all([L.require('ui'), L.require('dom')]);
    if (document.body.classList.contains('modal-overlay-active'))
      return { passed: false, cases: [], failure: 'Close the existing modal before running widget regressions.' };

    const fixture = document.createElement('div');
    fixture.id = '__browser_widget_regressions';
    const results = [];
    let sequence = 0;
    const delay = () => new Promise(resolve => setTimeout(resolve, 160));
    const assert = (value, message) => { if (!value) throw new Error(message); };
    const equal = (actual, expected, message) =>
      assert(JSON.stringify(actual) === JSON.stringify(expected), message);
    const sourceList = node => node.querySelector(':scope > ul:not(.preview)');
    const item = (node, value) => [...sourceList(node).children]
      .find(li => li.getAttribute('data-value') === value);
    const values = widget => {
      const value = widget.getValue();
      return Array.isArray(value) ? value : value == null ? [] : [value];
    };
    function snapshot(widget) {
      const node = widget.node;
      const entries = [...sourceList(node).children]
        .filter(li => li.hasAttribute('data-value') && !li.hasAttribute('unselectable'))
        .map(li => ({ value: li.getAttribute('data-value'), selected: li.hasAttribute('selected'),
          checked: li.querySelector('input[type="checkbox"]')?.checked ?? null }));
      return { entries, selected: entries.filter(li => li.selected).map(li => li.value),
        hidden: [...node.lastElementChild.querySelectorAll('input[type="hidden"]')].map(input => input.value),
        value: values(widget) };
    }
    function expectState(widget, expected, context) {
      const state = snapshot(widget);
      equal(state.selected, expected, context + ': selected items');
      equal(state.hidden, expected, context + ': hidden inputs');
      equal(state.value, expected, context + ': getValue');
      if (widget.options.multiple)
        assert(state.entries.every(entry => entry.checked === entry.selected), context + ': checked state');
      return state;
    }
    function make(value, options = {}) {
      const field = document.createElement('div');
      field.className = 'cbi-value-field';
      field.style.margin = '12px 0';
      const id = '__browser_widget_' + (++sequence);
      const widget = new ui.Dropdown(value, { a: 'A', b: 'B', c: 'C' }, {
        id, name: id, sort: ['a', 'b', 'c'], optional: true, display_items: 3, ...options
      });
      const node = widget.render();
      field.appendChild(node);
      fixture.appendChild(field);
      assert(dom.findClassInstance(node) === widget, 'Dropdown instance is bound to its DOM');
      return widget;
    }
    async function open(widget) {
      if (!widget.node.hasAttribute('open')) widget.node.querySelector(':scope > .open').click();
      await delay();
      assert(widget.node.hasAttribute('open'), 'Dropdown opened');
    }
    async function close(widget) {
      widget.closeDropdown(widget.node, true);
      await delay();
      assert(!widget.node.hasAttribute('open'), 'Dropdown closed');
    }
    function key(target, name, code) {
      target.dispatchEvent(new KeyboardEvent('keydown', {
        key: name, code: name, keyCode: code, which: code, bubbles: true, cancelable: true
      }));
    }
    async function run(name, callback) {
      try {
        const detail = await callback();
        results.push({ name, passed: true, ...(detail ? { detail } : {}) });
      } catch (error) {
        results.push({ name, passed: false, failure: String(error.message).slice(0, 200) });
      } finally {
        for (const node of fixture.querySelectorAll('.cbi-dropdown[open]'))
          dom.findClassInstance(node)?.closeDropdown(node, true);
        await delay();
        fixture.replaceChildren();
      }
    }

    ui.showModal('Dropdown widget regression', [fixture], 'cbi-modal');
    try {
      await run('multiple-initial-selection', async () => {
        const widget = make(['a', 'c'], { multiple: true });
        expectState(widget, ['a', 'c'], 'initial multi-select');
      });
      await run('multiple-empty-selection', async () => {
        const widget = make([], { multiple: true });
        expectState(widget, [], 'initial empty multi-select');
        await open(widget);
        await close(widget);
        expectState(widget, [], 'empty after open/close');
      });
      await run('open-close-preserves-values', async () => {
        const widget = make(['a', 'c'], { multiple: true });
        expectState(widget, ['a', 'c'], 'before open');
        for (let cycle = 0; cycle < 2; cycle++) {
          await open(widget);
          expectState(widget, ['a', 'c'], 'while open');
          await close(widget);
          expectState(widget, ['a', 'c'], 'after close');
        }
      });
      await run('multiple-toggle-synchronizes-values', async () => {
        const widget = make(['a'], { multiple: true });
        await open(widget);
        item(widget.node, 'b').click();
        expectState(widget, ['a', 'b'], 'add B');
        item(widget.node, 'a').click();
        expectState(widget, ['b'], 'remove A');
        item(widget.node, 'b').click();
        expectState(widget, [], 'remove last optional item');
        await close(widget);
        expectState(widget, [], 'closed empty selection');
      });
      await run('single-optional-empty-and-placeholder', async () => {
        const widget = make(null, { multiple: false, optional: true });
        expectState(widget, [], 'initial optional single');
        assert(sourceList(widget.node).querySelector('li[placeholder][selected]'), 'Empty placeholder selected');
        await open(widget);
        item(widget.node, 'b').click();
        expectState(widget, ['b'], 'select B');
        assert(!widget.node.hasAttribute('open'), 'Single selection closes dropdown');
        await open(widget);
        sourceList(widget.node).querySelector('li[placeholder]').click();
        expectState(widget, [], 'select empty placeholder');
      });
      await run('single-required-keeps-one-value', async () => {
        const widget = make(null, { multiple: false, optional: false });
        expectState(widget, ['a'], 'required initial fallback');
        assert(!sourceList(widget.node).querySelector('li[placeholder]'), 'No empty required placeholder');
        await open(widget);
        item(widget.node, 'b').click();
        expectState(widget, ['b'], 'required B selection');
        await open(widget);
        item(widget.node, 'b').click();
        expectState(widget, ['b'], 'required current item cannot clear itself');
      });
      await run('multiple-required-keeps-last-value', async () => {
        const widget = make(['a'], { multiple: true, optional: false });
        await open(widget);
        item(widget.node, 'a').click();
        expectState(widget, ['a'], 'last required item retained');
        item(widget.node, 'b').click();
        expectState(widget, ['a', 'b'], 'add required alternative');
        item(widget.node, 'a').click();
        expectState(widget, ['b'], 'switch remaining required item');
        item(widget.node, 'b').click();
        expectState(widget, ['b'], 'new last required item retained');
      });
      await run('keyboard-open-navigation-enter-escape', async () => {
        const widget = make('a', { multiple: false, optional: false });
        widget.node.focus();
        key(widget.node, 'ArrowDown', 40);
        await delay();
        assert(widget.node.hasAttribute('open'), 'ArrowDown opens dropdown');
        sourceList(widget.node).focus();
        key(sourceList(widget.node), 'ArrowDown', 40);
        assert(document.activeElement === item(widget.node, 'a'), 'ArrowDown focuses first item');
        key(document.activeElement, 'ArrowDown', 40);
        assert(document.activeElement === item(widget.node, 'b'), 'ArrowDown advances to B');
        key(document.activeElement, 'Enter', 13);
        expectState(widget, ['b'], 'Enter selects focused item');
        assert(!widget.node.hasAttribute('open'), 'Enter closes dropdown');
        widget.node.focus();
        key(widget.node, 'ArrowUp', 38);
        await delay();
        assert(widget.node.hasAttribute('open'), 'ArrowUp opens dropdown');
        sourceList(widget.node).focus();
        key(sourceList(widget.node), 'ArrowUp', 38);
        assert(document.activeElement === item(widget.node, 'c'), 'ArrowUp focuses last item');
        key(document.activeElement, 'ArrowUp', 38);
        assert(document.activeElement === item(widget.node, 'b'), 'ArrowUp advances to B');
        key(document.activeElement, 'Escape', 27);
        assert(!widget.node.hasAttribute('open'), 'Escape closes dropdown');
        assert(document.body.classList.contains('modal-overlay-active'), 'Escape preserves parent modal');
        expectState(widget, ['b'], 'Escape preserves value');
        return { syntheticKeyboardEvents: true };
      });
      await run('dynamic-choice-creation', async () => {
        const widget = make([], { multiple: true, optional: true, create: true });
        const choice = widget.createChoiceElement(widget.node, 'delta', 'Delta');
        assert(!choice.hasAttribute('selected'), 'New choice starts unselected');
        assert(choice.querySelector('input[type="checkbox"]')?.checked === false, 'New choice checkbox starts clear');
        const list = sourceList(widget.node);
        list.insertBefore(choice, list.lastElementChild);
        await open(widget);
        choice.click();
        expectState(widget, ['delta'], 'select dynamically added choice');
        const input = widget.node.querySelector(widget.options.create_query);
        assert(input, 'Create input exists');
        input.focus();
        input.value = 'echo';
        key(input, 'Enter', 13);
        expectState(widget, ['delta', 'echo'], 'Enter creates and selects choice');
        widget.createItems(widget.node, 'echo');
        assert([...sourceList(widget.node).children].filter(li => li.getAttribute('data-value') === 'echo').length === 1,
          'Creating existing choice does not duplicate it');
        expectState(widget, ['delta', 'echo'], 'repeat create keeps values');
        await close(widget);
        expectState(widget, ['delta', 'echo'], 'created values survive close');
      });
    } finally {
      ui.hideModal();
      fixture.remove();
    }
    return { passed: results.length === 9 && results.every(result => result.passed),
      temporaryBrowserControlsOnly: true, uciApisCalled: false, cases: results };
  });
};
