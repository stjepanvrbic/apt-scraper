from enum import Enum

class QuantType(Enum):
    QInt8 = 0
    QUInt8 = 1

def quantize_dynamic(model_input: str, model_output: str, *, weight_type: QuantType = ..., use_external_data_format: bool = False) -> None: ...
