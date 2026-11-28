from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from custom_components import bloomin8_eink_canvas as integration
from custom_components.bloomin8_eink_canvas.button import EinkSleepButton


class FrameTargetTests(IsolatedAsyncioTestCase):
    async def test_sleep_only_targets_pressed_frame(self):
        hass = Mock()
        hass.services.async_call = AsyncMock()
        entries = {key: Mock(entry_id=key) for key in ("kitchen", "hall")}
        hass.data = {integration.DOMAIN: entries}
        hass.config_entries.async_entries.return_value = list(entries.values())
        hass.config_entries.async_get_entry.side_effect = entries.get
        registry = Mock()
        with patch.object(integration.er, "async_get", return_value=registry):
            for key, entry in entries.items():
                button = EinkSleepButton(hass, entry, key, key)
                button.entity_id = f"button.{key}_sleep"
                registry.async_get.side_effect = {button.entity_id: Mock(config_entry_id=key)}.get
                await button.async_press()
                data = hass.services.async_call.await_args.args[2]
                targets = integration._get_entries_from_service_call(hass, Mock(data=data))
                self.assertEqual(targets, [entry])
