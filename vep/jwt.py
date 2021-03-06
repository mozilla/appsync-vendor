# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is PyVEP
#
# The Initial Developer of the Original Code is the Mozilla Foundation.
# Portions created by the Initial Developer are Copyright (C) 2011
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Ryan Kelly (rkelly@mozilla.com)
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****
"""

Utilities for dealing with Signed JSON Web Tokens.

"""

import os
import struct
import hashlib
import M2Crypto

from vep.utils import decode_bytes, encode_bytes
from vep.utils import decode_json_bytes, encode_json_bytes


class JWT(object):
    """Class for parsing signed JSON Web Tokens.

    To parse a JWT from a bytestring, use JTW.parse(data).  The default
    constructor is only for internal purposes.
    """

    def __init__(self, algorithm, payload, signature, signed_data):
        self.algorithm = algorithm
        self.payload = payload
        self.signature = signature
        self.signed_data = signed_data

    @classmethod
    def parse(cls, jwt):
        """Parse a JWT from a string."""
        algorithm, payload, signature = jwt.split(".")
        signed_data = algorithm + "." + payload
        try:
            algorithm = decode_json_bytes(algorithm)["alg"]
        except KeyError:
            raise ValueError("badly formed JWT")
        payload = decode_json_bytes(payload)
        signature = decode_bytes(signature)
        return cls(algorithm, payload, signature, signed_data)

    @classmethod
    def generate(cls, payload, key):
        """Generate and sign a JWT for a dict payload."""
        alg = key.__class__.__name__[:-3]
        algorithm = encode_json_bytes({"alg": alg})
        payload = encode_json_bytes(payload)
        signature = encode_bytes(key.sign(".".join((algorithm, payload))))
        return ".".join((algorithm, payload, signature))

    def check_signature(self, key_data):
        """Check that the JWT was signed with the given key."""
        if not self.algorithm.startswith(key_data["algorithm"]):
            return False
        key = load_key(self.algorithm, key_data)
        return key.verify(self.signed_data, self.signature)


def load_key(algorithm, key_data):
    """Load a Key object from the given data."""
    if not algorithm.isalnum():
        msg = "unknown signing algorithm: %s" % (algorithm,)
        raise ValueError(msg)
    try:
        key_class = globals()[algorithm + "Key"]
    except KeyError:
        msg = "unknown signing algorithm: %s" % (algorithm,)
        raise ValueError(msg)
    return key_class(key_data)


class Key(object):
    """Generic base class for Key objects."""

    def verify(self, signed_data, signature):
        raise NotImplementedError

    def sign(self, data):
        raise NotImplementedError


#
#  RSA keys, implemented using the RSA support in M2Crypto.
#

class RSKey(object):

    SIZE = None
    HASHMOD = None

    def __init__(self, data):
        e = int2mpint(int(data["e"]))
        n = int2mpint(int(data["n"]), pad=self.SIZE + 1)
        self.rsa = M2Crypto.RSA.new_pub_key((e, n))

    def verify(self, signed_data, signature):
        digest = self.HASHMOD(signed_data).digest()
        try:
            return self.rsa.verify(digest, signature, self.HASHNAME)
        except M2Crypto.RSA.RSAError:
            return False


class RS64Key(RSKey):
    SIZE = 64
    HASHNAME = "sha256"
    HASHMOD = hashlib.sha256


class RS128Key(RSKey):
    SIZE = 128
    HASHNAME = "sha256"
    HASHMOD = hashlib.sha256


class RS256Key(RSKey):
    SIZE = 256
    HASHNAME = "sha256"
    HASHMOD = hashlib.sha256


#
#  DSA keys, implemented by hand because I haven't figured out how to
#  map what M2Crypto provides onto what the browserid.org server does.
#

class DSKey(object):

    BITLENGTH = None
    HASHMOD = None

    def __init__(self, data):
        self.p = long(data["p"], 16)
        self.q = long(data["q"], 16)
        self.g = long(data["g"], 16)
        self.y = long(data["y"], 16)
        if "x" in data:
            self.x = long(data["x"], 16)
        else:
            self.x = None

    def verify(self, signed_data, signature):
        p, q, g, y = self.p, self.q, self.g, self.y
        signature = signature.encode("hex")
        hexlength = self.BITLENGTH / 4
        signature = signature.rjust(hexlength * 2, "0")
        if len(signature) != hexlength * 2:
            return False
        r = long(signature[:hexlength], 16)
        s = long(signature[hexlength:], 16)
        if r <= 0 or r >= q:
            return False
        if s <= 0 or s >= q:
            return False
        w = modinv(s, q)
        u1 = (long(self.HASHMOD(signed_data).hexdigest(), 16) * w) % q
        u2 = (r * w) % q
        v = ((pow(g, u1, p) * pow(y, u2, p)) % p) % q
        return (v == r)

    def sign(self, data):
        p, q, g, y, x = self.p, self.q, self.g, self.y, self.x
        if not x:
            raise ValueError("private key not present")
        # We need to do lots of if-not-this-then-start-over type tests.
        while True:
            k = long(os.urandom(self.BITLENGTH / 8).encode("hex"), 16) % q
            if k == 0:
                continue
            r = pow(g, k, p) % q
            if r == 0:
                continue
            h = (long(self.HASHMOD(data).hexdigest(), 16) + (x * r)) % q
            s = (modinv(k, q) * h) % q
            if s == 0:
                continue
            break
        assert 0 < r < q
        assert 0 < s < q
        bytelength = self.BITLENGTH / 8
        r_bytes = int2mpint(r)[4:].rjust(bytelength, "\x00")
        s_bytes = int2mpint(s)[4:].rjust(bytelength, "\x00")
        return r_bytes + s_bytes


class DS128Key(DSKey):
    BITLENGTH = 160
    HASHMOD = hashlib.sha1


class DS256Key(DSKey):
    BITLENGTH = 256
    HASHMOD = hashlib.sha256


#
#  Other helper functions.
#


def int2mpint(x, pad=None):
    """Convert a Python integer into a string in OpenSSL's MPINT format."""
    # The horror...the horror...
    bytes = []
    while x:
        bytes.append(chr(x % 256))
        x = x / 256
    if pad is not None:
        while len(bytes) < pad:
            bytes.append("\x00")
    bytes.reverse()
    return struct.pack(">I", len(bytes)) + "".join(bytes)


def modinv(a, m):
    """Find the modular inverse of a, with modulus m."""
    # This is a transliteration of the algorithm as it was described
    # to me by Wikipedia, using the Extended Euclidean Algorithm.
    x = 0
    lastx = 1
    y = 1
    lasty = 0
    b = m
    while b != 0:
        a, (q, b) = b, divmod(a, b)
        x, lastx = lastx - (q * x), x
        y, lasty = lasty - (q * y), y
    return lastx % m
