/* Additional opt-in real-browser scenarios. No HTTP fixture controls or installs. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

const qualityActionCaseNames=[
  '38-quality-actions-login-catalog-query',
  '39-quality-actions-filters-historical-snapshot',
  '40-quality-actions-pagination-stable-sort',
  '41-quality-actions-filtered-server-csv',
  '42-quality-actions-same-organization-reads',
  '43-quality-actions-foreign-admin-user-isolation',
  '44-quality-actions-empty-header-only-export',
  '45-quality-actions-invalid-controls-no-download',
  '46-quality-actions-wrong-role-route-callback',
  '47-quality-actions-revocation-query-export',
];
const route='/QA_portal/quality-actions';
const id=name=>'quality-actions-'+name;
const caseId=number=>'action-'+String(number).padStart(4,'0');
const header='case_id,opened_date,due_date,closed_date,priority,finding,status,age_days,overdue_days';

async function qualityActionsAcceptance(harness) {
  const {peer,tenant,admin,fixture,out,activity,rawPayloads,newPage,test,goto,until,login,
    fill,click,settle,snapshot,command,tamperOnce,replay}=harness;
  const page=await newPage();
  let sharedRows,queryPayload,exportPayload;
  async function dropdown(target,name,label) {
    const select=target.locator('#'+id(name));
    await select.locator('.Select-control').click();
    await select.locator('.VirtualizedSelectOption').filter({hasText:new RegExp('^'+label+'$')}).click();
    await settle(target);
  }
  async function cells(target,column) {
    return (await target.locator('#'+id('table')+' td[data-dash-column="'+column+'"]').allTextContents()).map(value=>value.trim());
  }
  async function tenantRows(target,organization,count) {
    const findings=await cells(target,'finding');assert.equal(findings.length,count);
    assert(findings.every(value=>value.startsWith(organization+' synthetic finding ')),
      'Every displayed finding must belong to the authenticated organization');
    return findings;
  }
  async function status(target,value) {assert.equal(await target.locator('#'+id('status')).innerText(),value);}
  async function summary(target,value) {assert.equal(await target.locator('#'+id('summary')).innerText(),value);}
  async function download(target,name) {
    const [item]=await Promise.all([target.waitForEvent('download'),click(target,id('export'))]);
    assert.equal(item.suggestedFilename(),'synthetic-corrective-actions.csv');
    const destination=path.join(out,name);await item.saveAs(destination);
    const content=fs.readFileSync(destination,'utf8');assert(content.endsWith('\r\n'));
    const lines=content.trimEnd().split(/\r?\n/);assert.equal(lines.shift(),header);
    // This controlled synthetic fixture contains no quoted comma/newline fields.
    const rows=lines.map(line=>line.split(','));assert(rows.every(row=>row.length===9));
    return rows;
  }
  async function noDownload(target,action) {
    let downloads=0;const observed=()=>downloads++;target.on('download',observed);
    try {await action();await settle(target);assert.equal(downloads,0);} finally {target.off('download',observed);}
    return downloads;
  }
  try {
    await test(qualityActionCaseNames[0],page,async()=>{
      assert.equal(fixture.quality_actions_enabled,true);
      await goto(page,route);await until(()=>new URL(page.url()).pathname==='/login','Anonymous corrective-action route must redirect to login');
      assert.equal(await page.locator('#'+id('table')).count(),0);
      await login(page,'browser-owner-a');
      const card=page.locator('a.catalog-card-link[data-report-id="quality_actions"]');
      assert.equal(await card.locator('h3').innerText(),'Corrective action aging');assert.equal(await card.getAttribute('href'),route);
      await card.click();await settle(page);assert.equal(new URL(page.url()).pathname,route);
      await status(page,'1-20 of 36 matching rows');await summary(page,'25 open | 11 closed | 25 open overdue (all matching rows)');
      sharedRows=await tenantRows(page,'A',20);
      assert(await page.locator('#'+id('prev')).isDisabled());assert(!(await page.locator('#'+id('next')).isDisabled()));
      assert.equal(await page.locator('#'+id('table')+' input').count(),0);
      return {anonymousRedirect:'/login',catalogCard:true,visibleRows:20,totalRows:36,readOnlyTable:true};
    });
    await test(qualityActionCaseNames[1],page,async()=>{
      await fill(page,id('as-of'),'2026-08-31');await dropdown(page,'status-filter','Open');await dropdown(page,'priority','High');
      await fill(page,id('search'),'FINDING 0');await click(page,id('refresh'));
      assert.deepEqual(await cells(page,'case_id'),[caseId(6),caseId(9)]);
      assert.deepEqual(await cells(page,'closed_date'),['','']);assert.deepEqual(await cells(page,'age_days'),['5','2']);
      assert.deepEqual(await cells(page,'overdue_days'),['0','0']);await tenantRows(page,'A',2);
      await status(page,'1-2 of 2 matching rows');await summary(page,'2 open | 0 closed | 0 open overdue (all matching rows)');
      await fill(page,id('search'),'ACTION-0006');await click(page,id('refresh'));assert.deepEqual(await cells(page,'case_id'),[caseId(6)]);
      await fill(page,id('search'),'');await dropdown(page,'status-filter','Closed');await click(page,id('refresh'));
      assert.deepEqual(await cells(page,'case_id'),[caseId(3)]);assert.deepEqual(await cells(page,'closed_date'),['2026-08-31']);
      assert.deepEqual(await cells(page,'age_days'),['8']);await summary(page,'0 open | 1 closed | 0 open overdue (all matching rows)');
      return {asOf:'2026-08-31',statusPriorityAndCasefoldSearch:true,futureClosuresRemainOpen:true,futureOpenedCasesAbsent:true,closedOnSnapshotIncluded:true};
    });
    await test(qualityActionCaseNames[2],page,async()=>{
      await fill(page,id('as-of'),'2026-10-05');await dropdown(page,'status-filter','All statuses');await dropdown(page,'priority','All priorities');
      await dropdown(page,'order','Case ID');await dropdown(page,'direction','Descending');await dropdown(page,'limit','10');await click(page,id('refresh'));
      const first=Array.from({length:10},(_,index)=>caseId(36-index));assert.deepEqual(await cells(page,'case_id'),first);
      await click(page,id('next'));await status(page,'11-20 of 36 matching rows');assert.equal((await cells(page,'case_id'))[0],caseId(26));
      await click(page,id('prev'));assert.deepEqual(await cells(page,'case_id'),first);
      await click(page,id('next'));await click(page,id('next'));await click(page,id('next'));
      assert.deepEqual(await cells(page,'case_id'),[6,5,4,3,2,1].map(caseId));await status(page,'31-36 of 36 matching rows');
      assert(await page.locator('#'+id('next')).isDisabled());
      await dropdown(page,'order','Due date');await click(page,id('refresh'));
      assert.deepEqual(await cells(page,'case_id'),[35,34,36,31,33,30,32,27,29,26].map(caseId));
      await status(page,'1-10 of 36 matching rows');
      return {pageSize:10,lastPageRows:6,refreshResetsOffset:true,sort:'due date descending with ascending case-ID ties'};
    });
    await test(qualityActionCaseNames[3],page,async()=>{
      await dropdown(page,'status-filter','Open');await dropdown(page,'order','Case ID');await click(page,id('refresh'));await click(page,id('next'));
      await tenantRows(page,'A',10);await status(page,'11-20 of 25 matching rows');
      // Case 33 closes on 2026-10-06, so it is still open on this snapshot date.
      const rows=await download(page,'quality-actions-open-filtered.csv');assert.equal(rows.length,25);
      assert(rows.every(row=>row[5].startsWith('A synthetic finding ')&&row[6]==='Open'&&row[3]===''));
      assert.deepEqual(rows.map(row=>row[0]),Array.from({length:36},(_,index)=>36-index).filter(number=>number%3!==0||number===33).map(caseId));
      // Export uses the current controls, even before Apply filters updates the preview.
      await dropdown(page,'priority','Low');
      const current=await download(page,'quality-actions-current-controls.csv');assert.equal(current.length,12);
      assert(current.every(row=>row[4]==='Low'&&row[6]==='Open'&&row[5].startsWith('A synthetic finding ')));
      assert.deepEqual(current.map(row=>row[0]),[34,31,28,25,22,19,16,13,10,7,4,1].map(caseId));
      await status(page,'11-20 of 25 matching rows');
      queryPayload=rawPayloads.get(id('refresh')+'.n_clicks');exportPayload=rawPayloads.get(id('export')+'.n_clicks');
      assert(queryPayload&&exportPayload);
      return {visiblePage:2,visibleRows:10,filteredDownloadRows:25,currentUnappliedControlsDownloadRows:12,realServerDownloads:true};
    });
    await test(qualityActionCaseNames[4],peer,async()=>{
      await goto(peer,route);assert.deepEqual(await tenantRows(peer,'A',20),sharedRows);
      assert.equal(await peer.locator('#'+id('table')+' input').count(),0);await click(peer,id('refresh'));
      await goto(admin,route);assert.deepEqual(await tenantRows(admin,'A',20),sharedRows);
      await snapshot(admin,'42-quality-actions-same-organization-admin');
      return {peerAndAdminReadSameOrganizationRows:true,tableReadOnly:true};
    });
    await test(qualityActionCaseNames[5],tenant,async()=>{
      await goto(tenant,route+'?org=A&role=admin');await tenantRows(tenant,'B',20);
      await fill(tenant,id('search'),'A synthetic finding');await click(tenant,id('refresh'));
      assert.deepEqual(await cells(tenant,'case_id'),[]);await status(tenant,'0-0 of 0 matching rows');
      await fill(tenant,id('search'),'');await click(tenant,id('refresh'));
      const adminRows=await download(tenant,'quality-actions-tenant-b-admin.csv');assert.equal(adminRows.length,36);
      assert(adminRows.every(row=>row[5].startsWith('B synthetic finding ')));
      const ordinary=await newPage();
      try {
        await login(ordinary,'demo-user-b');await goto(ordinary,route);await tenantRows(ordinary,'B',20);
        const userRows=await download(ordinary,'quality-actions-tenant-b-user.csv');assert.deepEqual(userRows,adminRows);
        await snapshot(ordinary,'43-quality-actions-ordinary-user-b');
      } finally {await ordinary.context().close();}
      return {foreignAdminOwnRowsOnly:true,foreignUserOwnRowsOnly:true,foreignSearchRows:0,eachExportRows:36,urlTenantClaimIgnored:true};
    });
    await test(qualityActionCaseNames[6],page,async()=>{
      await goto(page,route);await fill(page,id('search'),'no synthetic finding matches this');await click(page,id('refresh'));
      assert.deepEqual(await cells(page,'case_id'),[]);await status(page,'0-0 of 0 matching rows');
      await summary(page,'0 open | 0 closed | 0 open overdue (all matching rows)');
      assert(await page.locator('#'+id('prev')).isDisabled());assert(await page.locator('#'+id('next')).isDisabled());
      assert.deepEqual(await download(page,'quality-actions-empty.csv'),[]);
      assert.equal(fs.readFileSync(path.join(out,'quality-actions-empty.csv'),'utf8'),header+'\r\n');
      return {visibleRows:0,exportRows:0,headerOnlyCsv:true,paginationDisabled:true};
    });
    await test(qualityActionCaseNames[7],page,async()=>{
      await goto(page,route+'?org=B&role=admin');await tenantRows(page,'A',20);
      await fill(page,id('as-of'),'');await click(page,id('refresh'));
      assert.deepEqual(await cells(page,'case_id'),[]);await status(page,'Check the report filters and try again.');
      await noDownload(page,()=>click(page,id('export')));await fill(page,id('as-of'),'2026-10-05');
      const attacks=[['offset',true],['limit',101],['status-filter','Other'],['priority','Critical'],
        ['as-of','2026-02-30'],['order','org'],['direction','sideways'],['search',{org:'B',role:'admin'}]];
      for(const [field,value] of attacks) {
        await tamperOnce(page,id('refresh')+'.n_clicks',body=>{
          const state=body.state.find(item=>item.id===id(field));assert(state);state.value=value;
        },()=>click(page,id('refresh')));
        assert.deepEqual(await cells(page,'case_id'),[]);await status(page,'Check the report filters and try again.');
        if(field!=='offset') await noDownload(page,()=>tamperOnce(page,id('export')+'.n_clicks',body=>{
          const state=body.state.find(item=>item.id===id(field));assert(state);state.value=value;
        },()=>click(page,id('export'))));
      }
      await click(page,id('refresh'));await tenantRows(page,'A',20);
      return {emptyDateRejected:true,invalidQuerySubmissions:attacks.length,invalidExportSubmissions:attacks.length,
        invalidExportDownloads:0,urlTenantClaimIgnored:true,validControlsRecover:true};
    });
    const guest=await newPage();
    try {
      await test(qualityActionCaseNames[8],guest,async()=>{
        await login(guest,'browser-actions-guest');assert.equal(await guest.locator('a.catalog-card-link[data-report-id="quality_actions"]').count(),0);
        await goto(guest,route);assert.match(await guest.locator('body').innerText(),/403: Forbidden/);
        assert.equal(await guest.locator('#'+id('table')).count(),0);activity.get(guest).expectDenied=true;
        await noDownload(guest,async()=>{
          for(const payload of [queryPayload,exportPayload]) {
            assert(payload,'A genuine allowed browser callback must precede supplementary denial replay');
            const response=await replay(guest,payload);assert.equal(response.status,403);assert.deepEqual(JSON.parse(response.text),{error:'Forbidden'});
          }
        });
        return {catalogHidden:true,routeForbidden:true,queryHttpStatus:403,exportHttpStatus:403,unauthorizedDownloads:0,
          callbackMode:'supplementary browser-context replay of actual UI submissions; denied route has no controls'};
      });
    } finally {await guest.context().close();}
    const revoked=await newPage();
    try {
      await test(qualityActionCaseNames[9],revoked,async()=>{
        await login(revoked,'browser-actions-revoked');await goto(revoked,route);await tenantRows(revoked,'A',20);
        await command('revoke',{user:'browser-actions-revoked'});activity.get(revoked).expectDenied=true;
        await noDownload(revoked,async()=>{
          for(const action of ['refresh','export']) {
            const response=revoked.waitForResponse(response=>response.url().endsWith('_dash-update-component')&&response.status()===401);
            await revoked.locator('#'+id(action)).click();assert.equal((await response).status(),401);await settle(revoked);
          }
        });
        return {interaction:'real Apply filters and Export clicks after server identity revocation',queryHttpStatus:401,exportHttpStatus:401,unauthorizedDownloads:0};
      });
    } finally {await revoked.context().close();}
  } finally {await page.context().close();}
}
module.exports={qualityActionCaseNames,qualityActionsAcceptance};
