import asyncio
import pytest
from bridge.events import Events


async def test_initial_state_edges_unavailable_and_no_duplicates():
    e = Events()
    key = e.create()
    messages = await e.pull(key, 0, 128)
    assert messages[0][1:] == (False, "Initialized")
    e.update("on")
    e.update("on")
    e.update("off")
    messages = await e.pull(key, 0, 128)
    assert [m[1] for m in messages] == [True, False]
    e.update("on")
    e.update("unavailable")
    assert not e.available and not e.motion
    assert [m[1] for m in await e.pull(key, 0, 128)] == [True, False]


async def test_long_poll_wakes_on_motion_and_subscriber_isolation():
    e, other = Events(), Events()
    first, second = e.create(), e.create()
    await e.pull(first, 0, 1)
    await e.pull(second, 0, 1)
    task = asyncio.create_task(e.pull(first, 1, 1))
    await asyncio.sleep(0)
    e.update("on")
    assert (await task)[0][1] is True
    assert (await e.pull(second, 0, 1))[0][1] is True
    assert other.motion is False


def test_expiration_and_limits():
    now = [1000]
    e = Events(clock=lambda: now[0])
    key = e.create(1)
    now[0] += 2
    with pytest.raises(KeyError):
        e.get(key)
    for _ in range(64):
        e.create()
    with pytest.raises(ValueError):
        e.create()
