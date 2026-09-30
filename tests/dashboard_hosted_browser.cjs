/* Actual official SDK against isolated PocketBase; no production data. */
const fs=require('node:fs');
const config=JSON.parse(fs.readFileSync(0,'utf8'));
const {chromium}=require(process.argv[2]);
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.__cspViolations=[];document.addEventListener('securitypolicyviolation',e=>window.__cspViolations.push(e.violatedDirective));});
  await page.goto(config.url+'#/traces/'+config.alice.record.id);
  await page.locator('#signin').waitFor({state:'visible'});
  // Stock SDK OAuth uses its realtime channel and stock callback, not a proxy.
  const popup=context.waitForEvent('page');
  await page.locator('#login').click();
  await popup;
  await page.locator('#dashboard').waitFor({state:'visible'});
  if(!page.url().includes('#/traces/'+config.alice.record.id))throw Error('Lost login destination');
  await page.goto(config.url);
  await page.getByRole('button',{name:/^Inspect /}).first().focus();
  await page.keyboard.press('Enter');
  await page.locator('#detail').waitFor({state:'visible'});
  if(!(await page.locator('#sql').textContent()).includes(config.marker))throw Error('SQL missing');
  if(await page.locator('#sql img,#sql script').count())throw Error('SQL treated as markup');
  if(await page.evaluate(()=>window.__traceExecuted))throw Error('Trace executed');
  const operationURL=page.url();
  await page.reload();await page.locator('#detail').waitFor({state:'visible'});
  await page.getByRole('link',{name:'Permanent trace link',exact:true}).first().click();
  await page.waitForURL(/#\/traces\/[a-z0-9]{15}$/);await page.locator('#detail').waitFor({state:'visible'});
  await page.goBack();await page.locator('#detail').waitFor({state:'visible'});
  if(page.url()!==operationURL)throw Error('History failed');
  await page.locator('#filter').fill('missing-fixture');await page.locator('#empty').waitFor({state:'visible'});
  await page.locator('#filter').fill('');await page.getByRole('button',{name:/^Inspect /}).first().click();
  await page.locator('#detail').waitFor({state:'visible'});
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

  // Trace pagination remains per trace even when every result shares an operation.
  let paginationRow;
  await page.route('**/api/context/query',async route=>{
   const sql=route.request().postDataJSON().sql;
   if(!sql.startsWith('WITH selected AS'))return route.continue();
   const response=await route.fetch(),body=await response.json();
   const offset=sql.includes('OFFSET 50'),row=body.rows[0]||paginationRow;
   if(!offset)paginationRow=row;
   if(!row)return route.fulfill({response,json:body});
   const idIndex=body.columns.indexOf('id');
   body.rows=Array.from({length:offset?1:50},(_,i)=>{const copy=[...row];copy[idIndex]=String(i+1).padStart(15,'0');return copy;});
   await route.fulfill({response,json:body});
  });
  await page.locator('#collection').selectOption('traces');
  await page.waitForFunction(()=>document.querySelectorAll('#recent tr').length===50);
  await page.locator('#next').click();
  await page.waitForFunction(()=>document.querySelectorAll('#recent tr').length===1);
  await page.unroute('**/api/context/query');
  await page.locator('#collection').selectOption('operations');
  await page.getByRole('button',{name:/^Inspect /}).first().click();
  await page.locator('#detail').waitFor({state:'visible'});
  await context.setOffline(true);await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Refresh failed'));
  await context.setOffline(false);
  await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Updated'));
  const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('observecontext.auth')));
  if(!stored.token||stored.record.id!==config.alice.record.id)throw Error('LocalAuthStore absent');
  const second=await context.newPage();await second.goto(operationURL);
  await second.locator('#detail').waitFor({state:'visible'});
  await second.reload();await second.locator('#detail').waitFor({state:'visible'});
  // An old 401 must not clear a same-account token renewed by another tab.
  let oldRequest;await page.route('**/api/context/query',route=>{oldRequest=route;});
  await page.locator('#refresh').click();await page.waitForTimeout(1100);
  await second.evaluate(async()=>{const {renew}=await import('/dashboard/assets/api.js');await renew();});
  if(!oldRequest)throw Error('Missing held query');
  await oldRequest.fulfill({status:401,contentType:'application/json',body:'{}'});
  await page.waitForTimeout(100);
  if(await page.locator('#dashboard').isHidden())throw Error('Old rejection cleared renewed session');
  await page.unroute('**/api/context/query');
  // Cross-tab account switch must invalidate every old private view.
  await second.evaluate(async auth=>{const {pb}=await import('/dashboard/assets/api.js');pb.authStore.save(auth.token,auth.record);},config.bob);
  await page.waitForFunction(()=>document.querySelector('#account-name').textContent==='bob');
  await page.waitForFunction(()=>document.querySelector('#detail').hidden);
  if(await page.locator('#sql').textContent())throw Error('Previous account SQL retained');
  // Session revocation is checked on filtered SQL/refresh, independently of storage.
  await page.evaluate(async({admin,id})=>{await fetch('/api/collections/users/records/'+id,{method:'PATCH',headers:{Authorization:admin,'Content-Type':'application/json'},body:JSON.stringify({disabled:true})});},{admin:config.admin,id:config.bob.record.id});
  await page.locator('#refresh').click();await page.locator('#signin').waitFor({state:'visible'});
  await second.locator('#signin').waitFor({state:'visible'});
  await page.evaluate(async auth=>{const {pb}=await import('/dashboard/assets/api.js');pb.authStore.save(auth.token,auth.record);},config.alice);
  await page.locator('#dashboard').waitFor({state:'visible'});await second.locator('#dashboard').waitFor({state:'visible'});
  // A delayed renewal cannot resurrect a cross-tab logout.
  let held;await page.route('**/auth-refresh',route=>{held=route;});
  const pending=page.evaluate(async()=>{const {renew}=await import('/dashboard/assets/api.js');await renew().catch(()=>{});});
  await page.waitForTimeout(200);
  await second.locator('#logout').click();await page.locator('#signin').waitFor({state:'visible'});
  if(held)await held.fulfill({status:200,contentType:'application/json',body:JSON.stringify(config.alice)});
  await pending;await page.unroute('**/auth-refresh');
  if(await page.evaluate(()=>localStorage.getItem('observecontext.auth')))throw Error('Renewal resurrected logout');
  await page.reload();await page.locator('#signin').waitFor({state:'visible'});
  await page.evaluate(async auth=>{const {pb}=await import('/dashboard/assets/api.js');pb.authStore.save(auth.token,auth.record);},config.alice);
  await page.locator('#dashboard').waitFor({state:'visible'});
  // Hold an old SQL response across logout; it must never restore private rows.
  let sqlHeld;await page.route('**/api/context/query',route=>{sqlHeld=route;});
  await page.locator('#refresh').click();await page.waitForTimeout(100);
  await second.locator('#logout').click();await page.locator('#signin').waitFor({state:'visible'});
  if(sqlHeld)await sqlHeld.fulfill({status:200,contentType:'application/json',body:JSON.stringify({columns:['id','service'],rows:[['late','late-private']]})}).catch(()=>{});
  await page.unroute('**/api/context/query');
  if(await page.locator('#recent').textContent())throw Error('Late private rows restored');
  // Expired persisted JWT is rejected without rendering private content.
  await page.evaluate(async auth=>{
   const {pb}=await import('/dashboard/assets/api.js');
   const parts=auth.token.split('.');parts[1]=btoa(JSON.stringify({exp:1,id:auth.record.id}));
   pb.authStore.save(parts.join('.'),auth.record);
  },config.alice);
  await page.reload();await page.locator('#signin').waitFor({state:'visible'});
  if(await page.evaluate(()=>localStorage.getItem('observecontext.auth')))throw Error('Expired token retained');
  if((await page.evaluate(()=>window.__cspViolations)).length)throw Error('CSP violation');
  if(errors.length)throw Error(errors.join('\n'));
  console.log('PASS actual SDK OAuth/realtime callback, LocalAuthStore tabs/reload/logout, account switching, revocation, stale refresh/SQL, navigation, timeline/mobile/keyboard/CSP');
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
