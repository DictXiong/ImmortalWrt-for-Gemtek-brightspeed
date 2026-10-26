These are offline fixtures for `node scripts/dicttech/test-ap-ui.cjs`.

Prepare the locked feed sources first, or pass `--source-root` pointing to a
prepared build tree. The runner checks candidate hashes against the reviewed UI
manifest. Original LuCI and Glass files are read with local `git show` at the
pinned feed revisions; missing blobs fail without lazy network fetches.

The source/VM checks cover shared radio settings, three-band capability lookup,
frequency save targets, power display and the MLO width form. The board fixture
contains a synthetic path and public band capabilities only. It has no device
identity, factory data, credentials or live UCI information.

For real DOM tests, install Playwright and Chromium locally, or set:

```sh
PLAYWRIGHT_MODULE=/path/to/playwright-core \
BROWSER_EXECUTABLE=/path/to/chromium \
node scripts/dicttech/test-ap-ui.cjs --source-root /path/to/build-tree --require-browser
```

`--playwright-module` and `--browser-executable` provide the same settings.
Without browser dependencies, syntax/radio/MLO checks still run and the report
explicitly marks the DOM suite `SKIP`. `--require-browser` makes that skip fail.
Use `--output report.json` to save the combined result.

The browser runs local fixture DOM only, with offline contexts, blocked service
workers and aborted resource requests. The suite accepts no router URL and
contains no authentication or UCI transport implementation. Desktop and touch
cases exercise initial selection, open/close state, keyboard input, value
synchronization, dynamic choices, dependency-hidden rows, viewport/modal bounds
and scroll stability. An unpatched baseline must reproduce the initial checkbox
defect as a negative control.
