"""
Copyright (c) Hathor Labs and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import unittest

from txstratum.commons import Block, TokenCreationTransaction, Transaction
from txstratum.commons.scripts import create_output_script
from txstratum.commons.utils import decode_address


class HathorCommonsTestCase(unittest.TestCase):
    def test_block_basics(self):
        data = bytes.fromhex('000001ffffffe8b789180000001976a9147fd4ae0e4fb2d2854e76d359029d8078bb9'
                             '9649e88ac40350000000000005e0f84a9000000000000000000000000000000278a7e')
        block = Block.create_from_struct(data)
        self.assertTrue(block.verify_pow())
        self.assertEqual(data, bytes(block))

        # These prints are here to test the methods.
        self.assertEqual(str(block), 'Block(nonce=2591358, timestamp=1578075305, version=0, '
                'weight=21.000000, hash=000006cb93385b8b87a545a1cbb6197e6caff600c12cc12fc54250d39c8088fc)')
        self.assertEqual(repr(block), 'Block(nonce=2591358, timestamp=1578075305, version=0, '
                'weight=21.000000, hash=000006cb93385b8b87a545a1cbb6197e6caff600c12cc12fc54250d39c8088fc, '
                'inputs=[], outputs=[TxOutput(token_data=0b0, value=100000000000)], parents=[], data=)')
        self.assertEqual(block.get_struct_nonce().hex(), '00000000000000000000000000278a7e')

        block.nonce += 1
        block.update_hash()
        self.assertFalse(block.verify_pow())

    def test_tx_basics(self):
        data = bytes.fromhex('0001000102000001e0e88216036e4e52872ba60a96df7570c3e29cc30eda6dd92ea0fd'
                             '304c00006a4730450221009fa4798bb69f66035013063c13f1a970ec58111bcead277d'
                             '9c93e45c2b6885fe022012e039b26cc4a4cb0a8a5abb7deb7bb78610ed362bf422efa2'
                             '47db37c5a841e12102bc1213ea99ab55effcff760f94c09f8b1a0b7b990c01128d06b4'
                             'a8c5c5f41f8400089f0800001976a91438fb3bc92b76819e9c19ef7c079d327c8fcd19'
                             '9288ac02de2d3800001976a9148d880c42ddcf78a2da5d06558f13515508720b4088ac'
                             '403518509c63f9195ecfd7d40200001ea9d6e1d31da6893fcec594dc3fa8b6819ae126'
                             '8c190f7a1441302226e2000007d1c5add7b9085037cfc591f1008dff4fe8a9158fd1a4'
                             '840a6dd5d4e4e600d2da8d')
        tx = Transaction.create_from_struct(data)

        self.assertEqual(data, bytes(tx))
        self.assertTrue(tx.verify_pow())
        self.assertTrue(tx.is_transaction)
        self.assertFalse(tx.is_block)

        # These prints are here to test the methods.
        print(str(tx))
        print(repr(tx))

        tx.nonce += 1
        tx.update_hash()
        self.assertFalse(tx.verify_pow())

    def test_token_creation_basics(self):
        data = bytes.fromhex('00020104000005551d7740fd7d3c0acc50b5677fdd844f1225985aa431e1712af2a2fd'
                             '8900006a473045022100a445edb5cd6c79a0a7b5ed837582fd65b8d511ee60b64fd076'
                             'e07bd8f63f75a202202dca24320bffc4c3ca2a07cdfff38f7c839bde70ed49ef634ac6'
                             '588972836cab2103bfa995d676e3c0ed7b863c74cfef9683fab3163b42b6f21442326a'
                             '023fc57fba0000264800001976a9146876f9578221fdb678d4e8376503098a9228b132'
                             '88ac00004e2001001976a914031761ef85a24603203c97e75af355b83209f08f88ac00'
                             '00000181001976a9149f091256cb98649c7c35df0aad44d7805710691e88ac00000002'
                             '81001976a914b1d7a5ee505ad4d3b93ea1a5162ba83d5049ec4e88ac0109546f546865'
                             '4d6f6f6e04f09f9a804034a52aec6cece75e0fc0e30200001a72272f48339fcc5d5ec5'
                             'deaf197855964b0eb912e8c6eefe00928b6cf600001055641c20b71871ed2c5c7d4096'
                             'a34f40888d79c25bce74421646e732dc01ff7369')
        tx = TokenCreationTransaction.create_from_struct(data)

        self.assertEqual(data, bytes(tx))
        self.assertTrue(tx.verify_pow())
        self.assertTrue(tx.is_transaction)
        self.assertFalse(tx.is_block)

        # These prints are here to test the methods.
        self.assertEqual(str(tx), 'TokenCreationTransaction(nonce=33518441, timestamp=1578090723, '
                'version=2, weight=20.645186, hash=00000828d80dd4cd809c959139f7b4261df41152f4cce65a8777eb1c3a1f9702, '
                'token_name=ToTheMoon, token_symbol=🚀)')
        self.assertEqual(repr(tx), 'TokenCreationTransaction(nonce=33518441, timestamp=1578090723, '
                'version=2, weight=20.645186, hash=00000828d80dd4cd809c959139f7b4261df41152f4cce65a8777eb1c3a1f9702, '
                'inputs=[TxInput(tx_id=000005551d7740fd7d3c0acc50b5677fdd844f1225985aa431e1712af2a2fd89, index=0)], '
                'outputs=[TxOutput(token_data=0b0, value=9800), TxOutput(token_data=0b1, value=20000), '
                'TxOutput(token_data=0b10000001, value=0b1), TxOutput(token_data=0b10000001, value=0b10)], '
                'parents=[\'00001a72272f48339fcc5d5ec5deaf197855964b0eb912e8c6eefe00928b6cf6\', '
                '\'00001055641c20b71871ed2c5c7d4096a34f40888d79c25bce74421646e732dc\'])')

        tx.nonce += 1
        tx.update_hash()
        self.assertFalse(tx.verify_pow())

    def test_script_basics(self):
        create_output_script(decode_address('HVZjvL1FJ23kH3buGNuttVRsRKq66WHUVZ'))
