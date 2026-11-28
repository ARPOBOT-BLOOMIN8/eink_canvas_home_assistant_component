from unittest import TestCase

from custom_components.bloomin8_eink_canvas import config_flow as flow


class WakeAddressTests(TestCase):
    def test_valid_entries(self):
        for address in ("aabbccddeeff", "AA:bb:cc:dd:ee:ff", "aa-bb-cc-dd-ee-ff"):
            data = {flow.CONF_MAC_ADDRESS: address}
            flow._set_wake_address(data)
            self.assertEqual(data[flow.CONF_MAC_ADDRESS], "AA:BB:CC:DD:EE:FF")

    def test_invalid_entries(self):
        for address in ("xxaabbccddeeff", "aa:bb:cc:dd:ee", "aa:bb-cc:dd:ee:ff"):
            with self.assertRaises(flow.InvalidMacAddress):
                flow._set_wake_address({flow.CONF_MAC_ADDRESS: address})
