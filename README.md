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
savestreams info some.savestream
```

```sh
savestreams decode some.savestream outputs --index 2
```

```sh
savestreams trim some.savestream trimmed.savestream 50 250 
```


## Use example data provided with this repository

```sh
python -c "import zipfile; zipfile.ZipFile('tests/msdos-states.zip', 'r').extractall('examples')"
savestreams encode "examples/msdos-v86state (1).bin" "examples/msdos-v86state (2).bin" "examples/msdos-v86state (3).bin" examples/msdos.savestream
savestreams info examples/msdos.savestream
```

## Testing

```sh
pip install .[test]
pytest
```