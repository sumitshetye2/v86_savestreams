# Format Specification: v86 Savestreams

## Overview
This document defines the v86 savestream format and its Python reference implementation. The format efficiently compresses and decompresses v86 virtual machine save states into a single compact file (a savestream), minimizing redundant data via block-based deduplication and structural diffs.

## Core Concepts
1. **Save State Structure**
   - A v86 save state consists of:
     - `header_block`: First 16 bytes, includes a 32-bit little-endian field giving the length of the info block
     - `info_block`: JSON-encoded metadata (UTF-8), beginning at byte 16
     - `buffer_block`: Packed memory
2. **Savestream Format**
   - A savestream is a MessagePack-encoded list of frames, each storing:
     - An uncompressed header segment
     - A JSON diff patch of the info segment (using `dictdiffer`)
     - A compressed representation of the buffer segment:
       - A sequence of superblock indexes
       - New blocks encountered
       - New superblocks encountered

## Supporting Format Utilities

The following helper functions are used throughout the specification. Each is defined here with a description sufficient for implementation:

```
_pad_to(x: bytes, multiple: int) -> bytes
```
Pads the input byte string `x` with null bytes (`b'\x00'`) so that its length is a multiple of `multiple`. If already aligned, returns `x` unchanged.

```
_split_v86_savestate(file_content: bytes) -> Tuple[bytes, bytes, bytes]
```
Given the raw bytes of a v86 savestate, returns a tuple `(header_block, info_block, buffer_block)`. The header is the first 16 bytes. The length of the info block is read from the 4th 32-bit little-endian integer in the header. The info block follows the header, and the buffer block follows the info block (with padding to a 4-byte boundary if needed).

```
_recombine_v86_savestate(components: Tuple[bytes, bytes, bytes]) -> bytes
```
Given a tuple `(header_block, info_block, buffer_block)`, reconstructs the original savestate as bytes. Pads the info block to a 4-byte boundary if needed, then concatenates header, padded info, and buffer.

```
_make_aligned_buffer_block(info_block: bytes, buffer_block: bytes, block_size: int) -> bytes
```
Given the info block and buffer block, and a block size, extracts each memory region described in `buffer_infos` from the info block, pads each region to a multiple of `block_size`, and concatenates them in order. The result is a single aligned buffer block.

```
_make_unaligned_buffer_block(info_block: bytes, aligned_buffer_block: bytes, block_size: int) -> bytes
```
Given the info block and an aligned buffer block, removes the per-region padding (using the original lengths from `buffer_infos` in the info block) to reconstruct the original buffer block.

## Encoding Specification

```
encode(v86state_array: Sequence[bytes]) -> bytes
```
**Parameters**:
- `v86state_array`: List of raw v86 savestate binaries

**Returns**
- A msgpack-encoded savestream containing compressed v86 save states

**Encoding Procedure**
1. Split each state into `header_block`, `info_block`, `buffer_block` using `_split_v86_savestate()`
2. Align and expand buffers:
   - Use `buffer_infos` in `info_block` to extract subregions from `buffer_block`
   - Each subregion is padded to `block_size` (default: 256 bytes)
   - All padded subregions are concatenated and the result is padded to `super_block_size` (default: 256 × 256 bytes)
3. Deduplicate blocks:
   - Divide the aligned buffer into superblocks of size `super_block_size`
   - Assign each unique superblock an ID (`sid`) in `known_super_blocks`
   - Each superblock is divided into chunks of size `block_size`, each chunk gets a `bid` in `known_blocks`
4. Track block/superblock diffs:
   - For each frame, record new `block_id: block_bytes` in `new_blocks`
   - Record new `super_id: [block_ids]` in `new_super_blocks`
   - Store the sequence of superblock IDs in `super_sequence`
5. Delta encode metadata:
   - Compute a JSON diff (using `dictdiffer.diff`) between the current and previous `info_json`
   - Store the encoded diff as UTF-8 bytes as `info_patch`
6. Store encoded entry:
   - Each frame is a dictionary:
     ```
     {
       'header_block': ...,
       'info_patch': ...,
       'super_sequence': [...],
       'new_blocks': {bid: bytes, ...},
       'new_super_blocks': {sid: [bid, ...], ...}
     }
     ```
7. Serialize the list of frames with MessagePack

## Decoding Specification

```
decode(savestream_bytes: bytes) -> Generator[bytes, None, None]
```
**Parameters**
- `savestream_bytes`: A msgpack-encoded savestream

**Returns**
- A generator yielding decompressed v86 save states as bytes

**Decoding Procedure**
1. Unpack the savestream (msgpack list of frame dicts)
2. Initialize known block stores:
   - `known_blocks = {0: b'\x00' * 256}`
   - `known_super_blocks = {0: [0] * 256}`
3. For each frame:
   - Add `new_blocks` and `new_super_blocks` to stores
   - Apply `info_patch` (JSON diff) to previous info using `dictdiffer.patch`
   - Convert the resulting info to a UTF-8 `info_block`
   - Reconstruct aligned buffer from `super_sequence` by concatenating `known_blocks[bid]` using `known_super_blocks[sid]`
   - Use `_make_unaligned_buffer_block(info_block, aligned_buffer)` to remove padding
   - Recombine with `_recombine_v86_savestate`
   - Yield the full savestate

## Decode One Specification
```
decode_one(savestream_bytes: bytes, index: int) -> bytes
```
**Parameters**
- `savestream_bytes`: A msgpack-encoded savestream
- `index`: Integer index of the save state to decode

**Returns**
- The decompressed v86 save state at the given `index`

**Decoding Procedure**
- Iterates using the `decode` generator until the requested index is reached
- Returns the state at `index`, or raises `IndexError` if out of range

## Decode Length Specification
```
decode_len(savestream_bytes: bytes) -> int
```
**Parameters**
- `savestream_bytes`: A msgpack-encoded savestream

**Returns**
- The number of states in the savestream

**Procedure**
- Unpacks the savestream using msgpack
- Returns the length of the decoded list

## Trim Savestream Specification
```
trim(savestream_bytes: bytes, start_index: int, end_index: int | None = None) -> bytes
```
**Parameters**
- `savestream_bytes`: A msgpack-encoded savestream
- `start_index`: Starting index of the range to keep (inclusive)
- `end_index`: Ending index of the range to keep (exclusive, or None for end)

**Returns**
- A new savestream containing only the specified range of savestates

**Trimming Procedure**
- Checks if starting index is valid
- Iterates through `savestream_bytes` using `decode` and appends states in the specified range
- Returns a msgpack-encoded savestream of the kept states (re-encoded)

## Savestream File Format Summary
A savestream is a msgpack-encoded list of frames. Each frame is structured as:
```
{
  'header_block': bytes,
  'info_patch': bytes,    # UTF-8 JSON diff (dictdiffer)
  'super_sequence': List[int],
  'new_blocks': Dict[int, bytes],
  'new_super_blocks': Dict[int, List[int]]
}
```

