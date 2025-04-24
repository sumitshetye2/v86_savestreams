# v86_savestreams

Efficient and lossless compression and decompression of [v86](https://copy.sh/v86/) virtual machine savestates.

This repository provides:
- A specification of the savestream file format
- A reference implementation of savestream encoding and decoding in Python
- A command-line utility for transforming `v86state.bin` files into savestreams and back

## Installation

It is recommended that you set up a [Python virtual environment](https://docs.python.org/3/library/venv.html) first.

```sh
pip install .
```

## Usage

```sh
savestreams [optional options]
```

## Testing

```sh
pip install .[test]
pytest
```