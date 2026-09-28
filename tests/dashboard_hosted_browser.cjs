/* Run by dashboard_hosted.py against synthetic isolated services only. */
const fs = require('node:fs');
const config = JSON.parse(fs.readFileSync(0, 'utf8'));
const { chromium } = require(process.argv[2]);
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    await context.addCookies(config.cookies);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => { window.__cspViolations = []; document.addEventListener('securitypolicyviolation', event => window.__cspViolations.push(event.violatedDirective)); });
    let reportRequests = 0, dataRequests = 0;
    page.on('request', request => { if (request.url().endsWith('/api/dashboard/report')) reportRequests++; if (request.url().endsWith('/api/dashboard/data')) dataRequests++; });
    page.on('dialog', dialog => { errors.push('Untrusted trace opened a dialog'); dialog.dismiss(); });
    await page.goto(config.url);
    await page.locator('#dashboard').waitFor({state: 'visible'});
    await page.getByRole('button', {name: /^Inspect /}).first().waitFor();
    if (reportRequests) throw Error('Summary fetched before requested');
    await page.getByRole('button', {name: /^Inspect /}).first().focus();
    await page.keyboard.press('Enter');
    await page.locator('#detail').waitFor({state: 'visible'});
    if (!(await page.locator('#sql').textContent()).includes(config.marker)) throw Error('SQL marker missing');
    if (await page.locator('#sql img, #sql script').count()) throw Error('SQL interpreted as markup');
    if (await page.evaluate(() => window.__traceExecuted)) throw Error('Trace script executed');
    if (!(await page.locator('#identity').textContent()).includes('14:00:00.000')) throw Error('Berlin timezone missing');
    if (!(await page.locator('#measurements').textContent()).includes('sql.execute')) throw Error('Phase missing');
    if (!(await page.locator('#detail').evaluate(node => node === document.activeElement))) throw Error('Inspect did not focus detail');
    // Check actual rendered geometry, not only SVG attributes: phases share one
    // trace-relative scale, including overlap, gaps and a zero-duration endpoint.
    async function checkTimeline() {
      const result = await page.locator('.timeline').first().evaluate(timeline => {
        const charts = [...timeline.querySelectorAll('.timeline-chart')];
        return {
          ticks: [...timeline.querySelectorAll('.timeline-axis text')].map(node => node.textContent),
          rows: charts.map(chart => {
            const frame = chart.getBoundingClientRect();
            const bar = chart.querySelector('.timeline-bar, .timeline-marker');
            const box = bar.getBoundingClientRect();
            return {x: (box.x - frame.x) / frame.width, width: box.width / frame.width,
                    label: chart.getAttribute('aria-label'), left: frame.left, right: frame.right};
          })
        };
      });
      if (JSON.stringify(result.ticks) !== JSON.stringify(['0.00 ms', '5.00 ms', '10.00 ms', '15.00 ms', '20.00 ms'])) throw Error('Trace axis scale incorrect');
      const expected = [[0, .4], [.2, .5], [.6, .15], [1, 0]];
      if (result.rows.length !== expected.length) throw Error('Missing timeline phases');
      result.rows.forEach((row, index) => {
        if (Math.abs(row.x - expected[index][0]) > .005 || Math.abs(row.width - expected[index][1]) > .005) throw Error('Incorrect phase geometry: ' + JSON.stringify(row));
        if (Math.abs(row.left - result.rows[0].left) > 1 || Math.abs(row.right - result.rows[0].right) > 1) throw Error('Phase scales are not aligned');
        if (!row.label.includes('starts at ') || !row.label.includes('duration ')) throw Error('Accessible timing missing');
      });
    }
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({width, height: 900});
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Page overflow at ' + width);
      await checkTimeline();
      if (process.env.OBSERVECONTEXT_TEST_SCREENSHOTS && width !== 768) await page.screenshot({path: process.env.OBSERVECONTEXT_TEST_SCREENSHOTS + '/timeline-' + width + '.png', fullPage: true});
    }
    // Even the maximum accepted trace duration must fit the narrow mobile axis.
    await page.route('**/api/dashboard/trace?*', async route => {
      const response = await route.fetch();
      const body = await response.json();
      body.trace.duration_ms = 86400000;
      body.spans = [{name: 'long', offset_ms: 43200000, duration_ms: 21600000}];
      await route.fulfill({response, json: body});
    });
    await page.setViewportSize({width: 320, height: 900});
    await page.getByRole('button', {name: /^Inspect /}).first().click();
    await page.locator('.timeline-chart[aria-label^="long;"]').waitFor();
    const labelsFit = await page.locator('.timeline-axis svg').evaluate(axis => {
      const labels = [...axis.querySelectorAll('text')].map(node => node.getBoundingClientRect());
      const box = axis.getBoundingClientRect();
      return labels.length === 2 && labels[0].right < labels[1].left && labels[0].left >= box.left - 1 && labels[1].right <= box.right + 1;
    });
    if (!labelsFit) throw Error('Long-duration mobile axis labels overlap or overflow');
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Long-duration mobile page overflow');
    await page.unroute('**/api/dashboard/trace?*');
    await page.setViewportSize({width: 1440, height: 900});
    // A zero-duration trace must still render a finite origin marker.
    await page.route('**/api/dashboard/trace?*', async route => {
      const response = await route.fetch();
      const body = await response.json();
      body.trace.duration_ms = 0;
      body.spans = [{name: 'instant', offset_ms: 0, duration_ms: 0}];
      await route.fulfill({response, json: body});
    });
    await page.getByRole('button', {name: /^Inspect /}).first().click();
    await page.locator('.timeline-chart[aria-label^="instant;"]').waitFor();
    const instant = page.locator('.timeline-marker');
    if (await instant.getAttribute('x1') !== '0%' || await instant.getAttribute('x2') !== '0%') throw Error('Zero-duration timeline has invalid origin');
    if ((await page.locator('.timeline-axis text').allTextContents()).join() !== '0.00 ms') throw Error('Zero-duration timeline axis incorrect');
    await page.unroute('**/api/dashboard/trace?*');
    await page.getByRole('button', {name: /^Inspect /}).first().click();
    await page.locator('.timeline-chart[aria-label^="sql.execute;"]').waitFor();
    await page.locator('#refresh').focus();
    await page.keyboard.press('Enter');
    await page.locator('#refresh').waitFor({state: 'visible'});
    await page.locator('#report-panel summary').click();
    await page.waitForFunction(() => document.getElementById('report-status').textContent.length > 0);
    if (await page.evaluate(() => localStorage.length || sessionStorage.length)) throw Error('Browser storage contains session material');
    if ((await page.evaluate(() => document.cookie)).includes('session')) throw Error('Session cookie accessible to script');
    const html = await page.content();
    if (config.forbidden.some(value => html.includes(value))) throw Error('Backend token leaked into HTML');
    // Background tabs must not keep triggering snapshot reads.
    await page.evaluate(() => Object.defineProperty(document, 'hidden', {configurable: true, value: true}));
    const beforeHidden = dataRequests;
    await page.waitForTimeout(5200);
    if (dataRequests !== beforeHidden) throw Error('Hidden tab continued polling');
    await page.evaluate(() => { delete document.hidden; });
    // A late private-data response must not repopulate the DOM after logout.
    let held;
    await page.route('**/api/dashboard/data', route => { held = route; });
    await page.locator('#refresh').click();
    await page.waitForTimeout(100);
    await page.route('**/api/dashboard/logout', route => route.fulfill({status: 204, body: ''}));
    await page.locator('#logout').click();
    await page.locator('#signin').waitFor({state: 'visible'});
    if (held) await held.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({recent: [{id:'late',operation:'late',service:'late-private',method:'POST',route:'/api/context/query',status:200,started_at:'2026-09-27T12:00:00.000Z',duration_ms:1}]})}).catch(() => {});
    await page.waitForTimeout(100);
    if ((await page.locator('#recent').textContent()).includes('late-private')) throw Error('Late response restored private data');
    await page.unroute('**/api/dashboard/data');
    await page.unroute('**/api/dashboard/logout');
    await page.reload();
    await page.locator('#dashboard').waitFor({state:'visible'});
    await page.getByRole('button', {name:/^Inspect /}).first().click();
    await page.locator('#detail').waitFor({state:'visible'});
    if ((await page.evaluate(() => window.__cspViolations)).length) throw Error('Dashboard violated its content security policy');
    // An expired/revoked session must clear already rendered private data.
    await page.route('**/api/dashboard/data', route => route.fulfill({status: 401, contentType: 'application/json', body: '{}'}));
    await page.locator('#refresh').click();
    await page.locator('#signin').waitFor({state: 'visible'});
    if ((await page.locator('#sql').textContent()) || (await page.locator('#recent').textContent())) throw Error('Private data retained after session ended');
    if (!(await page.locator('#dashboard').isHidden())) throw Error('Private dashboard visible after logout');
    if (errors.length) throw Error(errors.join('\n'));
    console.log('PASS hosted dashboard browser: timeline offsets/overlap/gaps/zero duration, literal SQL, Berlin time, mobile, keyboard, CSP, no token storage, revoked-session cleanup');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
