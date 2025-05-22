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
savestreams encode state0.bin state1.bin state2.bin > some.savestream
```

```sh
savestreams length some.savestream # 3
```

```sh
savestreams decode some.savestream 2 > state2.bin
```

```sh
savestreams trim some.savestream 50 250 > trimmed.savestream
```
## Unpack example data

```sh
python -c "import zipfile; zipfile.ZipFile('your_file.zip', 'r').extractall('destination_folder')"
```

## Testing

```sh
pip install .[test]
pytest
```