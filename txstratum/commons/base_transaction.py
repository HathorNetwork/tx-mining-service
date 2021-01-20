"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""
import base64
import datetime
import hashlib
from abc import ABC, abstractmethod
from enum import IntEnum
from math import isfinite, log
from struct import error as StructError, pack
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from _hashlib import HASH  # type: ignore

from txstratum.commons.exceptions import InvalidOutputValue, WeightError
from txstratum.commons.utils import int_to_bytes, unpack, unpack_len

MAX_NONCE = 2**32

MAX_OUTPUT_VALUE = 2**63  # max value (inclusive) that is possible to encode: 9223372036854775808 ~= 9.22337e+18
_MAX_OUTPUT_VALUE_32 = 2**31 - 1  # max value (inclusive) before having to use 8 bytes: 2147483647 ~= 2.14748e+09

TX_HASH_SIZE = 32   # 256 bits, 32 bytes

# H = unsigned short (2 bytes), d = double(8), f = float(4), I = unsigned int (4),
# Q = unsigned long long int (64), B = unsigned char (1 byte)

# Version (H), inputs len (B), and outputs len (B), token uids len (B).
# H = unsigned short (2 bytes)
_SIGHASH_ALL_FORMAT_STRING = '!HBBB'

# Weight (d), timestamp (I), and parents len (B)
_GRAPH_FORMAT_STRING = '!dIB'


def sum_weights(w1: float, w2: float) -> float:
    return aux_calc_weight(w1, w2, 1)


def sub_weights(w1: float, w2: float) -> float:
    if w1 == w2:
        return 0
    return aux_calc_weight(w1, w2, -1)


def aux_calc_weight(w1: float, w2: float, multiplier: int) -> float:
    a = max(w1, w2)
    b = min(w1, w2)
    if b == 0.0:
        # Zero is a special acc_weight.
        # We could use float('-inf'), but it is not serializable.
        return a
    return a + log(1 + 2**(b - a) * multiplier, 2)


class TxVersion(IntEnum):
    """Versions are sequential for blocks and transactions"""
    REGULAR_BLOCK = 0
    REGULAR_TRANSACTION = 1
    TOKEN_CREATION_TRANSACTION = 2
    MERGE_MINED_BLOCK = 3

    @classmethod
    def _missing_(cls, value: Any) -> 'TxVersion':
        # version's first byte is reserved for future use, so we'll ignore it
        assert isinstance(value, int)
        version = value & 0xFF
        if version == value:
            # Prevent infinite recursion when starting TxVerion with wrong version
            raise ValueError('Invalid version.')
        return cls(version)

    def get_cls(self) -> Type['BaseTransaction']:
        from txstratum.commons import Block, TokenCreationTransaction, Transaction

        cls_map: Dict[TxVersion, Type[BaseTransaction]] = {
            TxVersion.REGULAR_BLOCK: Block,
            TxVersion.REGULAR_TRANSACTION: Transaction,
            TxVersion.TOKEN_CREATION_TRANSACTION: TokenCreationTransaction,
        }

        cls = cls_map.get(self)

        if cls is None:
            raise ValueError('Invalid version.')
        else:
            return cls


class BaseTransaction(ABC):
    """Hathor base transaction"""

    # Even though nonce is serialized with different sizes for tx and blocks
    # the same size is used for hashes to enable mining algorithm compatibility
    SERIALIZATION_NONCE_SIZE: ClassVar[int]
    HASH_NONCE_SIZE = 16
    HEX_BASE = 16

    def __init__(self) -> None:
        self.nonce: int = 0
        self.timestamp: int = 0
        self.version: int = 0
        self.weight: float = 0
        self.inputs: List['TxInput'] = []
        self.outputs: List['TxOutput'] = []
        self.parents: List[bytes] = []
        self.hash: bytes = b''

    @property
    @abstractmethod
    def is_block(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_transaction(self) -> bool:
        raise NotImplementedError

    def _get_formatted_fields_dict(self, short: bool = True) -> Dict[str, str]:
        """ Used internally on __repr__ and __str__, returns a dict of `field_name: formatted_value`.
        """
        from collections import OrderedDict
        d = OrderedDict(
            nonce='%d' % (self.nonce or 0),
            timestamp='%s' % self.timestamp,
            version='%s' % int(self.version),
            weight='%f' % self.weight,
            hash=self.hash_hex,
        )
        if not short:
            d.update(
                inputs=repr(self.inputs),
                outputs=repr(self.outputs),
                parents=repr([x.hex() for x in self.parents]),
            )
        return d

    def __repr__(self) -> str:
        class_name = type(self).__name__
        return '%s(%s)' % (class_name, ', '.join('%s=%s' % i for i in self._get_formatted_fields_dict(False).items()))

    def __str__(self) -> str:
        class_name = type(self).__name__
        return '%s(%s)' % (class_name, ', '.join('%s=%s' % i for i in self._get_formatted_fields_dict().items()))

    def get_fields_from_struct(self, struct_bytes: bytes) -> bytes:
        """ Gets all common fields for a Transaction and a Block from a buffer.

        :param struct_bytes: Bytes of a serialized transaction
        :type struct_bytes: bytes

        :return: A buffer containing the remaining struct bytes
        :rtype: bytes

        :raises ValueError: when the sequence of bytes is incorect
        """
        buf = self.get_funds_fields_from_struct(struct_bytes)
        buf = self.get_graph_fields_from_struct(buf)
        return buf

    @classmethod
    @abstractmethod
    def create_from_struct(cls, struct_bytes: bytes) -> 'BaseTransaction':
        """ Create a transaction from its bytes.

        :param struct_bytes: Bytes of a serialized transaction
        :type struct_bytes: bytes

        :return: A transaction or a block, depending on the class `cls`

        :raises ValueError: when the sequence of bytes is incorrect
        """
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        """Two transactions are equal when their hash matches

        :raises NotImplement: when one of the transactions do not have a calculated hash
        """
        if not isinstance(other, BaseTransaction):
            return NotImplemented
        if self.hash and other.hash:
            return self.hash == other.hash
        return False

    def __bytes__(self) -> bytes:
        """Returns a byte representation of the transaction

        :rtype: bytes
        """
        return self.get_struct()

    def __hash__(self) -> int:
        assert self.hash is not None
        return hash(self.hash)

    @property
    def hash_hex(self) -> str:
        """Return the current stored hash in hex string format"""
        if self.hash is not None:
            return self.hash.hex()
        else:
            return ''

    @property
    def sum_outputs(self) -> int:
        """Sum of the value of the outputs"""
        return sum(output.value for output in self.outputs if not output.is_token_authority())

    def get_target(self, override_weight: Optional[float] = None) -> int:
        """Target to be achieved in the mining process"""
        if not isfinite(self.weight):
            raise WeightError
        return int(2**(256 - (override_weight or self.weight)) - 1)

    def get_time_from_now(self, now: Optional[Any] = None) -> str:
        """ Return a the time difference between now and the tx's timestamp

        :return: String in the format "0 days, 00:00:00"
        :rtype: str
        """
        if now is None:
            now = datetime.datetime.now()
        ts = datetime.datetime.fromtimestamp(self.timestamp)
        dt = now - ts
        seconds = dt.seconds
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return '{} days, {:02d}:{:02d}:{:02d}'.format(dt.days, hours, minutes, seconds)

    @abstractmethod
    def get_funds_fields_from_struct(self, buf: bytes) -> bytes:
        raise NotImplementedError

    def get_graph_fields_from_struct(self, buf: bytes) -> bytes:
        """ Gets all common graph fields for a Transaction and a Block from a buffer.

        :param buf: Bytes of a serialized transaction
        :type buf: bytes

        :return: A buffer containing the remaining struct bytes
        :rtype: bytes

        :raises ValueError: when the sequence of bytes is incorect
        """
        (self.weight, self.timestamp, parents_len), buf = unpack(_GRAPH_FORMAT_STRING, buf)

        for _ in range(parents_len):
            parent, buf = unpack_len(TX_HASH_SIZE, buf)  # 256bits
            self.parents.append(parent)

        return buf

    @abstractmethod
    def get_funds_struct(self) -> bytes:
        raise NotImplementedError

    def get_graph_struct(self) -> bytes:
        """Return the graph data serialization of the transaction, without including the nonce field

        :return: graph data serialization of the transaction
        :rtype: bytes
        """
        struct_bytes = pack(_GRAPH_FORMAT_STRING, self.weight, self.timestamp, len(self.parents))
        for parent in self.parents:
            struct_bytes += parent
        return struct_bytes

    def get_struct_without_nonce(self) -> bytes:
        """Return a partial serialization of the transaction, without including the nonce field

        :return: Partial serialization of the transaction
        :rtype: bytes
        """
        struct_bytes = self.get_funds_struct()
        struct_bytes += self.get_graph_struct()
        return struct_bytes

    def get_struct_nonce(self) -> bytes:
        """Return a partial serialization of the transaction's proof-of-work, which is usually the nonce field

        :return: Partial serialization of the transaction's proof-of-work
        :rtype: bytes
        """
        assert self.SERIALIZATION_NONCE_SIZE is not None
        struct_bytes = int_to_bytes(self.nonce, self.SERIALIZATION_NONCE_SIZE)
        return struct_bytes

    def get_struct(self) -> bytes:
        """Return the complete serialization of the transaction

        :rtype: bytes
        """
        struct_bytes = self.get_struct_without_nonce()
        struct_bytes += self.get_struct_nonce()
        return struct_bytes

    def verify_pow(self, override_weight: Optional[float] = None) -> bool:
        """Verify proof-of-work

        :raises PowError: when the hash is equal or greater than the target
        """
        assert self.hash is not None
        numeric_hash = int(self.hash.hex(), self.HEX_BASE)
        minimum_target = self.get_target(override_weight)
        if numeric_hash >= minimum_target:
            return False
        return True

    def get_funds_hash(self) -> bytes:
        """Return the sha256 of the funds part of the transaction

        :return: the hash of the funds data
        :rtype: bytes
        """
        funds_hash = hashlib.sha256()
        funds_hash.update(self.get_funds_struct())
        return funds_hash.digest()

    def get_graph_hash(self) -> bytes:
        """Return the sha256 of the graph part of the transaction

        :return: the hash of the funds data
        :rtype: bytes
        """
        graph_hash = hashlib.sha256()
        graph_hash.update(self.get_graph_struct())
        return graph_hash.digest()

    def get_header_without_nonce(self) -> bytes:
        """Return the transaction header without the nonce

        :return: transaction header without the nonce
        :rtype: bytes
        """
        return self.get_funds_hash() + self.get_graph_hash()

    def calculate_hash1(self) -> HASH:
        """Return the sha256 of the transaction without including the `nonce`

        :return: A partial hash of the transaction
        :rtype: :py:class:`_hashlib.HASH`
        """
        calculate_hash1 = hashlib.sha256()
        calculate_hash1.update(self.get_header_without_nonce())
        return calculate_hash1

    def calculate_hash2(self, part1: HASH) -> bytes:
        """Return the hash of the transaction, starting from a partial hash

        The hash of the transactions is the `sha256(sha256(bytes(tx))`.

        :param part1: A partial hash of the transaction, usually from `calculate_hash1`
        :type part1: :py:class:`_hashlib.HASH`

        :return: The transaction hash
        :rtype: bytes
        """
        part1.update(self.nonce.to_bytes(self.HASH_NONCE_SIZE, byteorder='big', signed=False))
        # SHA256D gets the hash in littlean format. Reverse the bytes to get the big-endian representation.
        return hashlib.sha256(part1.digest()).digest()[::-1]

    def calculate_hash(self) -> bytes:
        """Return the full hash of the transaction

        It is the same as calling `self.calculate_hash2(self.calculate_hash1())`.

        :return: The hash transaction
        :rtype: bytes
        """
        part1 = self.calculate_hash1()
        return self.calculate_hash2(part1)

    def update_hash(self) -> None:
        """ Update the hash of the transaction.
        """
        self.hash = self.calculate_hash()


class TxInput:
    _tx: BaseTransaction  # XXX: used for caching on hathor.transaction.Transaction.get_spent_tx

    def __init__(self, tx_id: bytes, index: int, data: bytes) -> None:
        """
            tx_id: hash of the transaction that contains the output of this input
            index: index of the output you are spending from transaction tx_id (1 byte)
            data: data to solve output script
        """
        assert isinstance(tx_id, bytes), 'Value is %s, type %s' % (str(tx_id), type(tx_id))
        assert isinstance(index, int), 'Value is %s, type %s' % (str(index), type(index))
        assert isinstance(data, bytes), 'Value is %s, type %s' % (str(data), type(data))

        self.tx_id = tx_id  # bytes
        self.index = index  # int
        self.data = data  # bytes

    def __repr__(self) -> str:
        return str(self)

    def __str__(self) -> str:
        return 'TxInput(tx_id=%s, index=%s)' % (self.tx_id.hex(), self.index)

    def __bytes__(self) -> bytes:
        """Returns a byte representation of the input

        :rtype: bytes
        """
        ret = b''
        ret += self.tx_id
        ret += int_to_bytes(self.index, 1)
        ret += int_to_bytes(len(self.data), 2)  # data length
        ret += self.data
        return ret

    def get_sighash_bytes(self, clear_data: bool) -> bytes:
        """Return a serialization of the input for the sighash

        :return: Serialization of the input
        :rtype: bytes
        """
        if not clear_data:
            return bytes(self)
        else:
            ret = bytearray()
            ret += self.tx_id
            ret += int_to_bytes(self.index, 1)
            ret += int_to_bytes(0, 2)
            return bytes(ret)

    @classmethod
    def create_from_bytes(cls, buf: bytes) -> Tuple['TxInput', bytes]:
        """ Creates a TxInput from a serialized input. Returns the input
        and remaining bytes
        """
        input_tx_id, buf = unpack_len(TX_HASH_SIZE, buf)
        (input_index, data_len), buf = unpack('!BH', buf)
        input_data, buf = unpack_len(data_len, buf)
        txin = cls(input_tx_id, input_index, input_data)
        return txin, buf

    def to_human_readable(self) -> Dict[str, Any]:
        """Returns dict of Input information, ready to be serialized

        :rtype: Dict
        """
        return {
            'tx_id': self.tx_id.hex(),  # string
            'index': self.index,  # int
            'data':
                base64.b64encode(self.data).decode('utf-8')  # string
        }


class TxOutput:

    # first bit in the index byte indicates whether it's an authority output
    TOKEN_INDEX_MASK = 0b01111111
    TOKEN_AUTHORITY_MASK = 0b10000000

    # last bit is mint authority
    TOKEN_MINT_MASK = 0b00000001
    # second to last bit is melt authority
    TOKEN_MELT_MASK = 0b00000010

    ALL_AUTHORITIES = TOKEN_MINT_MASK | TOKEN_MELT_MASK

    def __init__(self, value: int, script: bytes, token_data: int = 0) -> None:
        """
            value: amount spent (4 bytes)
            script: script in bytes
            token_data: index of the token uid in the uid list
        """
        assert isinstance(value, int), 'value is %s, type %s' % (str(value), type(value))
        assert isinstance(script, bytes), 'script is %s, type %s' % (str(script), type(script))
        assert isinstance(token_data, int), 'token_data is %s, type %s' % (str(token_data), type(token_data))
        if value <= 0 or value > MAX_OUTPUT_VALUE:
            raise InvalidOutputValue

        self.value = value  # int
        self.script = script  # bytes
        self.token_data = token_data  # int

    def __repr__(self) -> str:
        return str(self)

    def __str__(self) -> str:
        value_str = bin(self.value) if self.is_token_authority() else str(self.value)
        return ('TxOutput(token_data=%s, value=%s)' % (bin(self.token_data), value_str))

    def __bytes__(self) -> bytes:
        """Returns a byte representation of the output

        :rtype: bytes
        """
        ret = b''
        ret += output_value_to_bytes(self.value)
        ret += int_to_bytes(self.token_data, 1)
        ret += int_to_bytes(len(self.script), 2)    # script length
        ret += self.script
        return ret

    @classmethod
    def create_from_bytes(cls, buf: bytes) -> Tuple['TxOutput', bytes]:
        """ Creates a TxOutput from a serialized output. Returns the output
        and remaining bytes
        """
        value, buf = bytes_to_output_value(buf)
        (token_data, script_len), buf = unpack('!BH', buf)
        script, buf = unpack_len(script_len, buf)
        txout = cls(value, script, token_data)
        return txout, buf

    def get_token_index(self) -> int:
        """The token uid index in the list"""
        return self.token_data & self.TOKEN_INDEX_MASK

    def is_token_authority(self) -> bool:
        """Whether this is a token authority output"""
        return (self.token_data & self.TOKEN_AUTHORITY_MASK) > 0

    def can_mint_token(self) -> bool:
        """Whether this utxo can mint tokens"""
        return self.is_token_authority() and ((self.value & self.TOKEN_MINT_MASK) > 0)

    def can_melt_token(self) -> bool:
        """Whether this utxo can melt tokens"""
        return self.is_token_authority() and ((self.value & self.TOKEN_MELT_MASK) > 0)

    def to_human_readable(self) -> Dict[str, Any]:
        """Checks what kind of script this is and returns it in human readable form
        """
        from txstratum.commons.scripts import parse_address_script

        script_type = parse_address_script(self.script)
        if script_type:
            ret = script_type.to_human_readable()
            ret['value'] = self.value
            ret['token_data'] = self.token_data
            return ret

        return {}

    def to_json(self, *, decode_script: bool = False) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        data['value'] = self.value
        data['token_data'] = self.token_data
        data['script'] = base64.b64encode(self.script).decode('utf-8')
        if decode_script:
            data['decoded'] = self.to_human_readable()
        return data


def bytes_to_output_value(buf: bytes) -> Tuple[int, bytes]:
    (value_high_byte,), _ = unpack('!b', buf)
    if value_high_byte < 0:
        output_struct = '!q'
        value_sign = -1
    else:
        output_struct = '!i'
        value_sign = 1
    try:
        (signed_value,), buf = unpack(output_struct, buf)
    except StructError as e:
        raise InvalidOutputValue('Invalid byte struct for output') from e
    value = signed_value * value_sign
    assert value >= 0
    if value < _MAX_OUTPUT_VALUE_32 and value_high_byte < 0:
        raise ValueError('Value fits in 4 bytes but is using 8 bytes')
    return value, buf


def output_value_to_bytes(number: int) -> bytes:
    if number <= 0:
        raise InvalidOutputValue('Invalid value for output')

    if number > _MAX_OUTPUT_VALUE_32:
        return (-number).to_bytes(8, byteorder='big', signed=True)
    else:
        return number.to_bytes(4, byteorder='big', signed=True)  # `signed` makes no difference, but oh well
