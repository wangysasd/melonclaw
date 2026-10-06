"""Regression cases for the background SSE stream lifecycle."""

import asyncio
import gc

import pytest

from melonclaw.api.background_stream import (
    _tasks,
    close_stream_tasks,
    persistent_stream,
)


def test_slow_subscriber_applies_bounded_backpressure_and_resumes():
    async def run():
        started_blocked_put = asyncio.Event()
        source_finished = asyncio.Event()
        produced = 0

        async def events():
            nonlocal produced
            for index in range(100):
                produced += 1
                if index == 65:
                    started_blocked_put.set()
                yield str(index).encode()
            source_finished.set()

        stream = persistent_stream(events())
        try:
            assert await anext(stream) == b"0"
            await asyncio.wait_for(started_blocked_put.wait(), timeout=1)
            await asyncio.sleep(0)
            assert produced == 66
            assert not source_finished.is_set()

            assert await anext(stream) == b"1"
            await asyncio.wait_for(_wait_for_produced(lambda: produced > 66), timeout=1)
            await stream.aclose()
            await asyncio.wait_for(source_finished.wait(), timeout=1)
        finally:
            await stream.aclose()
            await close_stream_tasks()

    asyncio.run(run())


async def _wait_for_produced(predicate):
    while not predicate():
        await asyncio.sleep(0)


def test_disconnect_keeps_producer_running_and_closes_source():
    async def run():
        release = asyncio.Event()
        completed = asyncio.Event()
        source_closed = asyncio.Event()

        async def events():
            try:
                yield b"started"
                await release.wait()
                for _ in range(100):
                    yield b"progress"
                completed.set()
            finally:
                source_closed.set()

        stream = persistent_stream(events())
        try:
            assert await anext(stream) == b"started"
            await stream.aclose()
            release.set()
            await asyncio.wait_for(completed.wait(), timeout=1)
            await asyncio.wait_for(source_closed.wait(), timeout=1)
        finally:
            await close_stream_tasks()

    asyncio.run(run())


@pytest.mark.xfail(
    strict=True,
    reason="the producer done callback discards tasks without retrieving exceptions",
)
def test_producer_exception_is_retrieved():
    async def run():
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        unhandled = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

        async def events():
            yield b"started"
            raise RuntimeError("producer failed")

        stream = persistent_stream(events())
        try:
            assert await anext(stream) == b"started"
            producer_task = next(iter(_tasks))
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
            await asyncio.wait_for(
                _wait_for_task_reaped(producer_task), timeout=1
            )
            await stream.aclose()
            del stream
            del producer_task
            gc.collect()
            await asyncio.sleep(0)
            assert not unhandled
        finally:
            await close_stream_tasks()
            loop.set_exception_handler(previous_handler)

    asyncio.run(run())


async def _wait_for_task_reaped(task):
    while not task.done() or task in _tasks:
        await asyncio.sleep(0)


def test_shutdown_cancels_producer_closes_source_and_reaps_task():
    async def run():
        source_closed = asyncio.Event()

        async def events():
            try:
                yield b"started"
                await asyncio.Event().wait()
            finally:
                source_closed.set()

        stream = persistent_stream(events())
        assert await anext(stream) == b"started"
        await stream.aclose()
        await close_stream_tasks()
        assert source_closed.is_set()
        assert not _tasks

    asyncio.run(run())


@pytest.mark.xfail(
    strict=True,
    reason="persistent_stream cannot run its cleanup if closed before first iteration",
)
def test_close_before_first_iteration_closes_source_iterator():
    async def run():
        class TrackedIterator:
            def __init__(self):
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self):
                self.closed = True

        source = TrackedIterator()
        stream = persistent_stream(source)
        await stream.aclose()
        assert source.closed
        assert not _tasks

    asyncio.run(run())


def test_normal_completion_releases_source_and_background_task():
    async def run():
        source_closed = asyncio.Event()

        async def events():
            try:
                yield b"one"
                yield b"two"
            finally:
                source_closed.set()

        stream = persistent_stream(events())
        try:
            assert [event async for event in stream] == [b"one", b"two"]
            await asyncio.wait_for(source_closed.wait(), timeout=1)
            await asyncio.wait_for(_wait_for_no_background_tasks(), timeout=1)
            assert not _tasks
        finally:
            await close_stream_tasks()

    asyncio.run(run())


async def _wait_for_no_background_tasks():
    while _tasks:
        await asyncio.sleep(0)
