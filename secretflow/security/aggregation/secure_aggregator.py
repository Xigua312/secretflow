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

from typing import Any, Dict, List, Tuple, Union

import numpy as np
import pandas as pd

import secretflow.utils.ndarray_encoding as ndarray_encoding
from secretflow.security.diffie_hellman import DiffieHellman
from secretflow.device.device import PYU, proxy, reveal
from secretflow.device.device.base import DeviceObject
from secretflow.device.device.pyu import PYUObject
from secretflow.security.aggregation import Aggregator
from secretflow.security.aggregation._utils import is_nesting_list


@proxy(PYUObject)
class _Masker:
    def __init__(self, self_device: PYU, fraction_precesion: int):
        self._device = self_device
        self._dh = DiffieHellman()
        self._pub_key, self._pri_key = self._dh.generate_key_pair()
        self._fraction_precesion = fraction_precesion

    def pub_key(self) -> int:
        return self._pub_key

    def gen_rng(self, pub_keys: Dict[PYU, int]) -> None:
        assert pub_keys, f'Public keys is None or empty.'
        self._rngs = {device: np.random.default_rng(
            int(self._dh.generate_secret(self._pri_key, peer_pub_key),
                base=16)) for device, peer_pub_key in pub_keys.items() if device != self._device}

    def mask(self,
             data: Union
             [List[Union[pd.DataFrame, pd.Series, np.ndarray]],
              Union[pd.DataFrame, pd.Series, np.ndarray]],
             weight=None) -> Tuple[Union[List[np.ndarray], np.ndarray], np.dtype]:
        assert data is not None, 'Data shall not be None or empty.'
        is_list = isinstance(data, list)
        if not is_list:
            data = [data]
        if weight is None:
            weight = 1
        masked_data = []
        dtype = None
        for datum in data:
            if isinstance(datum, (pd.DataFrame, pd.Series)):
                datum = datum.values
            assert isinstance(datum, np.ndarray), f'Accept ndarray or dataframe/series only but got {type(datum)}'
            if dtype is None:
                dtype = datum.dtype
            else:
                assert datum.dtype == dtype, f'Data should have same dtypes but got {datum.dtype} {dtype}.'
            is_float = np.issubdtype(datum.dtype, np.floating)
            if not is_float:
                assert np.issubdtype(
                    datum.dtype, np.integer), f'Data type are neither integer nor float.'
                if datum.dtype != np.int64:
                    datum = datum.astype(np.int64)
            # Do mulitple before encoding to finite field.
            masked_datum: np.ndarray = ndarray_encoding.encode(
                datum * weight, self._fraction_precesion) if is_float else datum * weight
            for pyu, rng in self._rngs.items():
                if pyu == self._device:
                    continue
                mask = rng.integers(low=np.iinfo(np.int64).min, high=np.iinfo(np.int64).max,
                                    size=masked_datum.shape).astype(masked_datum.dtype)
                if pyu > self._device:
                    masked_datum += mask
                else:
                    masked_datum -= mask

            masked_data.append(masked_datum)
        if is_list:
            return masked_data, dtype
        else:
            return masked_data[0], dtype


class SecureAggregator(Aggregator):

    def __init__(self, device: PYU, participants: List[PYU], fraction_precesion: int = 7):
        assert len(set(participants)) == len(participants), 'Should not have duplicated devices.'
        self._device = device
        self._participants = set(participants)
        self._fraction_precesion = fraction_precesion
        self._maskers = {pyu: _Masker(pyu, self._fraction_precesion, device=pyu) for pyu in participants}
        pub_keys = reveal({pyu: masker.pub_key() for pyu, masker in self._maskers.items()})
        for masker in self._maskers.values():
            masker.gen_rng(pub_keys)

    def _check_data(self, data: List[PYUObject]):
        assert data, f'The data should not be None or empty.'
        assert len(data) == len(
            self._maskers), f'Length of the data not equals devices: {len(data)} vs {len(self._maskers)}'
        devices_of_data = set(datum.device for datum in data)
        assert devices_of_data == self._participants, 'Devices of the data must be corresponding with this aggregator.'

    @classmethod
    def _is_list(cls, masked_data: Union[List, Any]) -> bool:
        is_list = isinstance(masked_data[0], list)
        for masked_datum in masked_data[1:]:
            assert isinstance(masked_datum, list) == is_list, f'Some data are list where some others are not.'
            assert not is_list or len(masked_datum) == len(masked_datum[0]), f'Lengths of datum in data are different.'
        return is_list

    @reveal
    def sum(self, data: List[PYUObject], axis=None):
        def _sum(masked_data: List[np.ndarray], dtypes: List[np.dtype]):
            for dtype in dtypes[1:]:
                assert dtype == dtypes[0], f'Data should have same dtypes but got {dtype} {dtypes[0]}.'
            is_float = np.issubdtype(dtypes[0], np.floating)

            if is_nesting_list(masked_data):
                results = [np.sum(element, axis=axis) for element in zip(*masked_data)]
                return [ndarray_encoding.decode(result, self._fraction_precesion) for result in results] if is_float else results
            else:
                result = np.sum(masked_data, axis=axis)
                return ndarray_encoding.decode(result, self._fraction_precesion) if is_float else result

        self._check_data(data)
        masked_data = [None] * len(data)
        dtypes = [None] * len(data)
        for i, datum in enumerate(data):
            masked_data[i], dtypes[i] = self._maskers[datum.device].mask(datum)
        masked_data = [d.to(self._device) for d in masked_data]
        dtypes = [dtype.to(self._device) for dtype in dtypes]
        return self._device(_sum)(masked_data, dtypes)

    @reveal
    def average(self, data: List[PYUObject], axis=None, weights=None):
        def _average(masked_data: List[np.ndarray], dtypes: List[np.dtype], weights):
            for dtype in dtypes[1:]:
                assert dtype == dtypes[0], f'Data should have same dtypes but got {dtype} {dtypes[0]}.'
            is_float = np.issubdtype(dtypes[0], np.floating)

            sum_weights = len(data)
            if weights:
                sum_weights = np.sum(weights)
            if is_nesting_list(masked_data):
                sum_data = [np.sum(element, axis=axis) for element in zip(*masked_data)]
                if is_float:
                    sum_data = [ndarray_encoding.decode(sum_datum, self._fraction_precesion) for sum_datum in sum_data]
                return [element / sum_weights for element in sum_data]
            else:
                if is_float:
                    return ndarray_encoding.decode(
                        np.sum(masked_data, axis=axis),
                        self._fraction_precesion) / sum_weights
                return np.sum(masked_data, axis=axis) / sum_weights

        self._check_data(data)
        masked_data = [None] * len(data)
        dtypes = [None] * len(data)
        _weights = []
        if weights is not None and isinstance(weights, (list, tuple)):
            assert len(weights) == len(
                data), f'Length of the weights not equals data: {len(weights)} vs {len(data)}.'
            for i, w in enumerate(weights):
                if isinstance(w, DeviceObject):
                    assert w.device == data[i].device, 'Device of weight is not same with the corresponding data.'
                    _weights.append(w.to(self._device))
                else:
                    _weights.append(w)
            for i, (datum, weight) in enumerate(zip(data, weights)):
                masked_data[i], dtypes[i] = self._maskers[datum.device].mask(datum, weight)
        else:
            for i, datum in enumerate(data):
                masked_data[i], dtypes[i] = self._maskers[datum.device].mask(datum, weights)
        masked_data = [d.to(self._device) for d in masked_data]
        dtypes = [dtype.to(self._device) for dtype in dtypes]
        return self._device(_average)(masked_data, dtypes, _weights)
