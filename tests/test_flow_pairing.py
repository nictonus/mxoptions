"""Pairing with a short code: the key exchange against the published test vector, and the ways it must fail."""

import ctypes
import ctypes.util
import hashlib
import unittest

import tests.support  # noqa: F401
from mxoptions import flow, flow_pairing as pairing

# RFC 5054, appendix B: SHA-1, the 1024-bit group, user "alice", password "password123"
RFC_GROUP = pairing.Group("""
EEAF0AB9ADB38DD69C33F80AFA8FC5E86072618775FF3C0B9EA2314C9C256576
D674DF7496EA81D3383B4813D692C6E0E0D5D8E250B98BE48E495C1D6089DAD1
5DC7D7B46154D6B6CE8EF4AD69B15D4982559B297BCF1885C529F566660E57EC
68EDBC3C05726CC02FD4CBF4976EAA9AFD5138FE8376435B9FC61D2FC0EB06E3
""", 2, hashlib.sha1)
RFC_SALT = bytes.fromhex("BEB25379D1A8581EB5A727673A2441EE")
RFC_A = int("60975527035CF2AD1989806F0407210BC81EDC04E2762A56AFD529DDDA2D4393", 16)
RFC_B = int("E487CB59D31AC550471E81F00F6928E01DDA08E974A004F49E61F5D105284D20", 16)
RFC_HOST_PUBLIC = int(
    "BD0C61512C692C0CB6D041FA01BB152D4916A1E77AF46AE105393011BAF38964DC46A0670DD125B95A981652236F99D9B681CBF87837EC99"
    "6C6DA04453728610D0C6DDB58B318885D7D82C7F8DEB75CE7BD4FBAA37089E6F9C6059F388838E7A00030B331EB76840910440B1B27AAEAE"
    "EB4012B7D7665238A8E3FB004B117B58", 16)
RFC_CLIENT_PUBLIC = int(
    "61D5E490F6F1B79547B0704C436F523DD0E560F0C64115BB72557EC44352E8903211C04692272D8B2D1A5358A2CF1B6E0BFCF99F921530EC"
    "8E39356179EAE45E42BA92AEACED825171E1E8B9AF6D9C03E1327F44BE087EF06530E69F66615261EEF54073CA11CF5858F0EDFDFE15EFEA"
    "B349EF5D76988A3672FAC47B0769447B", 16)
RFC_SHARED = int(
    "B0DC82BABCF30674AE450C0287745E7990A3381F63B387AAF271A10D233861E359B48220F7C4693C9AE12B0A6F67809F0876E2D013800D6C"
    "41BB59B6D5979B5C00A172B4A2A5903A0BDCAF8A709585EB2AFAFA8F3499B200210DCC1F10EB33943CD67FC88A2F39A4BE5BEC4EC0A3212D"
    "C346D7E474B29EDE8A469FFECA686E5A", 16)


class Published(unittest.TestCase):
    """The numbers of the RFC come out of this implementation."""

    def setUp(self):
        identity, pairing._IDENTITY = pairing._IDENTITY, b"alice"
        self.addCleanup(setattr, pairing, "_IDENTITY", identity)

    def test_multiplier_and_public_values(self):
        self.assertEqual(RFC_GROUP.k, int("7556AA045AEF2CDD07ABAF0F665C3E818913186F", 16))
        self.assertEqual(RFC_GROUP.secret_of(RFC_SALT, b"password123"), int("94B7555AABE9127CC58CCF4993DB6CF84D16C124", 16))
        host = pairing.Host("password123", RFC_GROUP, salt=RFC_SALT, private=RFC_B)
        client = pairing.Client("password123", RFC_GROUP, private=RFC_A)
        self.assertEqual(host.public, RFC_HOST_PUBLIC)
        self.assertEqual(client.public, RFC_CLIENT_PUBLIC)

    def test_both_sides_reach_the_published_secret(self):
        host = pairing.Host("password123", RFC_GROUP, salt=RFC_SALT, private=RFC_B)
        client = pairing.Client("password123", RFC_GROUP, private=RFC_A)
        proof = client.answer(host.salt, host.public)
        expected = hashlib.sha1(RFC_GROUP.pad(RFC_SHARED)).digest()
        self.assertEqual(host.accept(client.public, proof), expected)
        self.assertEqual(client.confirm(host.proof()), expected)


class Exchange(unittest.TestCase):
    def pair(self, shown, typed, host_info=b"", client_info=b"", seen_client_info=None):
        host, client = pairing.Host(shown), pairing.Client(typed)
        proof = client.answer(host.salt, host.public, client_info)
        key = host.accept(client.public, proof, client_info if seen_client_info is None else seen_client_info)
        return key, client.confirm(host.proof(host_info), host_info)

    def test_the_same_code_gives_both_the_same_key(self):
        at_host, at_client = self.pair("482913", "482913")
        self.assertEqual(at_host, at_client)
        key = pairing.pairing_key(at_host)
        self.assertTrue(flow.key_usable(key))
        self.assertRegex(key, r"^([A-Z2-7]{4}-){5}[A-Z2-7]{4}$")

    def test_every_pairing_gives_another_key(self):
        self.assertNotEqual(self.pair("482913", "482913")[0], self.pair("482913", "482913")[0])

    def test_a_wrong_code_is_refused(self):
        with self.assertRaises(pairing.Mismatch):
            self.pair("482913", "482914")

    def test_what_a_side_says_about_itself_cannot_be_changed_on_the_way(self):
        info = pairing.info_bytes({"name": "laptop", "port": 47846})
        self.pair("482913", "482913", client_info=info)
        with self.assertRaises(pairing.Mismatch):
            self.pair("482913", "482913", client_info=info, seen_client_info=pairing.info_bytes({"name": "laptop", "port": 1}))

    def test_a_host_that_does_not_know_the_code_is_found_out(self):
        client = pairing.Client("482913")
        fake = pairing.Host("000000")
        client.answer(fake.salt, fake.public)
        with self.assertRaises(pairing.Mismatch):
            client.confirm(b"\0" * 32)

    def test_degenerate_public_values_are_refused(self):
        host, client = pairing.Host("482913"), pairing.Client("482913")
        for bad in (0, pairing.GROUP.N, pairing.GROUP.N * 2):
            with self.assertRaises(pairing.Mismatch):
                host.accept(bad, b"\0" * 32)
            with self.assertRaises(pairing.Mismatch):
                client.answer(host.salt, bad)

    def test_codes(self):
        code = pairing.new_code()
        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual(pairing.clean_code(" 482 913\n"), "482913")


class Groups(unittest.TestCase):
    """The primes are the standard ones: compared with the tables of the system's OpenSSL where it has them."""

    def test_against_openssl(self):
        name = ctypes.util.find_library("crypto")
        if not name:
            self.skipTest("no libcrypto")
        lib = ctypes.CDLL(name)

        class Entry(ctypes.Structure):
            _fields_ = [("id", ctypes.c_char_p), ("g", ctypes.c_void_p), ("N", ctypes.c_void_p)]

        try:
            lib.SRP_get_default_gN.restype = ctypes.POINTER(Entry)
            lib.SRP_get_default_gN.argtypes = [ctypes.c_char_p]
            lib.BN_bn2hex.restype = ctypes.c_void_p
            lib.BN_bn2hex.argtypes = [ctypes.c_void_p]
        except AttributeError:
            self.skipTest("this OpenSSL has no SRP tables")
        for size, group in ((b"3072", pairing.GROUP), (b"1024", RFC_GROUP)):
            entry = lib.SRP_get_default_gN(size)
            if not entry:
                self.skipTest("this OpenSSL has no SRP tables")
            self.assertEqual(int(ctypes.string_at(lib.BN_bn2hex(entry.contents.N)), 16), group.N)
            self.assertEqual(int(ctypes.string_at(lib.BN_bn2hex(entry.contents.g)), 16), group.g)


if __name__ == "__main__":
    unittest.main()
