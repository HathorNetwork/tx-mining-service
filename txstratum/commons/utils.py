"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import hashlib
import re
import struct
from typing import Any, Tuple

import base58

from txstratum.commons.conf import HathorSettings

settings = HathorSettings()


def int_to_bytes(number: int, size: int, signed: bool = False) -> bytes:
    return number.to_bytes(size, byteorder='big', signed=signed)


def unpack(fmt: str, buf: bytes) -> Any:
    size = struct.calcsize(fmt)
    return struct.unpack(fmt, buf[:size]), buf[size:]


def unpack_len(n: int, buf: bytes) -> Tuple[bytes, bytes]:
    return buf[:n], buf[n:]


class InvalidAddress(Exception):
    """Raised when decoding an invalid address."""


def get_checksum(address_bytes: bytes) -> bytes:
    """ Calculate double sha256 of address and gets first 4 bytes

        :param address_bytes: address before checksum
        :param address_bytes: bytes

        :return: checksum of the address
        :rtype: bytes
    """
    return hashlib.sha256(hashlib.sha256(address_bytes).digest()).digest()[:4]


def decode_address(address58: str) -> bytes:
    """ Decode address in base58 to bytes

    :param address58: Wallet address in base58
    :type address58: string

    :raises InvalidAddress: if address58 is not a valid base58 string or
                            not a valid address or has invalid checksum

    :return: Address in bytes
    :rtype: bytes
    """
    try:
        decoded_address = base58.b58decode(address58)
    except ValueError:
        # Invalid base58 string
        raise InvalidAddress('Invalid base58 address')
    # Validate address size [25 bytes]
    if len(decoded_address) != 25:
        raise InvalidAddress('Address size must have 25 bytes')
    # Validate the checksum
    address_checksum = decoded_address[-4:]
    valid_checksum = get_checksum(decoded_address[:-4])
    if address_checksum != valid_checksum:
        raise InvalidAddress('Invalid checksum of address')
    return decoded_address


def get_address_b58_from_public_key_hash(public_key_hash: bytes) -> str:
    """Gets the b58 address from the hash of a public key.

        :param public_key_hash: hash of public key (sha256 and ripemd160)
        :param public_key_hash: bytes

        :return: address in base 58
        :rtype: string
    """
    address = get_address_from_public_key_hash(public_key_hash)
    return base58.b58encode(address).decode('utf-8')


def get_address_from_public_key_hash(public_key_hash: bytes,
                                     version_byte: bytes = settings.P2PKH_VERSION_BYTE) -> bytes:
    """Gets the address in bytes from the public key hash

        :param public_key_hash: hash of public key (sha256 and ripemd160)
        :param public_key_hash: bytes

        :param version_byte: first byte of address to define the version of this address
        :param version_byte: bytes

        :return: address in bytes
        :rtype: bytes
    """
    address = b''
    # Version byte
    address += version_byte
    # Pubkey hash
    address += public_key_hash
    checksum = get_checksum(address)
    address += checksum
    return address


def get_address_b58_from_redeem_script_hash(redeem_script_hash: bytes,
                                            version_byte: bytes = settings.MULTISIG_VERSION_BYTE) -> str:
    """Gets the b58 address from the hash of the redeem script in multisig.

        :param redeem_script_hash: hash of the redeem script (sha256 and ripemd160)
        :param redeem_script_hash: bytes

        :return: address in base 58
        :rtype: string
    """
    address = get_address_from_redeem_script_hash(redeem_script_hash, version_byte)
    return base58.b58encode(address).decode('utf-8')


def get_address_from_redeem_script_hash(redeem_script_hash: bytes,
                                        version_byte: bytes = settings.MULTISIG_VERSION_BYTE) -> bytes:
    """Gets the address in bytes from the redeem script hash

        :param redeem_script_hash: hash of redeem script (sha256 and ripemd160)
        :param redeem_script_hash: bytes

        :param version_byte: first byte of address to define the version of this address
        :param version_byte: bytes

        :return: address in bytes
        :rtype: bytes
    """
    address = b''
    # Version byte
    address += version_byte
    # redeem script hash
    address += redeem_script_hash
    checksum = get_checksum(address)
    address += checksum
    return address


def clean_token_string(string: str) -> str:
    """ Receives the token name/symbol and returns it after some cleanups.
        It sets to uppercase, removes double spaces and spaces at the beginning and end.
    """
    return re.sub(r'\s\s+', ' ', string).strip().upper()
