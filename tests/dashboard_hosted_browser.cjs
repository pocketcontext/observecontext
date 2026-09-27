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
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({width, height: 900});
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Page overflow at ' + width);
    }
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
    console.log('PASS hosted dashboard browser: literal SQL, Berlin time, mobile, keyboard, no token storage, revoked-session cleanup');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
