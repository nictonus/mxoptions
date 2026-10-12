"""Pairing two computers for Flow with a short code.

One computer shows six digits, the other is told them. The code itself never travels: the two run
SRP-6a (RFC 5054, with SHA-256 and the 3072-bit group), a password-authenticated key exchange, in
which each side proves it knows the code and both end up with the same strong secret. Somebody in
the middle of the conversation learns nothing they could try codes against afterwards: every guess
takes a fresh attempt with the computer showing the code, and that one gives up after a few.

From the shared secret both derive the long pairing key that flow.py encrypts the link with."""

import base64
import hashlib
import hmac
import json
import secrets

CODE_DIGITS = 6
ATTEMPTS = 3          # wrong codes after which the computer showing the code withdraws it
OPEN_FOR = 120.0      # s a code stays valid
_IDENTITY = b"mxoptions-flow"


class Group:
    """A prime-order setting for the exchange: the prime N, the generator g and the hash to go with them."""

    def __init__(self, prime_hex, generator, digest):
        self.N = int("".join(prime_hex.split()), 16)
        self.g = generator
        self.digest = digest
        self.size = (self.N.bit_length() + 7) // 8
        self.k = self.number(self.hash(self.pad(self.N), self.pad(self.g)))

    def pad(self, number):
        return number.to_bytes(self.size, "big")

    def hash(self, *parts):
        return self.digest(b"".join(parts)).digest()

    @staticmethod
    def number(data):
        return int.from_bytes(data, "big")

    def secret_of(self, salt, password):
        """x of the RFC: what the code turns into on the side that knows it."""
        return self.number(self.hash(salt, self.hash(_IDENTITY + b":" + password)))


# The 3072-bit group of RFC 5054 (the prime is that of RFC 3526, group 15)
GROUP = Group("""
FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74
020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437
4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED
EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05
98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB
9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B
E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718
3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33
A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7
ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864
D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E2
08E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF
""", 5, hashlib.sha256)


class Mismatch(Exception):
    """The other side does not know the code, or sent something that cannot be right."""


class Host:
    """The side that shows the code (the "server" of the RFC, with a verifier made on the spot)."""

    def __init__(self, code, group=GROUP, salt=None, private=None):
        self.group = group
        self.salt = salt if salt is not None else secrets.token_bytes(16)
        self._v = pow(group.g, group.secret_of(self.salt, code.encode()), group.N)
        self._b = private if private is not None else secrets.randbits(256)
        self.public = (group.k * self._v + pow(group.g, self._b, group.N)) % group.N
        self._key = None
        self._proof = None

    def accept(self, their_public, proof, info=b""):
        """Check the other side's proof (``info`` is what it said about itself, covered by the proof).
        Returns this side's proof for it; raises Mismatch if it does not know the code."""
        group = self.group
        if not 0 < their_public < group.N:
            raise Mismatch("bad public value")
        u = group.number(group.hash(group.pad(their_public), group.pad(self.public)))
        shared = pow(their_public * pow(self._v, u, group.N) % group.N, self._b, group.N)
        key = group.hash(group.pad(shared))
        expected = group.hash(group.pad(their_public), group.pad(self.public), key, info)
        if not hmac.compare_digest(expected, proof):
            raise Mismatch("wrong code")
        self._key, self._proof = key, proof
        self._their_public = their_public
        return key

    def proof(self, info=b""):
        return self.group.hash(self.group.pad(self._their_public), self._proof, self._key, info)


class Client:
    """The side that was told the code."""

    def __init__(self, code, group=GROUP, private=None):
        self.group = group
        self._code = code.encode()
        self._a = private if private is not None else secrets.randbits(256)
        self.public = pow(group.g, self._a, group.N)
        self._key = None
        self._proof = None

    def answer(self, salt, their_public, info=b""):
        """The proof that this side knows the code, for the host's challenge (``info`` rides along, covered by it)."""
        group = self.group
        if not 0 < their_public < group.N:
            raise Mismatch("bad public value")
        u = group.number(group.hash(group.pad(self.public), group.pad(their_public)))
        if u == 0:
            raise Mismatch("bad public value")
        x = group.secret_of(salt, self._code)
        base = (their_public - group.k * pow(group.g, x, group.N)) % group.N
        self._key = group.hash(group.pad(pow(base, self._a + u * x, group.N)))
        self._proof = group.hash(group.pad(self.public), group.pad(their_public), self._key, info)
        return self._proof

    def confirm(self, proof, info=b""):
        """Check the host's proof; returns the shared secret, raises Mismatch if the host did not know the code."""
        expected = self.group.hash(self.group.pad(self.public), self._proof, self._key, info)
        if not hmac.compare_digest(expected, proof):
            raise Mismatch("the other computer did not know the code")
        return self._key


def new_code():
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def clean_code(text):
    """The digits of what the user typed ("123 456" is as good as "123456")."""
    return "".join(ch for ch in str(text) if ch.isdigit())


def pairing_key(shared):
    """The long pairing key both sides keep, from the secret the exchange left them with: in the
    same form as a key made by hand (flow.new_key), 120 bits."""
    raw = hashlib.sha256(b"mxoptions-flow pairing key:" + shared).digest()[:15]
    text = base64.b32encode(raw).decode("ascii")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def info_bytes(info):
    """What a side says about itself (its name, its port), in the one form both sides hash."""
    return json.dumps(info, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
