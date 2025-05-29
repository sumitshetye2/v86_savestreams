TODO: add a specificaton of the savestream format to this file
# Format Specification: v86 Savestreams

## Overview
This format specification docuent defines a method to efficiently compress and decompress v86 virtual machine save states into a single compact file (called a savestream). It enables storing multiple VM save states while minimizing redundant data through block-based deduplication and structural diffs.

## Core Concepts
1. **Save State Structure**
   A single v86 save state is composed of:
   - header_block: First 16 bytes, includes a 32-bit little-endian field giving the length of the info block
   - info_block: JSON-encoded metadata (parsed as UTF-8), beggining at byte 16.
   - buffer_block: Packed memory
2. **Savestream Format**
   A savestream is a MessagePack-encoded stream that stores:
   - An uncompressed header segment
   - A patch of the info segment
   - A compressed representation of the buffer segment
     - A sequence of superblock indexes
     - New blocks encountered
     - New suberblocks encountered
  
## Supporting Format Utilities
```
_pad_to(x: bytes, multiple: int) -> bytes
```
pads `x` with null bytes to reach the next multiple of `multiple`

```
_split_v86_savestate(file_content: bytes) -> Tuple[bytes, bytes, bytes]
```
Parses and returns `(header_block, info_block, buffer_block)` from a raw save

```
_recombine_v86_savestate(components: Tuple[bytes, bytes, bytes]) -> bytes
```
Reconstructs a full binary from the three parts

```
_make_aligned_buffer_block(info_block, buffer_block, block_size)
```
Returns concatenated aligned blocks from memory regions specified in `buffer_infos`

```
_make_unaligned_buffer_block(info_block, aligned_buffer_block, block_size)
```
Removes per-region padding to restore the original buffer


## Encoding Specification

```
encode(v86state_array: Sequence[bytes] -> bytes
```
**Parameters**:
- `v86state_array`: A list of raw v86 savestate binaries

**Returns**
- A msgpack-encoded savestream containing compressed v86 save states

**Encoding Procedure**
1. Split State into Components
   Use _split_v86_savestates() to extract
   - `header_block`
   - `info_block`
   - `buffer_block`
2. Align and Expand Buffers
   - Use `buffer_infos` inside `info_block` to extract the subregions from `buffer_block`
   - Each subregion is padded to `super_block_size`
   - All padded subregions are concatenated into a single aligned buffer
   - This buffer is padded to `super_block_size`
3. Deduplicate blocks
   - Divide the aligned buffer into super blocks of size `super_block_size`
   - Assign each unique super block an ID (`sid`) in `known_super_blocks`
   - Each super block is divided into chunks of size `block_size`
   - Each chunk is given an ID (`bid`) in `known_blocks`
4. Track Block/Superblock Diffs
   For each frame:
   - Record new `block_id: block_bytes` in `new_blocks`
   - Record new `super_id: [block_ids]` in `new_super_blocks`
   - Store sequence of superblock IDs in `super_sequence`
5. Delta Encode Metadata
   - Compute a JSON diff between current `info_json` and the previous one
     - Python implementation uses the `dictdiffer` library to do this
   - Store the encoded diff as UTF-8 bytes as `info_patch`
6. Store Encoded Entry
   Each frame is stored as a dictionary:
   ```
   {
     'header_block': ...,
     'info_patch': ...,
     'super_sequence': [...],
     'new_blocks': {bid: bytes, ...},
     'new_super_blocks': {sid: [bid, ...], ...}
   }
   ```
7. Serialize with MessagePack

## Decoding Specification

```
decode(savestream_bytes: bytes) -> Sequence[bytes]
```
**Parameters**
- `savestream_bytes`: A msgpack-encoded savestream containing compressed v86 save states

**Returns**
- A generator that produces decompressed v86 save states one at a time as bytes

**Decoding Procedure**
1. Unpack the savestream
   - Use msgpack to get a list of frame dictionaries
2. Initialize Known Block Stores
   - `known_blocks = {0: b'\x00' * 256}`
   - `known_super_blocks = {0: [0] * 256}`
3. Iterate Through Frames
   For each frame:
   - Add `new_blocks` and `new_super_blocks` to stores
   - Apply `info_patch` to previous info
   - Confert the resulting `info_json` to a UTF-8 `info_block`
   - Reconstruct aligned buffer from `super_sequence`:
     - Concatenate `known_blocks[bid]` using the `known_super_blocks[sid]` mapping
   - Use `_make_unaligned_buffer_block(info_block, aligned_buffer)` to remove padding
   - Recombine with `_recombine_v86_savestate`
   - Yield the full savestate
  

## Decode One Specification
```
decode_one(savestream_bytes: bytes, index: int) -> bytes
```
**Parameters**
- `savestream_bytes`: A msgpack-encoded savestream containing compressed v86 save states
- `index`: An integer representing the save state within the savestream to decode

**Returns**
- The decompressed v86 save state pulled from the savestream at the given `index`

**Decoding Procedure**
- Utilizes the `decode` function's generator to reconstruct the save states one at a time until the save state at the chosen `index` is reached
- Returns state found at `index`

## Decode Length Specification
```
decode_len(savestream_bytes: bytes) -> int
```

**Parameters**
- `savestream_bytes`: A msgpack-encoded savestream containing compressed v86 save states

**Returns**
- The number of states in the savestream

**Procedure**
- Unpacks the savestream using msgpack
- Returns the length of the msgpack-decoded savestream

## Trim Savestream Specification
```
trim(savestream_bytes: bytes, start_index: int, end_index: int | None = None) -> bytes
```

 **Parameters**
 - `savestream_bytes`: A msgpack-encoded savestream containing compressed v86 save states
 - `start_index` Integer representing the starting index of the range to keep (inclusive)
 - `end_index` Integer representing the ending index of the range to keep (exclusive)

**Returns**
- A new savestream containing only the specified range of savestates

**Trimming Procedure**
- Check if starting index is valid
- Create an empty array to store the save states to keep
- Iterate through `savestream_bytes` and append those states to the empty array within the specified range
- Return a msgpack-encoded savestream of the states that were kept


## Command Line Interface

The savestream module supports a CLI with four subcommands

**Encode**
```sh
python -m savestreams encode [input_files] output_file.savestream
```
This encodes the given input files into a savestream called output_file.savestream

**Decode**
```sh
python -m savestreams decode input.savestream output_dir [--index n]
```
This decodes the given input savestream to a list of save states stored in the named output directory. If an optional index is provided, it will only store the indexed savestate in the output directory

**Trim**
```sh
python -m savestreams trim input.savestream output.savestream start_index [end_index]
```
This trims the given savestate using the specified range. If no ending index is given, the command will trim from the starting index to the end. Note the range works similar to Python slicing

**Info**
```sh
python -m savestreams info input.savestream
```
This will output information about the savestream including:
- Length of savestream
- Total size of savestream
- Average size per save in savestream

## Savestream File Format Summary
A savestream is a msgpack-encoded list of frames. Each frame is structured like so:
```
{
  'header_block': bytes
  'info_patch: bytes,    # UTF-8 JSON diff
  'super_sequence': List[int]
  'new_blocks': Dict[int, bytes],
  'new_super_blocks': Dict[Int, List[int]]
}
```
     
