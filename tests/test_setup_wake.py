import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from custom_components.bloomin8_eink_canvas import config_flow as flow


class SetupWakeTests(IsolatedAsyncioTestCase):
    async def test_delayed_http_after_one_wake(self):
        read = AsyncMock(side_effect=[None, {"name": "Frame", "sn": "123"}])
        with (
            patch.object(flow, "EinkCanvasApiClient", return_value=Mock(get_device_info=read)),
            patch.object(flow, "async_ble_wake") as wake,
            patch.object(flow.asyncio, "sleep", new_callable=AsyncMock),
        ):
            wake.return_value.attempted = True
            result = await flow.validate_input(Mock(), {"host": "frame", "name": "Frame"}, wake_address="AA:BB:CC:DD:EE:FF")
        self.assertEqual(result["sn"], "123")
        wake.assert_awaited_once()
        self.assertEqual(read.await_count, 2)

    async def test_deadline_stops_a_stalled_http_request(self):
        async def stall(**kwargs):
            await asyncio.Event().wait()

        deadline = asyncio.timeout(0.01)
        with (
            patch.object(flow, "EinkCanvasApiClient", return_value=Mock(get_device_info=stall)),
            patch.object(flow, "_async_wake_for_setup", return_value=True),
            patch.object(flow.asyncio, "timeout", return_value=deadline) as timeout,
        ):
            with self.assertRaises(flow.CannotConnect):
                await flow.validate_input(Mock(), {"host": "frame", "name": "Frame"})
            timeout.assert_called_once_with(10)
