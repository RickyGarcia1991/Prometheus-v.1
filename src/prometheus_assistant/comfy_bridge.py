"""Portable lifecycle adapter for ComfyUI's documented start_comfyui embedding API.

No custom nodes or model imports occur until main(). A stop request closes admission
first, drains submitted jobs and HTTP writes, then shuts down storage and releases
the parent process's SSD session lock. It never terminates a running generation.
"""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import os
from pathlib import Path
import signal
import sys


class DrainState:
    def __init__(self, stop_file):
        self.stop_file = Path(stop_file)
        self.closing = False
        self.active_writes = 0

    def requested(self):
        self.closing = self.closing or self.stop_file.exists()
        return self.closing

    def drained(self, queue):
        running, pending = queue.get_current_queue_volatile()
        return self.requested() and self.active_writes == 0 and not running and not pending


async def serve(server, port, state):
    from aiohttp import web

    @web.middleware
    async def admission(request, handler):
        if state.requested():
            raise web.HTTPServiceUnavailable(text='Prometheus is finishing media work for shutdown. New requests are paused.')
        writing = request.method not in ('GET', 'HEAD', 'OPTIONS')
        if writing:
            state.active_writes += 1
        try:
            return await handler(request)
        finally:
            if writing:
                state.active_writes -= 1

    server.app.middlewares.insert(0, admission)
    runner = web.AppRunner(server.app, access_log=None)
    publisher = None
    try:
        await server.setup()
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', port).start()
        server.address, server.port = '127.0.0.1', port
        publisher = asyncio.create_task(server.publish_loop())
        print(f'ComfyUI ready: http://127.0.0.1:{port}', flush=True)
        while not state.drained(server.prompt_queue):
            if publisher.done():
                # Even a failed progress publisher must not terminate a job that
                # is still saving files. Close admission and let the queue drain.
                state.closing = True
            await asyncio.sleep(.5)
        if publisher.done():
            await publisher
            raise RuntimeError('ComfyUI publisher ended unexpectedly.')
        print('All queued jobs finished. Closing ComfyUI cleanly.', flush=True)
    finally:
        state.closing = True
        # Close sockets before runner cleanup so a browser cannot hold the drive.
        for websocket in list(server.sockets.values()):
            with contextlib.suppress(Exception):
                await websocket.close()
        await runner.cleanup()
        if publisher is not None:
            publisher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await publisher
        if server.client_session is not None:
            await server.client_session.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--comfy-root', type=Path, required=True)
    parser.add_argument('--shutdown-request', type=Path, required=True)
    parser.add_argument('comfy_args', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    root = options.comfy_root.resolve(strict=True)
    args = options.comfy_args[1:] if options.comfy_args[:1] == ['--'] else options.comfy_args
    state = DrainState(options.shutdown_request)
    if state.requested():
        return 0
    sys.path.insert(0, str(root))
    sys.argv = [str(root/'main.py'), *args]
    os.chdir(root)
    import main as comfy_main
    loop, server, _ = comfy_main.start_comfyui()
    signal.signal(signal.SIGINT, lambda *_: setattr(state, 'closing', True))
    try:
        loop.run_until_complete(serve(server, comfy_main.args.port, state))
    finally:
        server.asset_manager.shutdown()
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
