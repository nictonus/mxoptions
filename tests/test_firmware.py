import unittest

import tests.support  # noqa: F401
from mxoptions import firmware

LISTING = {"Devices": [
    {"Name": "Bolt Receiver", "DeviceId": "aaa", "Version": "MPR05.05_B0030", "VersionBootloader": "BOT30.01_B0010",
     "Flags": ["updatable", "signed-payload"]},
    {"Name": "MX Master 4", "DeviceId": "bbb", "ParentDeviceId": "aaa", "Version": "RBM27.02_B0017",
     "VersionBootloader": "LD04.00_B0000", "Flags": ["updatable"]},
    {"Name": "Some SSD", "DeviceId": "ccc", "Version": "1.0", "Flags": []},
]}


class Listing(unittest.TestCase):
    def test_mouse_first_then_its_receiver(self):
        found = firmware.find(LISTING, "MX Master 4")
        self.assertEqual([(e["name"], e["version"]) for e in found],
                         [("MX Master 4", "RBM27.02_B0017"), ("Bolt Receiver", "MPR05.05_B0030")])
        self.assertTrue(found[0]["updatable"])
        self.assertEqual(found[0]["updates"], [])

    def test_name_is_matched_whatever_its_case(self):
        self.assertEqual(len(firmware.find(LISTING, " mx master 4 ")), 2)

    def test_unknown_mouse_or_empty_answer(self):
        self.assertEqual(firmware.find(LISTING, "MX Anywhere 3"), [])
        self.assertEqual(firmware.find({}, "MX Master 4"), [])
        self.assertEqual(firmware.find(LISTING, ""), [])

    def test_mouse_without_a_receiver(self):
        listing = {"Devices": [{"Name": "MX Master 4", "DeviceId": "bbb", "Version": "1"}]}
        self.assertEqual(len(firmware.find(listing, "MX Master 4")), 1)

    def test_update_command_names_the_device(self):
        self.assertEqual(firmware.update_command({"id": "bbb"}), "fwupdmgr update bbb")


if __name__ == "__main__":
    unittest.main()
