/* Real Chrome acceptance, with synthetic fixtures and no npm install required. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const root = path.resolve(__dirname, '../..');
const out = path.join(root, 'output/playwright', new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(out, {recursive:true});
const {chromium} = require(process.env.QA_PLAYWRIGHT_MODULE || 'playwright');
const results = [], consoleErrors = [], pageErrors = [], network = [], networkFailures = [], blocked = [], staticDelivery=[];
const assetCache=new Map();
const activity=new WeakMap();
const rawPayloads = new Map(); // Ephemeral callback payloads; never written to evidence.
const child = spawn(process.env.QA_PYTHON || 'python', ['-B', path.join(__dirname, 'fixture_server.py'), path.join(out, 'state')],
  {cwd:root, windowsHide:true, stdio:['pipe','pipe','pipe']});
const serverLog = fs.createWriteStream(path.join(out, 'server.log'));
child.stderr.pipe(serverLog);
let readyResolve, readyReject, sequence=0;
const ready = new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;});
const replies = new Map();
readline.createInterface({input:child.stdout}).on('line', line=>{
  try { const msg=JSON.parse(line); if(msg.event==='ready') readyResolve(msg);
    else if(msg.event==='reply') {replies.get(msg.sequence)?.(msg.result); replies.delete(msg.sequence);}
  } catch { /* Framework startup banners are not fixture protocol. */ }
});
child.on('error', readyReject);
child.on('exit', code=>{if(code) readyReject(new Error('Fixture exited '+code));});
async function command(action, args={}) {
  const id=++sequence;
  const answer = new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>{replies.delete(id);reject(new Error('Fixture command timed out'));},90000);
    replies.set(id,result=>{clearTimeout(timeout);resolve(result);});
  });
  child.stdin.write(JSON.stringify({action,sequence:id,...args})+'\n'); return answer;
}
const wait = ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn, label, timeout=12000) {
  const start=Date.now(); let last;
  while(Date.now()-start<timeout) {try {if(await fn()) return;} catch(e){last=e;} await wait(100);}
  throw new Error(label+(last ? ': '+last.message : ''));
}
async function settle(page) {
  await wait(300);
  await until(async()=>{
    const state=activity.get(page);
    return state.pending.size===0 && Date.now()-state.last>=650;
  },'Browser requests did not settle (90-second local static-transport allowance)',90000);
}
async function click(page,id) {await page.locator('#'+id).click();await settle(page);}
async function fill(page,id,value) {await page.locator('#'+id).fill(String(value));await settle(page);}
async function snapshot(page,name) {
  await page.evaluate(()=>window.scrollTo(0,0));
  await wait(75);
  await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
  fs.writeFileSync(path.join(out,name+'.dom.txt'),await page.locator('body').innerText());
}
async function test(name, page, fn) {
  const start=Date.now();
  try {const detail=await fn(); await snapshot(page,name);results.push({name,status:'passed',milliseconds:Date.now()-start,detail});console.log('PASS '+name);}
  catch(error) {await snapshot(page,name+'-FAILED').catch(()=>{});results.push({name,status:'failed',milliseconds:Date.now()-start,error:error.message});console.log('FAIL '+name+': '+error.message);}
  fs.writeFileSync(path.join(out,'progress.json'),JSON.stringify({results,consoleErrors,pageErrors,staticDelivery},null,2));
}
let browser, fixture, base;
async function newPage() {
  const context=await browser.newContext({viewport:{width:1440,height:1080},acceptDownloads:true});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!==base && !['data:','blob:','about:'].includes(url.protocol)) {blocked.push(url.origin);return route.abort();}
    if(process.env.QA_STATIC_BRIDGE==='1' && /^\/(?:_dash-component-suites|assets)\//.test(url.pathname)) {
      const key=url.pathname+url.search;
      if(!assetCache.has(key)) assetCache.set(key,command('static_asset',{path:key}).catch(error=>({error:error.message})));
      const asset=await assetCache.get(key);
      if(asset.error) {consoleErrors.push({text:'Static fixture fetch failed: '+asset.error,path:key});return route.abort();}
      if(!staticDelivery.some(item=>item.path===key)) staticDelivery.push({path:key,size:asset.size,sha256:asset.sha256,attempts:asset.attempts||1});
      return route.fulfill({status:asset.status,contentType:asset.content_type,body:Buffer.from(asset.body,'base64')});
    }
    await route.continue();
  });
  const page=await context.newPage(); page.setDefaultTimeout(10000);
  const state={pending:new Set(),last:Date.now()};activity.set(page,state);
  page.on('request',request=>{state.pending.add(request);state.last=Date.now();});
  const finished=request=>{state.pending.delete(request);state.last=Date.now();};
  page.on('framenavigated',frame=>{if(frame===page.mainFrame()) {state.pending.clear();state.last=Date.now();}});
  page.on('requestfinished',finished);page.on('requestfailed',finished);
  page.on('requestfailed',request=>networkFailures.push({path:new URL(request.url()).pathname,method:request.method(),error:request.failure()?.errorText}));
  page.on('console',msg=>{if(msg.type()==='error') {
    const text=msg.text();
    const expectedAuthorizationDenial=Boolean(state.expectDenied &&
      (text.includes('status of 401') || text.includes('status of 403') ||
       (text.includes('Callback error updating') && text.includes('"error":"Login required"'))));
    consoleErrors.push({text,path:new URL(page.url()).pathname,expectedAuthorizationDenial});
  }});
  page.on('pageerror',error=>pageErrors.push({message:error.message,stack:error.stack,path:new URL(page.url()).pathname}));
  page.on('request',req=>{if(req.url().endsWith('_dash-update-component')) {
    const payload=req.postDataJSON(); for(const changed of payload.changedPropIds||[]) rawPayloads.set(changed,payload);
  }});
  page.on('response',response=>{if(response.url().includes('_dash-update-component')) network.push({path:'/_dash-update-component',status:response.status()});});
  return page;
}
async function goto(page,route) {await page.goto(base+route,{timeout:90000});await settle(page);}
async function login(page,user) {
  await goto(page,'/login');await page.locator('#username-box').waitFor();
  await fill(page,'username-box',user);await fill(page,'password-box','demo-only');await click(page,'login-box');
  await until(()=>new URL(page.url()).pathname==='/', 'Login did not navigate home');
}
async function search(page,text) {await fill(page,'maintenance-search',text);await page.locator('#maintenance-search').press('Tab');await settle(page);}
async function selectDefinition(page,name) {
  await search(page,name);const row=page.locator('#maintenance-table tr').filter({hasText:name});
  await row.locator('input[type=radio]').click();await settle(page);
}
async function definitions(q='',user='browser-owner-a') {return command('definitions',{q,user});}
async function tamperOnce(page, trigger, edit, action) {
  let used=false;
  const handler=async route=>{const payload=route.request().postDataJSON();
    if(!used && payload.changedPropIds?.includes(trigger)){used=true;edit(payload);await route.continue({postData:JSON.stringify(payload)});}
    else await route.continue();};
  await page.route('**/_dash-update-component',handler);
  try {await action();assert(used,'Expected UI request was not intercepted');} finally {await page.unroute('**/_dash-update-component',handler);}
}
async function replay(page,payload) {return page.evaluate(async body=>{
  const response=await fetch('/_dash-update-component',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return {status:response.status,text:await response.text()};
},payload);}
async function qslRows() {return command('qsl');}
const fields={Material_Type:'IC',Vendor_Code:'BROWSERNEW',Vendor_Name:'Browser Created Vendor',Country:'TW',City:'Taipei',Rev:'A',Supplier_Level:'LEVEL 1'};
async function reportExport(page) {
  await goto(page,'/page3');assert(await page.locator('#table').isVisible());await click(page,'report-refresh');
  await until(async()=>await page.locator('#performance-chart .main-svg .barlayer .point path').count()>0,
    'Report graph must render actual SVG bar data');
  const [download]=await Promise.all([page.waitForEvent('download'),click(page,'report-export')]);await download.saveAs(path.join(out,'report.csv'));
  assert.match(fs.readFileSync(path.join(out,'report.csv'),'utf8'),/revenue/);
  return {tableVisible:true,renderedSvgBars:await page.locator('#performance-chart .main-svg .barlayer .point path').count(),downloadedCsvContainsRevenue:true};
}

(async()=>{
try {
  fixture=await Promise.race([ready,wait(30000).then(()=>{throw new Error('Fixture startup timeout');})]);
  fs.writeFileSync(path.join(out,'fixture.json'),JSON.stringify({pid:fixture.pid,port:fixture.port,importedEntry:'app.py'},null,2));
  base='http://127.0.0.1:'+fixture.port;
  browser=await chromium.launch({channel:process.env.QA_BROWSER_CHANNEL||'chrome',headless:process.env.QA_HEADED!=='1'});
  const owner=await newPage(), peer=await newPage(), admin=await newPage(), tenant=await newPage();
  await test('01-login-button-only',owner,async()=>{
    await goto(owner,'/login');await fill(owner,'username-box','browser-owner-a');await fill(owner,'password-box','demo-only');
    await owner.locator('#password-box').press('Enter');await settle(owner);assert.equal(new URL(owner.url()).pathname,'/login');
    await fill(owner,'password-box','incorrect');await click(owner,'login-box');assert(await owner.locator('#login-alert').isVisible());
    await fill(owner,'password-box','demo-only');await click(owner,'login-box');await until(()=>new URL(owner.url()).pathname==='/', 'Sign in failed');
  });
  if(results[0].status!=='passed') throw new Error('Browser login prerequisite failed; dependent UI scenarios were not run');
  if(process.env.QA_LOGIN_ONLY==='1') return;
  if(process.env.QA_REPORT_ONLY==='1') {
    await test('22-report-refresh-export',admin,async()=>{await login(admin,'demo-admin');await reportExport(admin);});
    return;
  }
  if(process.env.QA_REVOKE_ONLY==='1') {
    await test('diagnostic-revoke-session',owner,async()=>{
      await goto(owner,'/maintenance');await fill(owner,'maintenance-name','Revocation diagnostic');
      await command('revoke',{user:'browser-owner-a'});
      activity.get(owner).expectDenied=true;
      await Promise.all([owner.waitForResponse(r=>r.url().endsWith('_dash-update-component')&&r.status()===401),owner.locator('#maintenance-save').click()]);
      await wait(1500);
    });
    return;
  }
  await test('02-maintenance-pagination',owner,async()=>{
    await goto(owner,'/maintenance');assert.match(await owner.locator('#maintenance-page-label').innerText(),/25.*Page 1/);
    assert(await owner.locator('#maintenance-prev').isDisabled());await click(owner,'maintenance-next');
    assert.match(await owner.locator('#maintenance-page-label').innerText(),/Page 2/);assert(await owner.locator('#maintenance-next').isDisabled());
    await click(owner,'maintenance-prev');assert.match(await owner.locator('#maintenance-page-label').innerText(),/Page 1/);
  });
  let created, createPayload;
  await test('03-maintenance-create-double-click',owner,async()=>{
    await click(owner,'maintenance-new');await fill(owner,'maintenance-name','Browser owned definition');await fill(owner,'maintenance-description','Synthetic created through browser');
    await owner.locator('#maintenance-save').dblclick();await settle(owner);
    createPayload=rawPayloads.get('maintenance-save.n_clicks');
    const data=await definitions('Browser owned definition');assert.equal(data.total,1);created=data.items[0];assert.equal(created.version,1);
    assert.match(await owner.locator('#maintenance-record-meta').innerText(),/Version 1/);
    return {matchingDatabaseRows:data.total,version:created.version,interaction:'real double click'};
  });
  await test('04-maintenance-replay-supplementary',owner,async()=>{
    assert(createPayload);const response=await replay(owner,createPayload);assert.equal(response.status,200);
    assert.equal((await definitions('Browser owned definition')).total,1);return {matchingDatabaseRows:1,mode:'browser-context transport replay supplement; create exercised by real double click'};
  });
  await test('05-same-organization-peer-readonly',peer,async()=>{
    await login(peer,'browser-peer-a');await goto(peer,'/maintenance');await selectDefinition(peer,'Browser owned definition');
    assert(await peer.locator('#maintenance-save').isDisabled());assert(await peer.locator('#maintenance-name').isDisabled());
    assert.match(await peer.locator('#maintenance-form-hint').innerText(),/Read only/);
  });
  await test('06-owner-update-version',owner,async()=>{
    await selectDefinition(owner,'Browser owned definition');await fill(owner,'maintenance-description','Browser version two');await click(owner,'maintenance-save');
    assert.match(await owner.locator('#maintenance-record-meta').innerText(),/Version 2/);assert.equal((await definitions('Browser owned definition')).items[0].version,2);
  });
  await test('07-version-conflict-retains-draft',owner,async()=>{
    await login(admin,'demo-admin');await goto(admin,'/maintenance');await selectDefinition(admin,'Browser owned definition');
    await fill(admin,'maintenance-description','Admin concurrent change');await click(admin,'maintenance-save');
    await fill(owner,'maintenance-description','Owner unsaved conflicting draft');await click(owner,'maintenance-save');
    assert.equal(await owner.locator('#maintenance-description').inputValue(),'Owner unsaved conflicting draft');
    assert.equal((await definitions('Browser owned definition')).items[0].description,'Admin concurrent change');
    assert.match(await owner.locator('#maintenance-record-meta').innerText(),/Version 2/);await click(owner,'maintenance-reload');
    assert.match(await owner.locator('#maintenance-record-meta').innerText(),/Version 3/);
  });
  await test('08-archive-restore',owner,async()=>{
    await click(owner,'maintenance-archive');assert.match(await owner.locator('#maintenance-record-meta').innerText(),/Version 4.*Archived/);
    assert(await owner.locator('#maintenance-save').isDisabled());assert((await definitions('Browser owned definition')).items[0].deleted_at);
    await snapshot(owner,'08-archived-definition');
    await click(owner,'maintenance-restore');assert.match(await owner.locator('#maintenance-record-meta').innerText(),/Version 5.*Active/);
    assert.equal((await definitions('Browser owned definition')).items[0].deleted_at,null);
  });
  await test('09-new-clears-unsaved-form',owner,async()=>{
    await fill(owner,'maintenance-name','Do not persist this draft');await click(owner,'maintenance-new');
    assert.equal(await owner.locator('#maintenance-name').inputValue(),'');assert.equal((await definitions('Do not persist')).total,0);
  });
  await test('10-tenant-b-isolation',tenant,async()=>{
    await login(tenant,'browser-admin-b');await goto(tenant,'/maintenance');
    assert.match(await tenant.locator('#maintenance-table').innerText(),/Browser tenant B private/);
    assert.doesNotMatch(await tenant.locator('#maintenance-table').innerText(),/Browser owned|Browser seed A/);
    await search(tenant,'Browser owned');assert.match(await tenant.locator('#maintenance-page-label').innerText(),/No matching/);
  });
  await test('11-tampered-cross-tenant-id-ui-submit',owner,async()=>{
    await selectDefinition(owner,'Browser owned definition');const before=(await definitions('Browser tenant B','browser-admin-b')).items[0];
    await tamperOnce(owner,'maintenance-save.n_clicks',body=>{body.state.find(s=>s.id==='maintenance-record').value={id:fixture.foreign_id,version:1};},()=>click(owner,'maintenance-save'));
    const after=(await definitions('Browser tenant B','browser-admin-b')).items[0];assert.deepEqual(after,before);
    assert.match(await owner.locator('#maintenance-record-meta').innerText(),/Version 5/);
    return {foreignTenantRecordUnchanged:true};
  });
  await test('12-tampered-owner-ui-submit',peer,async()=>{
    await click(peer,'maintenance-new');await fill(peer,'maintenance-name','Unauthorized peer change');
    await tamperOnce(peer,'maintenance-save.n_clicks',body=>{body.state.find(s=>s.id==='maintenance-record').value={id:created.id,version:5};},()=>click(peer,'maintenance-save'));
    assert.equal((await definitions('Browser owned definition')).items[0].version,5);assert.equal((await definitions('Unauthorized peer')).total,0);
    return {targetVersionUnchanged:5,unauthorizedCreateCount:0};
  });
  await test('13-identity-revocation-ui-submit',peer,async()=>{
    const revoked=await newPage();await login(revoked,'browser-revoked');await goto(revoked,'/maintenance');
    await fill(revoked,'maintenance-name','Revoked account write');await command('revoke',{user:'browser-revoked'});
    activity.get(revoked).expectDenied=true;
    const denied=revoked.waitForResponse(r=>r.url().endsWith('_dash-update-component')&&r.status()===401);
    await revoked.locator('#maintenance-save').click();await denied;await settle(revoked);assert.equal((await definitions('Revoked account')).total,0);
    await snapshot(revoked,'13-revoked-session');await revoked.context().close();
    return {callbackStatus:401,unauthorizedCreateCount:0};
  });
  await test('14-qsl-read-pagination',admin,async()=>{
    await goto(admin,'/QA_portal/maintenance');await click(admin,'qa-read');assert.match(await admin.locator('#qa-table').innerText(),/Synthetic Vendor/);
    const first=await admin.locator('#qa-table').innerText();await admin.locator('#qa-table button.next-page').click();await settle(admin);
    assert.notEqual(await admin.locator('#qa-table').innerText(),first);await admin.locator('#qa-table button.previous-page').click();await settle(admin);
    assert.equal(await admin.locator('#qa-table').innerText(),first);
  });
  for(const modal of ['create','update','delete','upload']) {
    await test('15-cancel-'+modal,admin,async()=>{
      const before=await qslRows();await click(admin,'qa-open-'+modal);assert(await admin.locator('#qa-modal-'+modal).isVisible());
      if(modal==='create') await fill(admin,'qa-create-field-Vendor_Code','CANCELLED');
      if(modal==='update') {await fill(admin,'qa-record-id',1);await click(admin,'qa-load-id');await fill(admin,'qa-field-City','Cancelled city');}
      if(modal==='delete') {await fill(admin,'qa-delete-id',1);await click(admin,'qa-query-delete');}
      if(modal==='upload') {await admin.locator('#qa-upload input[type=file]').setInputFiles({name:'cancel.csv',mimeType:'text/csv',buffer:Buffer.from(Object.keys(fields).join(',')+'\n'+Object.values({...fields,Vendor_Code:'CANCELCSV'}).join(',')+'\n')});await settle(admin);}
      await snapshot(admin,'15-open-'+modal);
      await click(admin,'qa-close-'+modal);await admin.locator('#qa-modal-'+modal).waitFor({state:'hidden'});assert.deepEqual(await qslRows(),before);
      await click(admin,'qa-open-'+modal);
      if(modal==='create') assert.equal(await admin.locator('#qa-create-field-Vendor_Code').inputValue(),'');
      if(modal==='update') assert.equal(await admin.locator('#qa-record-id').inputValue(),'');
      if(modal==='delete') assert.equal(await admin.locator('#qa-delete-id').inputValue(),'');
      if(modal==='upload') assert.equal(await admin.locator('#qa-preview').innerText(),'');
      await click(admin,'qa-close-'+modal);
      return {databaseRowsUnchanged:true};
    });
  }
  let newQsl;
  await test('16-qsl-create-double-click',admin,async()=>{
    await click(admin,'qa-open-create');for(const [key,value] of Object.entries(fields)) await fill(admin,'qa-create-field-'+key,value);
    await admin.locator('#qa-create').dblclick();await settle(admin);
    const matches=(await qslRows()).filter(row=>row.Vendor_Code==='BROWSERNEW');assert.equal(matches.length,1);newQsl=matches[0];
  });
  await test('17-qsl-query-update-id-tamper',admin,async()=>{
    await click(admin,'qa-open-update');await fill(admin,'qa-record-id',newQsl.id);await click(admin,'qa-load-id');
    assert.equal(await admin.locator('#qa-field-Vendor_Code').inputValue(),'BROWSERNEW');await fill(admin,'qa-field-Rev','B');
    await fill(admin,'qa-record-id',1);await click(admin,'qa-submit-update');assert(await admin.locator('#qa-modal-update').isVisible());
    assert.match(await admin.locator('#qa-update-status').innerText(),/ID/);assert.equal((await qslRows()).find(row=>row.id===newQsl.id).version,1);
    await fill(admin,'qa-record-id',newQsl.id);await click(admin,'qa-load-id');await fill(admin,'qa-field-Rev','B');await click(admin,'qa-submit-update');
    await admin.locator('#qa-modal-update').waitFor({state:'hidden'});assert.equal((await qslRows()).find(row=>row.id===newQsl.id).version,2);
  });
  await test('18-qsl-delete-query-and-submit',admin,async()=>{
    await click(admin,'qa-open-delete');await fill(admin,'qa-delete-id',newQsl.id);await click(admin,'qa-query-delete');
    assert.match(await admin.locator('#qa-delete-record').innerText(),/BROWSERNEW/);await click(admin,'qa-delete');
    await admin.locator('#qa-modal-delete').waitFor({state:'hidden'});assert(!(await qslRows()).some(row=>row.id===newQsl.id));
    await fill(admin,'qa-history-id',newQsl.id);await click(admin,'qa-history-load');
    assert.match(await admin.locator('#qa-history-table').innerText(),/create/);assert.match(await admin.locator('#qa-history-table').innerText(),/delete/);
  });
  await test('19-qsl-upload-submit',admin,async()=>{
    await click(admin,'qa-open-upload');await admin.locator('#qa-upload input[type=file]').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from(Object.keys(fields).join(',')+'\n'+Object.values({...fields,Vendor_Code:'BROWSERCSV',Vendor_Name:'Browser CSV vendor'}).join(',')+'\n')});await settle(admin);
    assert((await admin.locator('#qa-preview').innerText()).length>0);await click(admin,'qa-import');
    await admin.locator('#qa-modal-upload').waitFor({state:'hidden'});assert.equal((await qslRows()).filter(row=>row.Vendor_Code==='BROWSERCSV').length,1);
  });
  await test('20-qsl-wizard-next-previous',admin,async()=>{
    assert(await admin.locator('#qb-source-panel').isVisible());await click(admin,'qb-next');assert(await admin.locator('#qb-columns-panel').isVisible());
    await click(admin,'qb-next');assert(await admin.locator('#qb-options-panel').isVisible());await click(admin,'qb-back');assert(await admin.locator('#qb-columns-panel').isVisible());
    await click(admin,'qb-next');await click(admin,'qb-next');assert(await admin.locator('#qb-preview-panel').isVisible());await click(admin,'qb-preview');
    assert.match(await admin.locator('#qb-table').innerText(),/Vendor_Code/);
  });
  await test('21-qsl-export-csv-xlsx',admin,async()=>{
    for(const [button,name] of [['qa-export','qsl.csv'],['qa-export-xlsx','qsl.xlsx']]) {
      const [item]=await Promise.all([admin.waitForEvent('download'),click(admin,button)]);await item.saveAs(path.join(out,name));assert(fs.statSync(path.join(out,name)).size>20);
    }
  });
  await test('22-report-refresh-export',admin,()=>reportExport(admin));
  await test('23-etl-and-operations-render',admin,async()=>{
    await goto(admin,'/QA_portal/etl');assert(await admin.locator('#etl-job-cards').isVisible());await click(admin,'etl-refresh');await snapshot(admin,'23-etl');
    await goto(admin,'/QA_portal/operations');assert(await admin.locator('#ops-job-status').isVisible());await click(admin,'ops-job-status');
  });
  await test('24-tenant-denied-qsl-etl-operations',tenant,async()=>{
    for(const route of ['/QA_portal/maintenance','/QA_portal/etl','/QA_portal/operations']) {
      await goto(tenant,route);assert.match(await tenant.locator('body').innerText(),/Forbidden/);await snapshot(tenant,'24-denied-'+route.split('/').pop());
    }
  });
  await test('25-readonly-qsl-controls-and-etl-denial',peer,async()=>{
    await goto(peer,'/QA_portal/maintenance');assert(await peer.locator('#qa-open-create').isDisabled());assert(await peer.locator('#qa-open-update').isDisabled());
    await goto(peer,'/QA_portal/etl');assert(await peer.locator('#etl-run').isDisabled());
  });
  await test('26-browser-history-navigation',admin,async()=>{
    await goto(admin,'/maintenance');await goto(admin,'/page3');await admin.goBack();await settle(admin);assert(await admin.locator('#maintenance-save').isVisible());
    await admin.goForward();await settle(admin);assert(await admin.locator('#report-export').isVisible());
  });
  await test('27-mobile-maintenance',owner,async()=>{
    await owner.setViewportSize({width:390,height:844});await goto(owner,'/maintenance');assert(await owner.locator('#maintenance-new').isVisible());
    await click(owner,'maintenance-new');await fill(owner,'maintenance-name','Mobile unsaved draft');await click(owner,'maintenance-new');assert.equal(await owner.locator('#maintenance-name').inputValue(),'');
  });
  await test('28-logout-replay-denied',owner,async()=>{
    await owner.goto(base+'/logout');await until(()=>new URL(owner.url()).pathname==='/login','Logout did not return login');
    await owner.locator('#username-box').waitFor({state:'visible'});
    activity.get(owner).expectDenied=true;
    const response=await replay(owner,createPayload);assert.equal(response.status,401);return {mode:'real logout navigation plus supplementary browser-context replay'};
  });
  await test('29-no-external-effects-or-page-errors',admin,async()=>{
    assert.deepEqual(blocked,[],'Unexpected external network was blocked');assert.deepEqual(pageErrors,[],'Uncaught browser errors');
    assert.deepEqual(consoleErrors.filter(item=>!item.expectedAuthorizationDenial),[],'Unexpected browser console errors');
    assert.deepEqual(networkFailures.filter(item=>item.error!=='net::ERR_ABORTED'),[],'Unexpected failed browser requests');
    return {networkBoundary:'All browser requests constrained to owned loopback origin; scheduler lifecycle never started'};
  });
} catch(error) {results.push({name:'harness',status:'failed',error:error.message});console.error(error.message);}
finally {
  const browserVersion=browser ? browser.version() : null;
  if(browser) await browser.close();
  if(child.exitCode===null) {child.stdin.write(JSON.stringify({action:'shutdown'})+'\n');child.stdin.end();await Promise.race([new Promise(resolve=>child.once('exit',resolve)),wait(10000)]);if(child.exitCode===null) child.kill();}
  serverLog.end();
  const mode=process.env.QA_LOGIN_ONLY==='1' ? 'login diagnostic' : process.env.QA_REVOKE_ONLY==='1' ? 'revocation diagnostic' : process.env.QA_REPORT_ONLY==='1' ? 'focused report acceptance' : 'full acceptance';
  const summary={generatedAt:new Date().toISOString(),mode,platform:process.platform,node:process.version,python:fixture?.python,browser:browserVersion,browserChannel:process.env.QA_BROWSER_CHANNEL||'chrome',
    fixture:{pid:fixture?.pid,port:fixture?.port,importedEntry:'app.py',schedulerStarted:false,syntheticOnly:true},
    passed:results.filter(r=>r.status==='passed').length,failed:results.filter(r=>r.status==='failed').length,results,consoleErrors,pageErrors,networkFailures,blockedExternalOrigins:blocked,
    staticTransport:process.env.QA_STATIC_BRIDGE==='1' ? 'fixture loopback HTTP fetch; identical static bytes fulfilled to browser; native static delivery not certified' : 'native browser',
    callbackStatuses:network.reduce((a,n)=>(a[n.status]=(a[n.status]||0)+1,a),{}),serverStopped:child.exitCode!==null};
  fs.writeFileSync(path.join(out,'static-delivery.json'),JSON.stringify(staticDelivery,null,2));
  fs.writeFileSync(path.join(out,'source-hashes.json'),JSON.stringify(fixture?.source_hashes||{},null,2));
  summary.packages=fixture?.packages;
  if(mode==='focused report acceptance') summary.unrun={count:30,reason:'Explicit focused report mode; other full-suite scenarios were not executed',caseGroups:['02','03','04','05','06','07','08','09','10','11','12','13','14','15-create','15-update','15-delete','15-upload','16','17','18','19','20','21','23','24','25','26','27','28','29']};
  const statePath=path.resolve(out,'state');
  if(summary.serverStopped && path.dirname(statePath)===out && path.relative(root,out).startsWith(path.join('output','playwright')+path.sep)) {
    fs.rmSync(statePath,{recursive:true,force:true});summary.syntheticStateRemoved=true;
  }
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(summary,null,2));
  fs.writeFileSync(path.join(root,'output/playwright/latest.json'),JSON.stringify({directory:path.basename(out),mode,passed:summary.passed,failed:summary.failed},null,2));
  console.log(JSON.stringify({directory:path.relative(root,out),passed:summary.passed,failed:summary.failed}));process.exitCode=summary.failed?1:0;
}
})();
