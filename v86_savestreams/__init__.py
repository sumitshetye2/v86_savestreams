#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
v86 Savestreams - Efficient compression and decompression of v86 virtual machine save states.
    
This module provides functionality to compress a sequence of v86 save states into a single compact
savestream and decompress it back to the original save states.
"""
from typing import List, Tuple, Dict, Any, Sequence
import json
import struct
import numpy as np
import msgpack
from dictdiffer import diff, patch
import io

import argparse
import sys
import os

def _pad_to(x: bytes, multiple: int) -> bytes:
    """
    Pad a buffer to a specific length with null bytes

    Args:
        x (bytes): The input buffer to pad
        multiple (int): The desired length multiple

    Returns:
        bytes: The Padded buffer
    """
    if multiple <= 0:
        raise ValueError("Padding multiple must be a positive integer")
    
    return x + b'\x00' * ((multiple - (len(x) % multiple)) % multiple)


def _split_v86_savestate(file_content: bytes) -> Tuple[bytes, bytes, bytes]:
    """
    Split the v86 savestate into its header, info, and buffer blocks. Internal helper function

    Args:
        buffer (bytes): The complete savestate buffer

    Returns:
        Tuple[bytes, bytes, bytes]: A tuple of (header_block, info_block, buffer_block)
    """
    header_block = file_content[:16]
    info_len = np.frombuffer(header_block, dtype='int32')[3]
    
    info_block = file_content[16:16 + info_len]
    
    buffer_offset = 16 + info_len
    buffer_offset = buffer_offset + 3 & ~3
    
    buffer_block = file_content[buffer_offset:]
    
    return header_block, info_block, buffer_block
    
    

def _recombine_v86_savestate(components: Tuple[bytes, bytes, bytes]) -> bytes:
    """
    Recombine the components of a v86 savestate into a single save state

    Args:
        components (Tuple[bytes, bytes, bytes]): A tuple of (header_block, info_block, buffer_block)

    Returns:
        bytes: The complete savestate buffer
    """
    header_block, info_block, buffer_block = components
    required_padding = (len(info_block) + 3 & ~3) - len(info_block)
    info_block = info_block + bytes([0] * required_padding)
    return header_block + info_block + buffer_block

def _make_aligned_buffer_block(info_block: bytes, buffer_block: bytes, block_size: int) -> bytes:
    """
    Align buffer blocks to a specific block size for efficient deduplication

    Args:
        info_block (bytes): The info block containing buffer metadata
        buffer_block (bytes): The raw buffer data
        block_size (int): The alignment block size

    Returns:
        bytes: Aligned buffer block
    """
    
    info = json.loads(info_block.decode('utf-8'))
    
    aligned_blocks = []
    for info in info["buffer_infos"]:
        offset = info['offset']
        length = info['length']
        padding_length = (block_size - (length % block_size)) % block_size
        raw_block = buffer_block[offset:offset + length]
        expanded_block = raw_block + bytes([0] * padding_length)
        aligned_blocks.append(expanded_block)
    return b''.join(aligned_blocks)


def _make_unaligned_buffer_block(info_block: bytes, aligned_buffer_block: bytes, block_size: int) -> bytes:
    """
    Convert an aligned buffer block back to its original unaligned form.

    Args:
        info_block (bytes): The info block containing buffer metadata
        aligned_buffer_block (bytes): The aligned buffer data
        block_size (int): The alignment block size

    Returns:
        bytes: Unaligned buffer block matching the original format
    """
    
    info = json.loads(info_block.decode('utf-8'))
    unaligned_blocks = []
    offset = 0
    
    for info in info["buffer_infos"]:
        length = info['length']
        padding_length = (block_size - (length % block_size)) % block_size
        
        #remove the padding
        raw_block = aligned_buffer_block[offset:offset + length +3 & ~3]
        offset += (length + padding_length)
        unaligned_blocks.append(raw_block)
    
    return b''.join(unaligned_blocks)


def encode(v86state_array: Sequence[bytes]) -> bytes:
    """
    Encode a sequence of v86 save states into a single compressed savestream.

    Args:
        v86state_array (Sequence[bytes]): A sequence of v86 savestate binary data
        block_size (int, optional): Size of individual blocks for deduplication. Defaults to 256 bytes
        super_block_size (int, optional): Size of super blocks. Defaults to None.

    Returns:
        bytes: Compressed savestream as bytes
    """
    block_size = 256
    super_block_size = 256 * block_size
    
        
    # Maps known byte blocks and super blocks to their unique IDs
    known_blocks = {b'\x00' * block_size: 0}
    known_super_blocks = {b'\x00' * super_block_size: 0}
    
    # Final encoded output
    incremental_saves = []
    
    # Keep track of previous state's decoded info for diffing
    prev_info = {} 
    
    # === Main Encoding Loop ===
    for savestate in v86state_array:
        # Split the savestate into its components
        header_block, info_block, buffer_block = _split_v86_savestate(savestate)
        
        # --- Align the buffer and break into fixed-size superblocks
        aligned_buffer = _make_aligned_buffer_block(info_block, buffer_block, block_size=block_size)
        aligned_buffer = _pad_to(aligned_buffer, super_block_size)
        
        # Split into super blocks
        super_blocks = [
            aligned_buffer[i:i + super_block_size]
            for i in range(0, len(aligned_buffer), super_block_size)
        ]
        
        super_id_sequence = [] # Sequence of IDs representing superblocks
        
        new_super_blocks = {} # Newly discovered super blocks
        new_blocks = {} # Newly discovered 256-byte blocks
        
        # --- Deduplicate the superblocks and extract the block IDs ---
        for sb in super_blocks:
            if sb not in known_super_blocks:
                sid = len(known_super_blocks)
                known_super_blocks[sb] = sid
                
                # Decompose into blocks
                block_ids = []
                for i in range(0, len(sb), block_size):
                    block = sb[i:i + block_size]
                    if block not in known_blocks:
                        bid = len(known_blocks)
                        known_blocks[block] = bid
                        new_blocks[bid] = block
                    block_ids.append(known_blocks[block])
                    
                # Store mapping from super block ID to block ID sequence
                new_super_blocks[sid] = block_ids
                
            # Add current super block ID to sequence
            super_id_sequence.append(known_super_blocks[sb])
            
        # --- Delta encode the info block using dictdiffer
        info_json = json.loads(info_block.decode('utf-8'))
        info_diff = list(diff(prev_info, info_json))
        encoded_info = json.dumps(info_diff, separators=(',',':')).encode('utf-8')
        prev_info = info_json
        
        
        # --- Record the current encoded state ---
        incremental_saves.append({
            'header_block': header_block,
            'info_patch': encoded_info,
            'super_sequence': super_id_sequence,
            'new_blocks': new_blocks,
            'new_super_blocks': new_super_blocks
        })
        
    # Pack and return the entire savestream
    return msgpack.packb(incremental_saves)


def decode(savestream_bytes: bytes) -> Sequence[bytes]:
    """
    Decode a savestream back into a sequence of v86 save states.

    Args:
        savestream_bytes (bytes): The compressed savestream

    Returns:
        Sequence[bytes]: A sequence of decompressed v86 savestates
    """
    
    
    unpacked_saves = msgpack.unpackb(savestream_bytes, strict_map_key=False)
    
    block_size = 256
    super_block_size = 256 * block_size
        
    # --- Initialize known block/super block stores with a zero block ---
    known_blocks = {0: b'\x00' * block_size}
    known_super_blocks = {0: [0] * 256}    
    
    # --- Start with an empty "previous info" to build patches on top of ---
    prev_info = {}
    
    # --- Iterate through each save entry in the unpacked list ---
    for i, save in enumerate(unpacked_saves):
        # --- Extract each component of the save ---
        header_block = save['header_block']
        info_patch = save['info_patch']
        super_sequence = save['super_sequence']
        new_blocks = save['new_blocks']
        new_super_blocks = save['new_super_blocks']
        
        # --- Add all newly introduced blocks to the known blocks dictionary ---
        for bid, block in new_blocks.items():
            known_blocks[bid] = block
            
        # --- Add all newly introduced superblocks to known_super_blocks dictionary ---
        for sid, block_id_list in new_super_blocks.items():
            known_super_blocks[sid] = block_id_list
            
        # Reconstruct the full info JSON using the patch and prev_info
        delta = json.loads(info_patch.decode('utf-8'))
        patched_info = patch(delta, prev_info)
        current_info = {
            'buffer_infos': patched_info['buffer_infos'],
            'state': patched_info['state']
        }
        prev_info = current_info
        info_block = json.dumps(current_info, separators=(',',':')).encode('utf-8')
        
        # --- Reconstruct the full aligned buffer from the superblcock sequence ---
        buffer = bytearray()
        for sb_id in super_sequence:
            block_ids = known_super_blocks[sb_id]
            for bid in block_ids:
                buffer.extend(known_blocks[bid])
                                   
        # --- Convert aligned buffer to unaligned buffer ---
        buffer_block = _make_unaligned_buffer_block(info_block, bytes(buffer), block_size)
        
        # --- Stitch together the full savestate ---
        full_savestate = _recombine_v86_savestate((header_block, info_block, buffer_block))
        
        yield full_savestate

def trim(savestream_bytes: bytes, start_index: int, end_index: int | None = None) -> bytes:
    """
    Trim a savestream to only include the specified range of save states.

    Args:
        savestream_bytes (bytes): The compressed savestream
        start_index (int): The starting index of the range to keep
        end_index (int): The ending index of the range to keep

    Returns:
        bytes: A new savestream containing only the specified range of save states
    """
    if start_index < 0:
        raise ValueError("Invalid start or end index")
    
    trimmed = []
    for i, state in enumerate(decode(savestream_bytes)):
        if i < start_index:
            continue
        if end_index is not None and i > end_index:
            break
        trimmed.append(state)
            
    if not trimmed:
        raise  ValueError("No states in the specified range")
    
    return encode(trimmed)
    


def decode_one(savestream_bytes: bytes, index: int) -> bytes:
    """
    Decode a single save state from a savestream at the specified index.
    
    Args:
        savestream_bytes (bytes): The compressed savestream
        index (int): The index of the save state to decode
        
    Returns:
        bytes: The decompressed v86 savestate binary data at the specified index
        
    Raises:
        IndexError: If the index is out of range
    """
    # iterate using the generator until we have the one that was requested
    for i, state in enumerate(decode(savestream_bytes)):
        if i == index:
            return state
    raise IndexError(f"Index {index} out of range for savestream with {decode_len(savestream_bytes)} saves")
    
    
    
def decode_len(savestream_bytes: bytes) -> int:
    """
    Get the number of save states contained in a savestream

    Args:
        savestream_bytes (bytes): The compressed savestream

    Returns:
        int: the number of saves in a savestream
    """
    
    incremental_saves = msgpack.unpackb(savestream_bytes, strict_map_key=False)
    return len(incremental_saves)


def main():

    
    parser = argparse.ArgumentParser(description="v86 Savestream Utility")
    subparsers = parser.add_subparsers(dest="command", help="Commmand to execute")
    
    # Encode command
    encode_parser = subparsers.add_parser("encode", help="Encode v86 savestates into a savestream")
    encode_parser.add_argument("input_files", nargs="+", help="File names of V86 save states")
    encode_parser.add_argument("output_file", help="Output savestream file path")
    
    # Decode command
    decode_parser = subparsers.add_parser("decode", help="Decode a savestream file into v86 save states")
    decode_parser.add_argument("input_file", help="Path to savestream file")
    decode_parser.add_argument("output_dir", help="Directory to save decoded states")
    decode_parser.add_argument("--index", type=int, default=None, help="(Optional) Decode only the specified index ")
        
    # Info command
    info_parser = subparsers.add_parser("info", help="Get information about a savestream")
    info_parser.add_argument("input_file", help="Input savestream file")

    # Trim command
    trim_parser = subparsers.add_parser("trim", help="Trim a savestream to a specific range")
    trim_parser.add_argument("input_file", help="Input savestream file")
    trim_parser.add_argument("output_file", help="Output trimmed savestream file")
    trim_parser.add_argument("start_index", type=int, default=0, help="Start index of the range to keep (default: 0)")
    trim_parser.add_argument("end_index", type=int, nargs='?', default=None, help="End index of the range to keep (default: -1, meaning up to the end)")

    args = parser.parse_args()
    
    if args.command == "encode":
        v86states = []
        for input_file in args.input_files:
            with open(input_file, "rb") as f:
                v86states.append(f.read())
        
        from . import encode
                
        savestream = encode(v86states)
        
        with open(args.output_file, "wb") as f:
            f.write(savestream)
            
        print(f"Encoded {len(v86states)} save states to {args.output_file}")
    
    elif args.command == "decode":
        from . import decode, decode_one
        with open(args.input_file, "rb") as f:
            savestream = f.read()
            
        os.makedirs(args.output_dir, exist_ok=True)
        
        if args.index is not None:
            # Decode only the specified index
            try:
                v86state = decode_one(savestream, args.index)
                output_file = os.path.join(args.output_dir, f"state_{args.index:04d}.bin")
                with open(output_file, "wb") as f:
                    f.write(v86state)
                print(f"Decoded state {args.index} to {output_file}")
            except IndexError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
                
        else:
            # Decode all save states
            for i, v86state in enumerate(decode(savestream)):
                output_file = os.path.join(args.output_dir, f"state_{i:04d}.bin")
                with open(output_file, "wb") as f:
                    f.write(v86state)
            print(f"Decoded {i+1} states to {args.output_dir}")
            
        
                
    elif args.command == "info":
        with open(args.input_file, "rb") as f:
            savestream = f.read()
            
        num_states = decode_len(savestream)
        
        print(f"Savestream file: {args.input_file}")
        print(f"Number of save states: {num_states}")
        print(f"Savestream size: {len(savestream):,} bytes")
        if num_states > 0:
            print(f"Average save state size: {len(savestream) / num_states:,.2f} bytes")  
    
    elif args.command == "trim":
        from . import trim
        with open(args.input_file, "rb") as f:
            savestream = f.read()
        
        trimmed_savestream = trim(savestream, args.start_index, args.end_index)
        
        with open(args.output_file, "wb") as f:
            f.write(trimmed_savestream)
            
        if args.end_index is None:
            print(f"Trimmed savestream saved to {args.output_file} from index {args.start_index} to the end")
        else:
            print(f"Trimmed savestream saved to {args.output_file} from index {args.start_index} to {args.end_index}")

    else:
        parser.print_help()
        sys.exit(1)  

if __name__ == "__main__":
    main()
