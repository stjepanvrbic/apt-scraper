"""Makes the fp16 and int8 graphs of a candidate whose publisher ships only fp32, for the sweep's precision comparison.
A developer tool, not shipped.

  encoder_convert.py <model-directory> <fp32-graph>

fp16 converts the weights and keeps fp32 inputs and outputs (onnxconverter-common); int8 is ONNX Runtime's dynamic
quantization of the weights, as the SigLIP 2 exports' int8 graphs are.
"""

import sys
from pathlib import Path


def main(argv: list[str]) -> None:
    import onnx
    from onnxconverter_common import float16
    from onnxruntime.quantization import QuantType, quantize_dynamic

    directory, graph = Path(argv[0]), argv[1]
    source = directory / graph
    stem = source.stem
    model = onnx.load(str(source))
    if not (directory / f'{stem}_fp16.onnx').exists():
        half = float16.convert_float_to_float16(model, keep_io_types=True)
        onnx.save(half, str(directory / f'{stem}_fp16.onnx'), save_as_external_data=True, location=f'{stem}_fp16.onnx.data')
    # The OpenCLIP exports carry intermediate shape annotations that disagree with their own graphs, which quantization's
    # shape inference rejects; the graph without them is the same computation.
    model = onnx.load(str(source))
    del model.graph.value_info[:]
    plain = directory / f'{stem}_plain.onnx'
    onnx.save(model, str(plain), save_as_external_data=True, location=f'{stem}_plain.onnx.data')
    quantize_dynamic(str(plain), str(directory / f'{stem}_int8.onnx'), weight_type=QuantType.QInt8, use_external_data_format=True)


if __name__ == '__main__':
    main(sys.argv[1:])
