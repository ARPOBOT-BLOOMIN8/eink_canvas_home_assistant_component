import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from homeassistant.components.bluetooth.match import BluetoothMatcherIndex

from custom_components.bloomin8_eink_canvas import config_flow


class ServiceDataTests(TestCase):
    def test_proxy_advertisement(self):
        index = BluetoothMatcherIndex()
        manifest = Path(config_flow.__file__).with_name("manifest.json")
        for matcher in json.loads(manifest.read_text())["bluetooth"]:
            index.add(matcher)
        index.build()
        frame = SimpleNamespace(
            name="Living Room", connectable=True, service_uuids=[],
            service_data={"0000180a-0000-1000-8000-00805f9b34fb": b""},
            manufacturer_data={319: bytes.fromhex("0049d083addd4241545430323830")},
        )
        self.assertTrue(index.match(frame))
        self.assertTrue(config_flow._is_probably_bloomin8(frame))
