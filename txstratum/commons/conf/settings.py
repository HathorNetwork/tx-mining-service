"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

from typing import NamedTuple


class HathorSettings(NamedTuple):
    # Name of the network: "mainnet", "testnet-alpha", "testnet-bravo", ...
    NETWORK_NAME: str

    # Version byte of the address in P2PKH
    P2PKH_VERSION_BYTE: bytes

    # Version byte of the address in MultiSig
    MULTISIG_VERSION_BYTE: bytes

    # HTR Token UID
    HATHOR_TOKEN_UID: bytes = b'\x00'

    # Maximum number of characters in a token name
    MAX_LENGTH_TOKEN_NAME: int = 30

    # Maximum number of characters in a token symbol
    MAX_LENGTH_TOKEN_SYMBOL: int = 5

    # Name of the Hathor token
    HATHOR_TOKEN_NAME: str = 'Hathor'

    # Symbol of the Hathor token
    HATHOR_TOKEN_SYMBOL: str = 'HTR'
