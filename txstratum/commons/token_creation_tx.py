"""
Copyright 2019 Hathor Labs

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from struct import error as StructError, pack
from typing import Tuple

from txstratum.commons.base_transaction import TxInput, TxOutput
from txstratum.commons.conf import settings
from txstratum.commons.exceptions import TransactionDataError
from txstratum.commons.transaction import Transaction
from txstratum.commons.utils import clean_token_string, int_to_bytes, unpack, unpack_len

# Version (H), inputs len (B), outputs len (B)
_FUNDS_FORMAT_STRING = '!HBB'

# Version (H), inputs len (B), outputs len (B)
_SIGHASH_ALL_FORMAT_STRING = '!HBB'

# used when (de)serializing token information
# version 1 expects only token name and symbol
TOKEN_INFO_VERSION = 1


class TokenCreationTransaction(Transaction):
    def __init__(self) -> None:
        super().__init__()
        # for this special tx, its own hash is used as the created token uid. We're artificially
        # creating the tokens list here
        self.tokens = []

    def __str__(self) -> str:
        return ('TokenCreationTransaction(nonce=%d, timestamp=%s, version=%s, weight=%f, hash=%s, '
                'token_name=%s, token_symbol=%s)' % (self.nonce, self.timestamp, int(self.version),
                                                     self.weight, self.hash_hex, self.token_name, self.token_symbol))

    def update_hash(self) -> None:
        """ When we update the hash, we also have to update the tokens uid list
        """
        super().update_hash()
        assert self.hash is not None
        self.tokens = [self.hash]

    def get_funds_fields_from_struct(self, buf: bytes) -> bytes:
        """ Gets all funds fields for a transaction from a buffer.

        :param buf: Bytes of a serialized transaction
        :type buf: bytes

        :return: A buffer containing the remaining struct bytes
        :rtype: bytes

        :raises ValueError: when the sequence of bytes is incorect
        """
        (self.version, inputs_len, outputs_len), buf = unpack(_FUNDS_FORMAT_STRING, buf)

        for _ in range(inputs_len):
            txin, buf = TxInput.create_from_bytes(buf)
            self.inputs.append(txin)

        for _ in range(outputs_len):
            txout, buf = TxOutput.create_from_bytes(buf)
            self.outputs.append(txout)

        # token name and symbol
        self.token_name, self.token_symbol, buf = TokenCreationTransaction.deserialize_token_info(buf)

        return buf

    def get_funds_struct(self) -> bytes:
        """ Returns the funds data serialization of the transaction

        :return: funds data serialization of the transaction
        :rtype: bytes
        """
        struct_bytes = pack(_FUNDS_FORMAT_STRING, self.version, len(self.inputs), len(self.outputs))

        tx_inputs = []
        for tx_input in self.inputs:
            tx_inputs.append(bytes(tx_input))
        struct_bytes += b''.join(tx_inputs)

        tx_outputs = []
        for tx_output in self.outputs:
            tx_outputs.append(bytes(tx_output))
        struct_bytes += b''.join(tx_outputs)

        struct_bytes += self.serialize_token_info()

        return struct_bytes

    def get_sighash_all(self, clear_input_data: bool = True) -> bytes:
        """ Returns a serialization of the inputs and outputs without including any other field

        :return: Serialization of the inputs, outputs and tokens
        :rtype: bytes
        """
        struct_bytes = pack(_SIGHASH_ALL_FORMAT_STRING, self.version, len(self.inputs), len(self.outputs))

        tx_inputs = []
        for tx_input in self.inputs:
            tx_inputs.append(tx_input.get_sighash_bytes(clear_input_data))
        struct_bytes += b''.join(tx_inputs)

        tx_outputs = []
        for tx_output in self.outputs:
            tx_outputs.append(bytes(tx_output))
        struct_bytes += b''.join(tx_outputs)

        struct_bytes += self.serialize_token_info()

        return struct_bytes

    def serialize_token_info(self) -> bytes:
        """ Returns the serialization for token name and symbol
        """
        encoded_name = self.token_name.encode('utf-8')
        encoded_symbol = self.token_symbol.encode('utf-8')

        ret = b''
        ret += int_to_bytes(TOKEN_INFO_VERSION, 1)
        ret += int_to_bytes(len(encoded_name), 1)
        ret += encoded_name
        ret += int_to_bytes(len(encoded_symbol), 1)
        ret += encoded_symbol
        return ret

    @classmethod
    def deserialize_token_info(cls, buf: bytes) -> Tuple[str, str, bytes]:
        """ Gets the token name and symbol from serialized format
        """
        (token_info_version,), buf = unpack('!B', buf)
        if token_info_version != TOKEN_INFO_VERSION:
            raise ValueError('unknown token info version: {}'.format(token_info_version))

        (name_len,), buf = unpack('!B', buf)
        name, buf = unpack_len(name_len, buf)
        (symbol_len,), buf = unpack('!B', buf)
        symbol, buf = unpack_len(symbol_len, buf)

        # Token name and symbol can be only utf-8 valid strings for now
        decoded_name = decode_string_utf8(name, 'Token name')
        decoded_symbol = decode_string_utf8(symbol, 'Token symbol')

        return decoded_name, decoded_symbol, buf

    def verify_token_info(self) -> None:
        """ Validates token info
        """
        name_len = len(self.token_name)
        symbol_len = len(self.token_symbol)
        if name_len == 0 or name_len > settings.MAX_LENGTH_TOKEN_NAME:
            raise TransactionDataError('Invalid token name length ({})'.format(name_len))
        if symbol_len == 0 or symbol_len > settings.MAX_LENGTH_TOKEN_SYMBOL:
            raise TransactionDataError('Invalid token symbol length ({})'.format(symbol_len))

        # Can't create token with hathor name or symbol
        if clean_token_string(self.token_name) == clean_token_string(settings.HATHOR_TOKEN_NAME):
            raise TransactionDataError('Invalid token name ({})'.format(self.token_name))
        if clean_token_string(self.token_symbol) == clean_token_string(settings.HATHOR_TOKEN_SYMBOL):
            raise TransactionDataError('Invalid token symbol ({})'.format(self.token_symbol))


def decode_string_utf8(encoded: bytes, key: str) -> str:
    """ Raises StructError in case it's not a valid utf-8 string
    """
    try:
        decoded = encoded.decode('utf-8')
        return decoded
    except UnicodeDecodeError:
        raise StructError('{} must be a valid utf-8 string.'.format(key))
