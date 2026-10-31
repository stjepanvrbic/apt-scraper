# typings

Stubs for the modules the check suite's environment lacks on purpose, each declaring only the names the repository's
code uses, with the types the real module gives them. basedpyright reads this directory as its `stubPath`.

- `hermes_cli`, `hermes_constants`: Hermes's own packages, which the profile's scripts import when Hermes runs them.
- `google_api`, `googleapiclient`: the google-workspace connector's script and the Google API client it uses.
- `curl_cffi`: the search's HTTP client, installed into the search's own environment.
- `onnxruntime`, `onnx`, `onnxconverter_common`, `transformers`: the photo encoder's runtime, which ships no stubs,
  and the encoder build tools' packages, which only a build environment has.
