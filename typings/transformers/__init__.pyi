from collections.abc import Sequence
from typing import Literal, overload

import numpy as np
from numpy.typing import NDArray

class BatchEncoding:
    def __getitem__(self, key: str) -> NDArray[np.int64]: ...

class TokenList:
    def __getitem__(self, key: str) -> list[int]: ...

class PreTrainedTokenizer:
    pad_token_id: int | None
    @overload
    def __call__(self, text: str | Sequence[str], *, padding: bool | str = False, max_length: int | None = None,
                 truncation: bool = False, return_tensors: Literal['np']) -> BatchEncoding: ...
    @overload
    def __call__(self, text: str, *, padding: bool | str = False, max_length: int | None = None,
                 truncation: bool = False, return_tensors: None = None) -> TokenList: ...

class AutoTokenizer:
    @classmethod
    def from_pretrained(cls, pretrained_model_name_or_path: str, *, local_files_only: bool = False) -> PreTrainedTokenizer: ...
