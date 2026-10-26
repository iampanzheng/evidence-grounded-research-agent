"""Terminate an in-flight synchronous remote request at a wall-clock boundary.

An isolated child process is used rather than a worker thread: Python cannot
reliably stop a blocked thread and returning while it runs risks billable
requests and process leaks. The remote provider may have received a request
before termination, so the caller must not automatically retry it.
"""

from __future__ import annotations

import multiprocessing
import time
from multiprocessing.connection import Connection

from p2_research.application.research.llm import ChatClient, ChatCompletion


class RemoteDeadlineExceeded(Exception):
    """A live child was terminated after its allotted time expired."""


class RemoteWorkerFailed(Exception):
    """The isolated call failed or its response was not delivered."""


def _remote_worker(sender: Connection, client: ChatClient, messages: list[dict[str, str]]) -> None:
    try:
        completion = client.complete(messages)
        sender.send(("ok", completion))
    except BaseException:
        # Never send exception representations: transport exceptions may include
        # provider URLs, sensitive headers or portions of the request.
        try:
            sender.send(("error", None))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        sender.close()


def call_remote_bounded(
    client: ChatClient,
    messages: list[dict[str, str]],
    *,
    timeout_seconds: float,
) -> ChatCompletion:
    """Invoke a pickleable client in a killable process with a finite wall timeout.

    Uses spawn, not fork, so the function is safe to call from a multithreaded
    host without inheriting its open network connections or locked mutexes.
    The client must be pickleable (the built-in OpenAI-compatible client is).
    """
    if timeout_seconds <= 0:
        raise RemoteDeadlineExceeded
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_remote_worker, args=(sender, client, messages), daemon=True)
    started = time.monotonic()
    try:
        worker.start()
        sender.close()
        while True:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise RemoteDeadlineExceeded
            if receiver.poll(min(remaining, 0.05)):
                try:
                    kind, value = receiver.recv()
                except (EOFError, OSError) as exc:
                    raise RemoteWorkerFailed from exc
                if kind == "ok" and isinstance(value, ChatCompletion):
                    return value
                raise RemoteWorkerFailed
            if not worker.is_alive():
                raise RemoteWorkerFailed
    except (OSError, ValueError, TypeError) as exc:
        raise RemoteWorkerFailed from exc
    finally:
        receiver.close()
        sender.close()
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=0.5)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=0.5)
