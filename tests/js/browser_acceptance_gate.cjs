'use strict';
const assert=require('node:assert/strict');
const {checkSummary,expectedNames}=require('../browser/verify_acceptance.cjs');
const now=Date.now();
function valid(){return {mode:'full acceptance',plannedScenarios:50,passed:50,failed:0,unrunCount:0,
  results:expectedNames.map(name=>({name,status:'passed'})),
  reportTemplate:{enabled:true,plannedScenarios:8,unrunScenarios:[]},
  qualityActions:{enabled:true,fixtureEnabled:true,plannedScenarios:10,unrunScenarios:[]},
  staticTransport:'native browser',fixture:{importedEntry:'app.py',schedulerStarted:false,syntheticOnly:true},
  serverStopped:true,syntheticStateRemoved:true,browser:'synthetic-test-browser-version',pageErrors:[],
  blockedExternalOrigins:[],consoleErrors:[],networkFailures:[],generatedAt:new Date(now).toISOString()};}
checkSummary(valid(),now);
const invalid=[r=>r.passed=0,r=>r.failed=1,r=>r.unrunCount=1,r=>r.mode='focused report acceptance',
  r=>r.results.pop(),r=>r.results[1].name=r.results[0].name,r=>r.results[0].status='failed',
  r=>r.qualityActions.fixtureEnabled=false,r=>r.reportTemplate.enabled=false,
  r=>r.staticTransport='fixture bridge',r=>r.serverStopped=false,r=>r.syntheticStateRemoved=false,
  r=>r.fixture.schedulerStarted=true,r=>r.browser=undefined,r=>r.pageErrors.push({message:'failure'}),
  r=>r.blockedExternalOrigins.push('https://example.invalid'),r=>r.consoleErrors.push({text:'unexpected'}),
  r=>r.networkFailures.push({error:'net::ERR_CONNECTION_RESET'}),
  r=>r.generatedAt=new Date(now-3600001).toISOString(),r=>r.generatedAt=new Date(now+1).toISOString()];
for(const mutate of invalid){const result=valid();mutate(result);assert.throws(()=>checkSummary(result,now));}
console.log('Browser acceptance gate: valid fixture accepted; '+invalid.length+' invalid fixtures rejected. Not browser execution.');
