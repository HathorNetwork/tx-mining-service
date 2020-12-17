"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import re
import struct
from enum import IntEnum
from typing import Any, Dict, List, Match, Optional, Pattern, Type, Union

from txstratum.commons.conf import HathorSettings
from txstratum.commons.exceptions import ScriptError
from txstratum.commons.utils import get_address_b58_from_public_key_hash, get_address_b58_from_redeem_script_hash

settings = HathorSettings()


def re_compile(pattern: str) -> Pattern[bytes]:
    """ Transform a given script pattern into a regular expression.

    The script pattern is like a regular expression, but you may include five
    special symbols:
      (i) OP_DUP, OP_HASH160, and all other opcodes;
     (ii) DATA_<length>: data with the specified length;
    (iii) NUMBER: a 4-byte integer;
     (iv) BLOCK: a variable length block, to be parsed later

    Example:
    >>> r = re_compile(
    ...     '^(?:DATA_4 OP_GREATERTHAN_TIMESTAMP)? '
    ...     'OP_DUP OP_HASH160 (DATA_20) OP_EQUALVERIFY OP_CHECKSIG$'
    ... )

    :return: A compiled regular expression matcher
    :rtype: :py:class:`re.Pattern`
    """

    def _to_byte_pattern(m: Match[bytes]) -> bytes:
        x = m.group().decode('ascii').strip()
        if x.startswith('OP_'):
            return bytes([Opcode[x]])
        elif x.startswith('DATA_'):
            length = int(m.group()[5:])
            return _re_pushdata(length)
        elif x.startswith('NUMBER'):
            return b'.{5}'
        elif x.startswith('BLOCK'):
            return b'.*'
        else:
            raise ValueError('Invalid opcode: {}'.format(x))

    p = pattern.encode('ascii')
    p = re.sub(rb'\s*([A-Z0-9_]+)\s*', _to_byte_pattern, p)
    return re.compile(p, re.DOTALL)


def _re_pushdata(length: int) -> bytes:
    """ Create a regular expression that matches a data block with a given length.

    :return: A non-compiled regular expression
    :rtype: bytes
    """
    ret = [bytes([Opcode.OP_PUSHDATA1]), bytes([length]), b'.{', str(length).encode('ascii'), b'}']

    if length <= 75:
        # for now, we accept <= 75 bytes with OP_PUSHDATA1. It's optional
        ret.insert(1, b'?')

    return b''.join(ret)


class Opcode(IntEnum):
    OP_0 = 0x50
    OP_1 = 0x51
    OP_2 = 0x52
    OP_3 = 0x53
    OP_4 = 0x54
    OP_5 = 0x55
    OP_6 = 0x56
    OP_7 = 0x57
    OP_8 = 0x58
    OP_9 = 0x59
    OP_10 = 0x5a
    OP_11 = 0x5b
    OP_12 = 0x5c
    OP_13 = 0x5d
    OP_14 = 0x5e
    OP_15 = 0x5f
    OP_16 = 0x60
    OP_DUP = 0x76
    OP_EQUAL = 0x87
    OP_EQUALVERIFY = 0x88
    OP_CHECKSIG = 0xAC
    OP_HASH160 = 0xA9
    OP_PUSHDATA1 = 0x4C
    OP_GREATERTHAN_TIMESTAMP = 0x6F
    OP_CHECKMULTISIG = 0xAE
    OP_CHECKDATASIG = 0xBA
    OP_DATA_STREQUAL = 0xC0
    OP_DATA_GREATERTHAN = 0xC1
    OP_FIND_P2PKH = 0xD0
    OP_DATA_MATCH_VALUE = 0xD1


class HathorScript:
    """This class is supposes to being a helper creating the scripts. It abstracts
    some of the corner cases when building the script.

    For eg, when pushing data to the stack, we may or may not have to use OP_PUSHDATA.
    This is the sequence we have to add to the script:
    - len(data) <= 75: [len(data) data]
    - len(data) > 75: [OP_PUSHDATA1 len(data) data]

    pushData abstracts this differences and presents an unique interface.
    """

    def __init__(self) -> None:
        self.data = b''

    def addOpcode(self, opcode: Opcode) -> None:
        self.data += bytes([opcode])

    def pushData(self, data: Union[int, bytes]) -> None:
        if isinstance(data, int):
            if data > 4294967295:
                n = struct.pack('!Q', data)
            elif data > 65535:
                n = struct.pack('!I', data)
            elif data > 255:
                n = struct.pack('!H', data)
            else:
                n = struct.pack('!B', data)
            data = n
        if len(data) <= 75:
            self.data += (bytes([len(data)]) + data)
        else:
            self.data += (bytes([Opcode.OP_PUSHDATA1]) + bytes([len(data)]) + data)


class P2PKH:
    re_match = re_compile('^(?:(DATA_4) OP_GREATERTHAN_TIMESTAMP)? '
                          'OP_DUP OP_HASH160 (DATA_20) OP_EQUALVERIFY OP_CHECKSIG$')

    def __init__(self, address: str, timelock: Optional[int] = None) -> None:
        """This class represents the pay to public hash key script. It enables the person
        who has the corresponding private key of the address to spend the tokens.

        This script validates the signature and public key on the corresponding input
        data. The public key is first checked against the script address and then the
        signature is verified, which means the sender owns the corresponding private key.

        Output script and the corresponding input data are usually represented like:
        input data: OP_DUP OP_HASH160 <pubKeyHash> OP_EQUALVERIFY OP_CHECKSIG
        output script: <sig> <pubKey>

        :param address: address to send tokens
        :type address: string(base58)

        :param timelock: timestamp until when it's locked
        :type timelock: int
        """
        self.address = address
        self.timelock = timelock

    def to_human_readable(self) -> Dict[str, Any]:
        ret: Dict[str, Any] = {}
        ret['type'] = 'P2PKH'
        ret['address'] = self.address
        ret['timelock'] = self.timelock
        return ret

    @classmethod
    def create_output_script(cls, address: bytes, timelock: Optional[Any] = None) -> bytes:
        """
        :param address: address to send tokens
        :type address: bytes

        :param timelock: timestamp until when the output is locked
        :type timelock: bytes

        :rtype: bytes
        """
        assert len(address) == 25
        public_key_hash = address[1:-4]
        s = HathorScript()
        if timelock:
            s.pushData(timelock)
            s.addOpcode(Opcode.OP_GREATERTHAN_TIMESTAMP)
        s.addOpcode(Opcode.OP_DUP)
        s.addOpcode(Opcode.OP_HASH160)
        s.pushData(public_key_hash)
        s.addOpcode(Opcode.OP_EQUALVERIFY)
        s.addOpcode(Opcode.OP_CHECKSIG)
        return s.data

    @classmethod
    def create_input_data(cls, public_key_bytes: bytes, signature: bytes) -> bytes:
        """
        :param private_key: key corresponding to the address we want to spend tokens from
        :type private_key: :py:class:`cryptography.hazmat.primitives.asymmetric.ec.EllipticCurvePrivateKey`

        :rtype: bytes
        """
        s = HathorScript()
        s.pushData(signature)
        s.pushData(public_key_bytes)
        return s.data

    @classmethod
    def parse_script(cls, script: bytes) -> Optional['P2PKH']:
        """Checks if the given script is of type p2pkh. If it is, returns the P2PKH object.
        Otherwise, returns None.

        :param script: script to check
        :type script: bytes

        :rtype: :py:class:`hathor.transaction.scripts.P2PKH` or None
        """
        match = cls.re_match.search(script)
        if match:
            groups = match.groups()
            timelock = None
            pushdata_timelock = groups[0]
            if pushdata_timelock:
                timelock_bytes = pushdata_timelock[1:]
                timelock = struct.unpack('!I', timelock_bytes)[0]
            pushdata_address = groups[1]
            public_key_hash = get_pushdata(pushdata_address)
            address_b58 = get_address_b58_from_public_key_hash(public_key_hash)
            return cls(address_b58, timelock)
        return None


# TODO: `IAddress` class for defining the common interface of `Union[MultiSig, P2PKH]`
class MultiSig:
    re_match = re_compile('^(?:(DATA_4) OP_GREATERTHAN_TIMESTAMP)? ' 'OP_HASH160 (DATA_20) OP_EQUAL$')

    def __init__(self, address: str, timelock: Optional[Any] = None) -> None:
        """This class represents the multi signature script (MultiSig). It enables the group of persons
        who has the corresponding private keys of the address to spend the tokens.

        This script validates the signatures and public keys on the corresponding input
        data.

        Output script and the corresponding input data are usually represented like:
        output script: OP_HASH160 <redeemScriptHash> OP_EQUAL
        input data: <sig1> ... <sigM> <redeemScript>

        :param address: address to send tokens
        :type address: string(base58)

        :param timelock: timestamp until when it's locked
        :type timelock: int
        """
        self.address = address
        self.timelock = timelock

    def to_human_readable(self) -> Dict[str, Any]:
        """ Decode MultiSig class to dict with its type and data

            :return: Dict with MultiSig info
            :rtype: Dict[str:]
        """
        ret: Dict[str, Any] = {}
        ret['type'] = 'MultiSig'
        ret['address'] = self.address
        ret['timelock'] = self.timelock
        return ret

    @classmethod
    def create_output_script(cls, address: bytes, timelock: Optional[Any] = None) -> bytes:
        """
        :param address: address to send tokens
        :type address: bytes

        :param timelock: timestamp until when the output is locked
        :type timelock: bytes

        :rtype: bytes
        """
        assert len(address) == 25
        redeem_script_hash = address[1:-4]
        s = HathorScript()
        if timelock:
            s.pushData(timelock)
            s.addOpcode(Opcode.OP_GREATERTHAN_TIMESTAMP)
        s.addOpcode(Opcode.OP_HASH160)
        s.pushData(redeem_script_hash)
        s.addOpcode(Opcode.OP_EQUAL)
        return s.data

    @classmethod
    def create_input_data(cls, redeem_script: bytes, signatures: List[bytes]) -> bytes:
        """
        :param redeem_script: script to redeem the tokens: <M> <pubkey1> ... <pubkeyN> <N> <OP_CHECKMULTISIG>
        :type redeem_script: bytes

        :param signatures: array of signatures to validate the input and redeem the tokens
        :type signagures: List[bytes]

        :rtype: bytes
        """
        s = HathorScript()
        for signature in signatures:
            s.pushData(signature)
        s.pushData(redeem_script)
        return s.data

    @classmethod
    def parse_script(cls, script: bytes) -> Optional['MultiSig']:
        """Checks if the given script is of type multisig. If it is, returns the MultiSig object.
        Otherwise, returns None.

        :param script: script to check
        :type script: bytes

        :rtype: :py:class:`hathor.transaction.scripts.MultiSig` or None
        """
        match = cls.re_match.search(script)
        if match:
            groups = match.groups()
            timelock = None
            pushdata_timelock = groups[0]
            if pushdata_timelock:
                timelock_bytes = pushdata_timelock[1:]
                timelock = struct.unpack('!I', timelock_bytes)[0]
            redeem_script_hash = get_pushdata(groups[1])
            address_b58 = get_address_b58_from_redeem_script_hash(redeem_script_hash)
            return cls(address_b58, timelock)
        return None


def create_output_script(address: bytes, timelock: Optional[Any] = None) -> bytes:
    """ Verifies if address is P2PKH or Multisig and create correct output script

        :param address: address to send tokens
        :type address: bytes

        :param timelock: timestamp until when the output is locked
        :type timelock: bytes

        :raises ScriptError: if address is not from one of the possible options

        :rtype: bytes
    """
    if address[0] == binary_to_int(settings.P2PKH_VERSION_BYTE):
        return P2PKH.create_output_script(address, timelock)
    elif address[0] == binary_to_int(settings.MULTISIG_VERSION_BYTE):
        return MultiSig.create_output_script(address, timelock)
    else:
        raise ScriptError('The address is not valid')


def parse_address_script(script: bytes) -> Optional[Union[P2PKH, MultiSig]]:
    """ Verifies if address is P2PKH or Multisig and calls correct parse_script method

        :param script: script to decode
        :type script: bytes

        :return: P2PKH or MultiSig class or None
        :rtype: class or None
    """
    script_classes: List[Type[Union[P2PKH, MultiSig]]] = [P2PKH, MultiSig]
    # Each class verifies its script
    for script_class in script_classes:
        if script_class.re_match.search(script):
            return script_class.parse_script(script)
    return None


def get_pushdata(data: bytes) -> bytes:
    if data[0] > 75:
        length = data[1]
        start = 2
    else:
        length = data[0]
        start = 1
    return data[start:(start + length)]


def binary_to_int(binary: bytes) -> int:
    """Receives a binary and transforms it to an integer

    :param binary: value to convert
    :type binary: bytes
    """
    if len(binary) == 1:
        _format = '!B'
    elif len(binary) == 2:
        _format = '!H'
    elif len(binary) == 4:
        _format = '!I'
    elif len(binary) == 8:
        _format = '!Q'
    else:
        raise struct.error

    value: int
    (value,) = struct.unpack(_format, binary)
    return value
