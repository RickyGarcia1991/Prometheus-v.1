'use strict';
const assert = require('node:assert/strict');
const {setup} = require('./test-workspace.cjs');
const f = setup();
(async () => {
f.emit('prometheus-busy',{busy:false,online:true,closed:false});
f.elements.projectRoot.value='C:\\Projects\\measurement';
await f.elements.openProject.click();
f.result('code_open',{project:'C:\\Projects\\measurement',files:[],
  test_presets:[{id:'python-unittest',label:'Python unittest',available:true}]});
const result = async metrics => {
  await f.elements.runCodeTests.click();
  f.result('code_tests', {exit_code:0, duration_seconds:1,
    argv:['python','-m','unittest'], output:'OK', resource_measurements:metrics});
};
await result({scope:'direct_process', ram_gib:.025, cpu_seconds:.01, io_mib:.5,
  job_metrics:{scope:'job_associated_processes', peak_commit_gib:.04,
    cpu_seconds:.02, io_mib:1.5, total_processes:2, active_processes:0,
    snapshot_complete:true}});
let text = f.elements.codeTestOutput.textContent;
assert.match(text,/25\.6 MiB peak working set/);
assert.match(text,/41\.0 MiB peak committed memory/);
assert.match(text,/2 associated processes/);
assert.match(text,/OS transfers.*not physical disk/);
assert.match(text,/Coverage may exclude descendants created before assignment/);
await result({scope:'direct_process',ram_gib:null,cpu_seconds:NaN,io_mib:-1,
  job_metrics:{peak_commit_gib:Infinity,snapshot_complete:false}});
text=f.elements.codeTestOutput.textContent;
assert.match(text,/unavailable/);
assert.match(text,/incomplete/);
assert.doesNotMatch(text,/NaN|Infinity|undefined|-1 MiB/);
await result(undefined);
assert.match(f.elements.codeTestOutput.textContent,/Measurements unavailable/);
console.log('Project check measurements: scope, units, missing and partial counters passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
