/* Fail-closed CI gate for the complete synthetic 50-scenario browser run. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {qualityActionCaseNames}=require('./acceptance_quality_actions.cjs');
const templateNames=['30-template-login-and-query','31-template-search-department',
  '32-template-pagination-and-sort','33-template-filtered-csv-download',
  '34-template-same-organization-peer','35-template-foreign-tenant-own-rows',
  '36-template-tampered-browser-state','37-template-revocation-query-export'];
const baseNames=['01-login-button-only','02-maintenance-pagination','03-maintenance-create-double-click',
  '04-maintenance-replay-supplementary','05-same-organization-peer-readonly','06-owner-update-version',
  '07-version-conflict-retains-draft','08-archive-restore','09-new-clears-unsaved-form','10-tenant-b-isolation',
  '11-tampered-cross-tenant-id-ui-submit','12-tampered-owner-ui-submit','13-identity-revocation-ui-submit',
  '14-qsl-read-pagination','15-cancel-create','15-cancel-update','15-cancel-delete','15-cancel-upload',
  '16-qsl-create-double-click','17-qsl-query-update-id-tamper','18-qsl-delete-query-and-submit',
  '19-qsl-upload-submit','20-qsl-wizard-next-previous','21-qsl-export-csv-xlsx','22-report-refresh-export',
  '23-etl-and-operations-render','24-tenant-denied-qsl-etl-operations',
  '25-readonly-qsl-controls-and-etl-denial','26-browser-history-navigation','27-mobile-maintenance',
  '28-logout-replay-denied','29-no-external-effects-or-page-errors'];
const expectedNames=[...baseNames,...templateNames,...qualityActionCaseNames];
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
function checkSummary(result,now=Date.now()) {
  assert.equal(result.mode,'full acceptance');
  assert.equal(result.plannedScenarios,50);assert.equal(result.passed,50);
  assert.equal(result.failed,0);assert.equal(result.unrunCount,0);
  assert(Array.isArray(result.results));assert.equal(result.results.length,50);
  assert.deepEqual(result.results.map(item=>item.name).sort(),[...expectedNames].sort());
  assert(result.results.every(item=>item.status==='passed'));
  assert.equal(result.reportTemplate.enabled,true);assert.equal(result.reportTemplate.plannedScenarios,8);
  assert.deepEqual(result.reportTemplate.unrunScenarios,[]);
  assert.equal(result.qualityActions.enabled,true);assert.equal(result.qualityActions.fixtureEnabled,true);
  assert.equal(result.qualityActions.plannedScenarios,10);assert.deepEqual(result.qualityActions.unrunScenarios,[]);
  assert.equal(result.staticTransport,'native browser');
  assert.equal(result.fixture.importedEntry,'app.py');assert.equal(result.fixture.schedulerStarted,false);
  assert.equal(result.fixture.syntheticOnly,true);assert.equal(result.serverStopped,true);
  assert.equal(result.syntheticStateRemoved,true);
  assert.equal(typeof result.browser,'string');assert(result.browser.length>0);
  assert.deepEqual(result.pageErrors,[]);assert.deepEqual(result.blockedExternalOrigins,[]);
  assert(Array.isArray(result.consoleErrors));assert(result.consoleErrors.every(item=>item.expectedAuthorizationDenial===true));
  assert(Array.isArray(result.networkFailures));assert(result.networkFailures.every(item=>item.error==='net::ERR_ABORTED'));
  const age=now-Date.parse(result.generatedAt);
  assert(Number.isFinite(age)&&age>=0&&age<=60*60*1000,'Browser evidence must be fresh (at most one hour).');
}
function readJson(filename) {
  assert(fs.statSync(filename).size<=2*1024*1024,'Evidence exceeds bound.');
  return JSON.parse(fs.readFileSync(filename,'utf8'));
}
function sourceFiles(root) {
  function walk(relative) {
    return fs.readdirSync(path.join(root,relative),{withFileTypes:true}).flatMap(entry=>{
      const name=path.posix.join(relative,entry.name);
      return entry.isDirectory()?walk(name):entry.isFile()?[name]:[];
    });
  }
  return ['app.py',...walk('reporting_workspace').filter(name=>name.endsWith('.py')),
    ...fs.readdirSync(path.join(root,'assets')).filter(name=>/\.(js|css)$/.test(name)).map(name=>'assets/'+name)].sort();
}
function checkHashes(root,record,expected) {
  assert.deepEqual(Object.keys(record).sort(),[...expected].sort(),'Evidence file set differs from current source.');
  for(const name of expected)assert.equal(record[name],hash(fs.readFileSync(path.join(root,name))),name+' source changed.');
}
function verify(root) {
  const evidenceRoot=path.join(root,'output','playwright','quality-actions');
  const latest=readJson(path.join(evidenceRoot,'latest.json'));
  assert(/^\d{4}-\d{2}-\d{2}T[\d-]+Z$/.test(latest.directory),'Invalid evidence directory.');
  const run=path.join(evidenceRoot,latest.directory);
  assert.equal(path.dirname(fs.realpathSync(run)),fs.realpathSync(evidenceRoot),'Evidence cannot escape its root.');
  const result=readJson(path.join(run,'results.json'));checkSummary(result);
  checkHashes(root,readJson(path.join(run,'source-hashes.json')),sourceFiles(root));
  checkHashes(root,readJson(path.join(run,'harness-hashes.json')),
    ['acceptance.cjs','acceptance_quality_actions.cjs','fixture_server.py','README.md'].map(name=>'tests/browser/'+name));
  const receipt={status:'PASSED',scope:'synthetic browser acceptance only',passed:50,failed:0,unrun:0,
    generatedAt:result.generatedAt,commit:process.env.GITHUB_SHA||null,python:result.python,browser:result.browser,
    sourceHashesSha256:hash(fs.readFileSync(path.join(run,'source-hashes.json'))),
    harnessHashesSha256:hash(fs.readFileSync(path.join(run,'harness-hashes.json')))};
  fs.writeFileSync(path.join(run,'ci-acceptance.json'),JSON.stringify(receipt,null,2)+'\n');
  console.log(JSON.stringify(receipt,null,2));return receipt;
}
if(require.main===module) {
  try {verify(path.resolve(__dirname,'../..'));}
  catch(error) {console.error('INCOMPLETE/FAILED browser acceptance: '+error.message);process.exitCode=1;}
}
module.exports={checkSummary,checkHashes,expectedNames,verify};
