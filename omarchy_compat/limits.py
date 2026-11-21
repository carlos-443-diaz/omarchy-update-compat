# SPDX-License-Identifier: GPL-3.0-or-later
"""Bound child lifetime, output, and cancellation without orphaned build groups."""
import os
from contextlib import contextmanager
from pathlib import Path
import selectors
import signal
import subprocess
import time

BUILD_SECONDS = 1800
OUTPUT_BYTES = 256 * 1024 * 1024
LOG_BYTES = 8 * 1024 * 1024


@contextmanager
def interruptible():
    def cancel(number, frame):
        raise RuntimeError('Build cancelled; the main package transaction was not started.')
    previous = {number: signal.signal(number, cancel) for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def stop(process):
    # The leader may have exited while a descendant still owns a pipe.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def command(arguments, log_path, *, timeout, output=None, output_limit=OUTPUT_BYTES, env=None):
    if timeout <= 0:
        raise RuntimeError('Build time budget exhausted before starting the next phase.')
    deadline = time.monotonic() + timeout
    process = subprocess.Popen([str(a) for a in arguments], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 'output' if output else 'log')
    selector.register(process.stderr, selectors.EVENT_READ, 'log')
    written = 0
    logged = min(Path(log_path).stat().st_size, LOG_BYTES) if Path(log_path).exists() else 0
    destination = Path(output).open('wb') if output else None
    try:
        with Path(log_path).open('ab') as log:
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise RuntimeError('Build phase exceeded its time budget.')
                for key, _ in selector.select(min(0.5, max(0, deadline - time.monotonic()))):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj); continue
                    if key.data == 'output':
                        written += len(data)
                        if written > output_limit:
                            raise RuntimeError('Build output exceeded its size budget.')
                        destination.write(data)
                    elif logged < LOG_BYTES:
                        log.write(data[:LOG_BYTES - logged]); logged += min(len(data), LOG_BYTES - logged)
            code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if code:
            raise RuntimeError(f'Build phase exited {code}; inspect the saved build log.')
    except BaseException:
        stop(process)
        if output: Path(output).unlink(missing_ok=True)
        raise
    finally:
        if destination: destination.close()
        process.stdout.close(); process.stderr.close(); selector.close()
