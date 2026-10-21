from pathlib import Path
from types import SimpleNamespace
import pytest
from prometheus_assistant import media
from prometheus_assistant.comfy_bridge import DrainState
from prometheus_assistant.hardware import HardwareProfile
from prometheus_assistant.cli import build_parser


@pytest.mark.parametrize('total,free,cpu,gpus,allowed', [
    (8, .9, True, [], False),
    (32, None, False, [], False),
    (8, 4.2, True, [], True),
    (16, 6, False, [], False),
    (16, 6, False, [dict(compute_capability=6.1,vram_gib=8,free_vram_gib=8)], False),
    (16, 6, False, [dict(compute_capability=8.6,vram_gib=8,free_vram_gib=2)], False),
    (32, 12, False, [dict(compute_capability=8.6,vram_gib=12,free_vram_gib=10)], True),
])
def test_media_uses_free_memory_and_matching_gpu(monkeypatch,total,free,cpu,gpus,allowed):
    monkeypatch.setattr(media.platform,'machine',lambda:'AMD64')
    assert media.eligibility(HardwareProfile(total,8,'Windows',free),gpus,cpu)[0] is allowed


def test_incompatible_host_is_refused(monkeypatch):
    monkeypatch.setattr(media.platform,'machine',lambda:'arm64')
    assert not media.eligibility(HardwareProfile(64,16,'Windows',50),[],True)[0]
    monkeypatch.setattr(media.platform,'machine',lambda:'AMD64')
    assert not media.eligibility(HardwareProfile(64,16,'Linux',50),[],True)[0]


def test_drain_never_releases_pending_job_or_write(tmp_path):
    stop=tmp_path/'stop.request'
    state=DrainState(stop)
    current=[[],[]]
    queue=SimpleNamespace(get_current_queue_volatile=lambda:current)
    assert not state.drained(queue)
    stop.touch()
    current[0]=['running job']
    assert not state.drained(queue)
    current[:]=[[],['queued job']]
    assert not state.drained(queue)
    current[:]=[[],[]]
    state.active_writes=1
    assert not state.drained(queue)
    state.active_writes=0
    assert state.drained(queue)
    stop.unlink()
    assert state.requested(), 'Shutdown admission must stay closed after the request is consumed.'


def test_rejected_launch_starts_no_process(monkeypatch):
    monkeypatch.setattr(media,'media_status',lambda cpu:dict(launch_allowed=False,reason='Low memory'))
    monkeypatch.setattr(media.subprocess,'Popen',lambda *a,**k:pytest.fail('No process should start'))
    with pytest.raises(ValueError,match='Low memory'):
        media.launch_media()


def test_status_is_default_and_cpu_opt_in():
    parser=build_parser()
    args=parser.parse_args(['media'])
    assert args.action=='status' and not args.cpu
    args=parser.parse_args(['media','start','--cpu'])
    assert args.action=='start' and args.cpu


def test_start_keeps_files_on_ssd_and_disables_network_nodes(monkeypatch,tmp_path):
    root=tmp_path/'Prometheus-Resources'
    package=root/media.PACKAGE
    monkeypatch.setattr(media,'resource_root',lambda:root)
    monkeypatch.setattr(media,'media_status',lambda cpu:dict(launch_allowed=True,package=str(package)))
    recorded={}
    def popen(command,**kwargs):
        recorded.update(command=command,**kwargs)
        return SimpleNamespace(wait=lambda **kw:0)
    monkeypatch.setattr(media.subprocess,'Popen',popen)
    assert media.launch_media(cpu=True)==0
    command=recorded['command']
    assert '--cpu' in command and '--offline' in command and '--disable-all-custom-nodes' in command
    assert command[command.index('--listen')+1]=='127.0.0.1'
    for key in ('TEMP','TMP','HF_HOME','TORCH_HOME'):
        assert Path(recorded['env'][key]).is_relative_to(tmp_path/'Prometheus-Data/ComfyUI')


def test_port_and_pending_eject_are_respected(monkeypatch,tmp_path):
    with pytest.raises(ValueError,match='port'):
        media.launch_media(port=80)
    root=tmp_path/'Prometheus-Resources'
    monkeypatch.setattr(media,'resource_root',lambda:root)
    monkeypatch.setattr(media,'media_status',lambda cpu:dict(launch_allowed=True,package=str(root/media.PACKAGE)))
    stop=tmp_path/'eject.request';stop.touch()
    with pytest.raises(ValueError,match='shutdown'):
        media.launch_media(cpu=True,shutdown_request=stop)
