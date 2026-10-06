import asyncio

from melonclaw.api.background_stream import close_stream_tasks, persistent_stream


def test_unsubscribe_does_not_cancel_execution_and_does_not_block_on_full_queue():
    async def run():
        release = asyncio.Event()
        completed = asyncio.Event()
        async def events():
            yield b"started"
            await release.wait()
            for _ in range(100):
                yield b"progress"
            completed.set()
        stream = persistent_stream(events())
        assert await anext(stream) == b"started"
        await stream.aclose()
        release.set()
        await asyncio.wait_for(completed.wait(), 1)
        await close_stream_tasks()
    asyncio.run(run())


def test_shutdown_cancels_execution_and_releases_generator():
    async def run():
        closed = asyncio.Event()
        async def events():
            try:
                yield b"started"
                await asyncio.Event().wait()
            finally:
                closed.set()
        stream = persistent_stream(events())
        await anext(stream)
        await stream.aclose()
        await close_stream_tasks()
        assert closed.is_set()
    asyncio.run(run())
