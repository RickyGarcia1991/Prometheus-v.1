"""Explicit handoff to portable coding applications, without shell interpolation."""
from __future__ import annotations
import os
from pathlib import Path
import platform
import subprocess
from .hardware import PORTABLE_AGENTS, coding_agents, detect_hardware, resource_root


def coding_status():
    found = coding_agents()
    root = resource_root()
    return {
        'hardware': detect_hardware().__dict__,
        'tools': [dict(name=name, installed=name in found, executable=found.get(name),
                       portable=bool(root and (Path(root)/path).is_file()),
                       inference_verified=False, authentication='user configured; not inspected')
                  for name, path in PORTABLE_AGENTS.items()],
        'offline_coding': 'prometheus --task coding chat uses installed local models',
        'online_coding': 'code codex requires your account; code hermes --setup configures its provider',
        'hermes_local_requirement': 'Current Hermes Agent requires at least 64000 context tokens; ordinary Prometheus chat uses smaller contexts.',
        'concurrency': 'Portable launcher holds the drive session lock until the coding application exits.',
    }


def launch_coding(provider, project, *, prompt=None, setup=False):
    if provider not in PORTABLE_AGENTS:
        raise ValueError('Choose hermes or codex.')
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64','x86_64'):
        raise ValueError('These bundled coding tools require Windows x64; this host needs a compatible runtime.')
    root = resource_root()
    if not root:
        raise ValueError('Start coding through the portable Prometheus launcher.')
    exe = Path(root)/PORTABLE_AGENTS[provider]
    if not exe.is_file():
        raise ValueError(f'Portable {provider} package is not installed.')
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError('The coding workspace must be an existing directory.')
    # The assistant's own package and runtime are not a default coding workspace.
    if project == project.parent or project.is_relative_to(Path(root).resolve()):
        raise ValueError('Choose a project folder outside Prometheus-Resources and the drive root.')
    env = os.environ.copy()
    command = [str(exe)]
    if provider == 'codex':
        data = Path(root).parent/'Prometheus-Data/Codex'
        data.mkdir(parents=True,exist_ok=True)
        env['CODEX_HOME'] = str(data)
        if setup:
            command += ['login']
        else:
            command += ['--no-daemon','--sandbox','workspace-write','--ask-for-approval','on-request','--cd',str(project)]
            if prompt: command.append(prompt)
    else:
        data = Path(root).parent/'Prometheus-Data/Hermes'
        data.mkdir(parents=True,exist_ok=True)
        env['HERMES_HOME'] = str(data)
        env['PYTHONPYCACHEPREFIX'] = str(data/'pycache')
        env['HERMES_RUNTIME_DIR'] = str(exe.parent.parent/'tools')
        env.pop('HERMES_YOLO_MODE',None)
        env.pop('HERMES_ACCEPT_HOOKS',None)
        configured = (data/'config.yaml').is_file()
        if setup or not configured:
            command += ['setup']
        else:
            command += ['chat','--in',str(project)]
            if prompt: command += ['--query',prompt]
    print(f'Opening {provider} in {project}. Complete or exit its session before ejecting the drive.',flush=True)
    if provider == 'codex' and not setup:
        print('Codex uses your configured provider/account. This handoff may use the internet.',flush=True)
    return subprocess.call(command,cwd=project,env=env)
