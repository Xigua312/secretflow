# Copyright 2022 Ant Group Co., Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np

from .errors import InvalidArgumentError


def encode(m: np.ndarray, fraction_precesion: int) -> np.ndarray:
    """Encode float ndarray to uint64 finite field.
    Float will times 10**fraction_precesion firstly.

    Args:
        m (np.ndarray): the ndarray to encode.
        fraction_precesion (int): keep how many decimal digits after the dot.
            Must provide if ndarray dtype is float.

    Returns:
        np.ndarray: the encoded ndarray.
    """
    assert isinstance(m, np.ndarray), f'Support ndarray only but got {type(m)}'
    if m.dtype not in [np.float16, np.float32, np.float64]:
        raise InvalidArgumentError(f'Accept float ndarray only but got {m.dtype}')

    uint64_max = 0xFFFFFFFFFFFFFFFF
    assert fraction_precesion is not None, f'Fraction precesion must not be None.'
    max_value = m.max()
    if max_value * (10 ** fraction_precesion) > uint64_max:
        raise InvalidArgumentError(f'Float data exceeds uint range (0, {uint64_max}) after encoding.')
    # Convert to np.float64 for reducing overflow.
    return (m.astype(np.float64) * (10 ** fraction_precesion)).astype(np.uint64)


def decode(m: np.ndarray, fraction_precesion: int) -> np.ndarray:
    """Decode ndarray from uint64 finite field to the float.
    Fraction precesion shall be corresponding to encoding fraction precesion.

    Args:
        m (np.ndarray): the ndarray to decode.
        fraction_precesion (int): the decimal digits to keep when encoding float.
            Must provide if the original dtype is float.

    Returns:
        np.ndarray: the decoded float ndarray.
    """
    assert isinstance(m, np.ndarray), f'Support ndarray only but got {type(m)}'
    assert m.dtype == np.uint64, f'Ndarray dtype must be uint but got {m.dtype}'
    assert fraction_precesion is not None, f'Fraction precesion must not be None.'
    # Convert to int for restoring the negetives.
    return m.astype(np.int64) / (10 ** fraction_precesion)
