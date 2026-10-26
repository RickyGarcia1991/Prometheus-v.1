import datetime,hashlib,json,shutil,subprocess
from pathlib import Path
B=Path(__file__).resolve().parent
W=B.parent.parent
S=Path('C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/source')
P=S.parent/'dev46-release-sync'
D=P/'docs/evidence/windows-enrollment-r5-20261021'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
assert read(B/'real-r5-restart-acceptance.json')['passed'] is True
assert read(B/'real-r5-installed-integrity.json')['passed'] is True
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=P,text=True).strip()
report='''# Windows reconnect enrollment and restart checkpoint

R5 is enrolled and active in the normal Windows profile. The signed manifest is `b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e`; the fresh sealed transaction is `7b06d1cde83a4b8ebb5a8ac1fa086887`.

181 related runtime/enrollment tests passed. The replacement helper passed 21 isolated tests, including duplicate-module cleanup. Independent source, upgrade and restart-helper reviews passed. Native process tests are included in the related suite. The unchanged strict signing profile retains its earlier 11 passing checks.

Actual Task Scheduler duplicate-start, stop and restart checks passed. Exactly one launcher/supervisor pair was present; stopping removed both; restarting created a fresh pair. The existing application listeners and real-profile controller survived both stop and restart, and authorization hashes matched. All five installed payloads match the signed package. Existing memory is present. Core dev55 and the Surface worker were not replaced.

Earlier failures are retained: MSIX profile redirection, initial orphan supervisor, active-controller refusal, and the two-module activation-helper error. The completed enrollment was not repeated after that activation error: activation succeeded in a fresh normal-profile process. The helper correction was tested separately. The original executed helper is preserved. A prior command to update the temporary execution task used the wrong PowerShell parameter set; it retried the old create-only entry and made no successful new enrollment. The corrected task action was verified before the R5 run.

Arrival now opens the ordinary owner-unlock page instead of treating its authentication requirement as startup failure. The protected interface still needs the user's passkey; no authentication bypass or protected-status acceptance is claimed.

Physical SSD disconnect/reconnect and a full Windows reboot were NOT performed. The forward controller release and broader roadmap remain separate pending milestones; Ubuntu is not started. Routine display updates remain OFF.

## Recovery and rollback

The original authorization and sealed journals remain in the real profile. The bounded upgrade saved both original signed payloads and the exact old disabled task XML before removal. R3 and R5 signed closures are included on the drives. Recovery must validate the relevant signatures, exact task owner and file hashes first; do not automatically re-enable the old R3 supervisor, which has the recorded child-lifetime defect. No recursive profile replacement or user-data restoration was performed. The temporary inspection/execution tasks will be removed after the final readback; the enrolled logon supervisor remains enabled.

Tested source commit: `SOURCECOMMIT`.
'''.replace('SOURCECOMMIT',head)
(W/'outputs/Prometheus-windows-enrollment-r5-checkpoint.md').write_text(report,encoding='utf-8')
queue=W/'outputs/Prometheus-current-workqueue.md'
queue.write_text(report+'\n## Earlier checkpoints\n\n'+queue.read_text(encoding='utf-8'),encoding='utf-8')
D.mkdir(parents=True,exist_ok=True)
(D/'README.md').write_text(report,encoding='utf-8')
files=[]
def add(p,name,public=True):
 assert p.is_file(),str(p)
 files.append({'path':str(p),'checkpoint_path':name,'sha256':sha(p)})
 if public:
  dest=D/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);assert sha(dest)==sha(p)
for n in ['enrollment-r5-signing.json','enrollment-r5-tests.xml','enrollment-r5-tests.log','enrollment-candidate-r5-index.json','real-restart-initial-failure.json','real-r5-active-controller-refusal.txt','real-r5-activation-module-refusal.txt','real-r5-activation-result.json','real-r5-restart-acceptance.json','real-r5-installed-integrity.json','quiet-supervisor-independent-review.json','arrival-owner-gate-independent-review.json','create-only-r5-upgrade-independent-review.json','real-r5-restart-helper-review.json','task-launch-diagnostic-review.json','upgrade-quiet-host-create-only.ps1','test_quiet_create_only_upgrade.py','test-real-r5-restart.ps1','activate-enrolled-reconnect.ps1','activate-real-r5.ps1','inspect-real-r5-integrity.ps1','qualify-reconnect-enrollment-v2.ps1','test_reconnect_signing_profile_v2.py','enrollment-profile-v2-tests.xml']:
 add(B/n,'evidence/'+n)
for n in ['quiet-supervisor-ownership-report.json','quiet-supervisor-ownership-tests.log','create-only-r5-upgrade-report.json','create-only-module-cleanup-report.json','create-only-module-cleanup-tests.log','upgrade-quiet-host-create-only-executed-r5.ps1']:
 add(W/'work/companion-enrollment'/n,'evidence/'+n)
for n in ['Prometheus-QuietHost-v3.cs','Prometheus-Arrival.ps1','ReconnectEnrollment.psm1']:
 add(S/'tools/Prometheus.HostAgent'/n,'source/tools/Prometheus.HostAgent/'+n,False)
for n in ['test_arrival_owner_gate.py','test_quiet_host_v3.py','test_reconnect_enrollment.py']:
 add(S/'tests'/n,'source/tests/'+n,False)
add(B/'reconnect-quiet-build-r4/quiet-host-build-receipt.json','evidence/quiet-host-build-receipt.json')
for rev in ['r3','r5']:
 for p in sorted((B/('reconnect-enrollment-candidate-'+rev)).iterdir()):
  add(p,'signed-'+rev+'/'+p.name,False)
for d in sorted(B.glob('quiet-create-only-upgrade-*')):
 for p in sorted(d.iterdir()):add(p,'upgrade-recovery/'+d.name+'/'+p.name,False)
add(W/'outputs/Prometheus-windows-enrollment-r5-checkpoint.md','README.md',False)
qualification={'qualified':True,'qualification_scope':'real-profile enrollment and scheduled supervisor restart only','activated':True,'source_commit':head,'captured':datetime.datetime.now(datetime.timezone.utc).isoformat(),'protected_interface_pending_user_unlock':True,'physical_usb_cycle_tested':False,'windows_reboot_tested':False,'routine_displays':'off','files':files}
(B/'windows-enrollment-r5-qualification.json').write_text(json.dumps(qualification,indent=2),encoding='utf-8')
print(json.dumps({'files':len(files),'repository_evidence':str(D),'source_commit':head}))
