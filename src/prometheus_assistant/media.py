"""On-demand ComfyUI launch; importing this module never loads Torch or a model."""
from __future__ import annotations
import csv
import os
from pathlib import Path
import platform
import subprocess
import time
from .hardware import detect_hardware, resource_root

PACKAGE = Path('Media/ComfyUI-v0.39.0-Windows-NVIDIA')


def nvidia_devices():
    candidates = [Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/nvidia-smi.exe',
                  Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'NVIDIA Corporation/NVSMI/nvidia-smi.exe']
    exe = next((p for p in candidates if p.is_file()), None)
    if exe is None:
        return []
    try:
        result = subprocess.run([str(exe), '--query-gpu=index,name,memory.total,memory.free,compute_cap',
                                 '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                check=True, timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return [dict(index=int(row[0]), name=row[1].strip(), vram_gib=float(row[2])/1024,
                     free_vram_gib=float(row[3])/1024, compute_capability=float(row[4]))
                for row in csv.reader(result.stdout.splitlines()) if len(row) == 5]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def eligibility(hardware, gpus, cpu=False):
    if hardware.system != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        return False, 'This portable ComfyUI package requires Windows x64.'
    if hardware.available_ram_gib is None:
        return False, 'Available RAM could not be measured; ComfyUI was not started.'
    if hardware.ram_gib < 7 or hardware.available_ram_gib < 4:
        return False, 'ComfyUI needs at least 4 GiB currently available RAM and about 8 GB installed. Close applications or use a larger host.'
    if cpu:
        return True, 'Explicit CPU mode: generation can be slow; start with a small image workflow.'
    if not any(g['compute_capability'] >= 7.5 and g['vram_gib'] >= 4 and g['free_vram_gib'] >= 3 for g in gpus):
        return False, 'No suitable free NVIDIA GPU detected for this CUDA 13 build. Use explicit --cpu, or supply the appropriate AMD, Intel, or older-NVIDIA package.'
    return True, 'GPU start permitted. Driver support and each workflow still need verification; this is not a guarantee that every model fits.'


def media_status(cpu=False):
    root = resource_root()
    package = Path(root)/PACKAGE if root else None
    portable = package/'ComfyUI_windows_portable' if package else None
    installed = bool(portable and (portable/'python_embeded/python.exe').is_file()
                     and (portable/'ComfyUI/main.py').is_file() and (package/'PROMETHEUS-PROVENANCE.json').is_file())
    hardware = detect_hardware()
    gpus = nvidia_devices() if hardware.system == 'Windows' else []
    ready, reason = eligibility(hardware, gpus, cpu)
    return dict(installed=installed, package=str(package) if package else None, hardware=hardware.__dict__,
                nvidia_devices=gpus, launch_allowed=installed and ready, reason=reason if installed else 'Portable ComfyUI is not fully installed.',
                cpu=cpu, starts_automatically=False, inference_verified=False,
                model_note='Image and video models are separate; application installation does not prove a workflow can generate.')


def launch_media(*, cpu=False, port=8188, shutdown_request=None):
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a port between 1024 and 65535.')
    state = media_status(cpu)
    if not state['launch_allowed']:
        raise ValueError(state['reason'])
    root = Path(resource_root()).resolve()
    data = root.parent/'Prometheus-Data/ComfyUI'
    for name in ('input', 'output', 'user', 'temp', 'cache'):
        (data/name).mkdir(parents=True, exist_ok=True)
    stop = Path(shutdown_request) if shutdown_request else data/'stop.request'
    if stop.exists():
        raise ValueError('A shutdown request is already present; finish eject or clear the stopped session before restarting.')
    portable = Path(state['package'])/'ComfyUI_windows_portable'
    command = [str(portable/'python_embeded/python.exe'), '-s', str(Path(__file__).with_name('comfy_bridge.py')),
               '--comfy-root', str(portable/'ComfyUI'), '--shutdown-request', str(stop), '--',
               '--listen', '127.0.0.1', '--port', str(port), '--disable-auto-launch', '--offline',
               '--disable-all-custom-nodes', '--cache-none', '--user-directory', str(data/'user'),
               '--input-directory', str(data/'input'), '--output-directory', str(data/'output'),
               '--temp-directory', str(data/'temp')]
    if cpu:
        command.append('--cpu')
    else:
        gpu = next(g for g in state['nvidia_devices'] if g['compute_capability'] >= 7.5 and g['vram_gib'] >= 4 and g['free_vram_gib'] >= 3)
        command += ['--cuda-device', str(gpu['index'])]
    env = os.environ.copy()
    env.update(HF_HOME=str(data/'cache/huggingface'), TORCH_HOME=str(data/'cache/torch'),
               TEMP=str(data/'temp'), TMP=str(data/'temp'), HF_HUB_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1', PYTHONDONTWRITEBYTECODE='1')
    log_path = data/'last-session.log'
    print(f'Starting ComfyUI at http://127.0.0.1:{port}. Log: {log_path}', flush=True)
    print('Press Ctrl+C to finish queued work and stop. Keep this window open until ComfyUI exits.', flush=True)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(f'\n--- Session {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n'); log.flush()
        process = subprocess.Popen(command, cwd=portable/'ComfyUI', env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while True:
            try:
                result = process.wait(timeout=1)
                if result:
                    print(f'ComfyUI exited with code {result}. See {log_path}', flush=True)
                return result
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                stop.parent.mkdir(parents=True, exist_ok=True)
                stop.write_text('Finish queued work, then stop.\n', encoding='utf-8')
                print('Stop requested. Waiting for queued work to finish before releasing the SSD.', flush=True)
