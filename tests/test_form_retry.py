from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from custom_components.bloomin8_eink_canvas import config_flow as flow


class FormRetryTests(IsolatedAsyncioTestCase):
    async def test_fields_survive_failed_validation(self):
        mac = "AA:BB:CC:DD:EE:FF"
        inputs = {"host": "192.0.2.9", "name": "Kitchen", "mac_address": mac,
                  flow.CONF_BLE_DEVICE: mac, "ble_auto_wake": False}
        for step in ("manual", "discover_confirm", "reconfigure"):
            with self.subTest(step=step):
                setup = flow.EinkDisplayConfigFlow()
                setup.hass = Mock()
                setup.handler = flow.DOMAIN
                setup.flow_id = "test"
                setup.context = {}
                setup._discovered_selected = "found"
                setup._discovered["found"] = flow._DiscoveredCandidate("frame", "Old", "123", mac)
                setup._get_reconfigure_entry = Mock(return_value=Mock(data={}))
                with (
                    patch.object(flow, "async_ble_wake", return_value=Mock(attempted=False)),
                    patch.object(flow, "validate_input", side_effect=flow.CannotConnect),
                    patch.object(flow, "_ble_wake_possible", return_value=True),
                    patch.object(flow, "_build_ble_selector_options", return_value=[mac]),
                ):
                    result = await getattr(setup, f"async_step_{step}")(inputs.copy())
                self.assertEqual(result["errors"], {"base": "cannot_connect"})
                values = {str(key): (key.description or {}).get("suggested_value")
                          for key in result["data_schema"].schema}
                self.assertEqual({key: values[key] for key in inputs}, inputs)
