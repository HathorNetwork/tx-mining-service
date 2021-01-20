# Copyright (c) Hathor Labs and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# Default expected mining time when there is no miners or the total hashrate is unknown.
#
# We used to return `-1`. In this case, the wallets show an error message saying that
# "No miners are available".
#
# We had an issue when there was a high demand for transaction mining and we restarted
# the tx-mining-service. In this case, all miners will reconnect and their hashrate will
# be unknown. At the same time, several transactions will arrive and will have expected_mining_time = -1.
# In this case, the wallets will show an error message saying that "No miners are available"
# while the tx mining service will actually solve these transactions.
#
# To fix this issue, we set a positive value for the default expected mining time.
DEFAULT_EXPECTED_MINING_TIME: float = 5.0
