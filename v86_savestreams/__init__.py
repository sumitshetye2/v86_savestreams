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
import jsonpatch

def _pad_to(buffer: bytes, length: int) -> bytes:
    """
    Pad a buffer to a specific length with null bytes

    Args:
        buffer (bytes): The input buffer to pad
        length (int): The desired length multiple

    Returns:
        bytes: The Padded buffer
    """
    return buffer + b'\x00' * ((length - len(buffer)) % length)


def _split_v86_savestate(buffer: bytes) -> Tuple[bytes, bytes, bytes]:
    """
    Split a v86 savestate into its component parts. This parts.

    Args:
        buffer (bytes): The complete savestate buffer

    Returns:
        Tuple[bytes, bytes, bytes]: A tuple of (header_block, info_block, buffer_block)
    """
    
    header_block = buffer[:16]
    info_len = np.frombuffer(header_block, dtype='int32')[3]
    info_block = buffer[16:16 + info_len]
    buffer_offset = 16 + info_len
    buffer_offset = buffer_offset + 3 & ~3
    buffer_block = buffer[buffer_offset:]
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
    for buf in info["buffer_infos"]:
        offset = buf['offset']
        length = buf['length']
        padding = (block_size - (length % block_size)) % block_size
        raw_block = buffer_block[offset:offset + length]
        expanded_block = raw_block + bytes([0] * padding)
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


def encode(v86state_array: Sequence[bytes], block_size: int = 256, super_block_size: int = None) -> bytes:
    """
    Encode a sequence of v86 save states into a single compressed savestream.

    Args:
        v86state_array (Sequence[bytes]): A sequence of v86 savestate binary data
        block_size (int, optional): Size of individual blocks for deduplication. Defaults to 256 bytes
        super_block_size (int, optional): Size of super blocks. Defaults to None.

    Returns:
        bytes: Compressed savestream as bytes
    """
    
    if super_block_size is None:
        super_block_size = block_size * 256
        
    known_blocks = {b'\x00' * block_size: 0}
    known_super_blocks = {b'\x00' * super_block_size: 0}
    
    incremental_saves = []
    prev_info = {}
    
    for savestate in v86state_array:
        header_block, info_block, buffer_block = _split_v86_savestate(savestate)
        aligned_buffer = _make_aligned_buffer_block(info_block, buffer_block, block_size=block_size)
        aligned_buffer = _pad_to(aligned_buffer, block_size)
        
        # Split into super blocks
        super_blocks = [aligned_buffer[i:i + super_block_size] for i in range(0, len(aligned_buffer), super_block_size)]
        
        super_id_sequence = []
        new_super_blocks = {}
        new_blocks = {}
        
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
                new_super_blocks[sb] = block_ids
            super_id_sequence.append(known_super_blocks[sb])
            
        # Delta compress info using jsonpatch
        info_json = json.loads(info_block.decode('utf-8'))
        patch = jsonpatch.make_patch(prev_info, info_json)
        encoded_info = patch.to_string().encode('utf-8')
        prev_info = info_json
        
        incremental_saves.append({
            'header_block': header_block,
            'info_patch': encoded_info,
            'super_sequence': super_id_sequence,
            'new_blocks': new_blocks,
            'new_super_blocks': new_super_blocks
        })
        
    # Pack the entire savestream
    return msgpack.packb(incremental_saves, use_bin_type=True)


def decode(savestream_bytes: bytes) -> List[bytes]:
    """
    Decode a savestream back into a sequence of v86 save states.

    Args:
        savestream_bytes (bytes): The compressed savestream

    Returns:
        List[bytes]: A list of decompressed v86 savestates
    """
    
    incremental_saves = msgpack.unpackb(savestream_bytes, raw=False)
    
    # Determine block size from the first non-empty block
    block_size = None
    for frame in incremental_saves:
        if frame['new_blocks']:
            # Get the first block
            first_block_id = next(iter(frame['new_blocks']))
            block_size = len(frame['new_blocks'][first_block_id])
            break
        
    if block_size is None:
        block_size = 256 # Default block size
        
    # Determine the super block size from the first super block
    super_block_size = None
    for frame in incremental_saves:
        if frame['new_super_blocks']:
            # Get the first super block
            first_sb_id = next(iter(frame['new_super_blocks']))
            super_block_size = len(frame['new_super_blocks'][first_sb_id]) * block_size
            break
    if super_block_size is None:
        super_block_size = block_size * 256
        
    # Initialize dictionaries to store blocks and super blocks
    blocks_dict = {0: b'\x00' * block_size}
    super_blocks_dict = {0: b'\x00' * super_block_size}
    
    # Initialize info for the first frame
    current_info = {}
    
    reconstructed_files = []
    
    for frame in incremental_saves:
        #Extract components from the frame
        header_block = frame['header_block']
        info_patch = frame['info_patch']
        super_sequence = frame['super_sequence']
        new_blocks = frame['new_blocks']
        new_super_blocks = frame['new_super_blocks']
        
        # Update blocks dictionary with new blocks
        blocks_dict.update(new_blocks)
        
        # Reconstruct super blocks
        for sb_id, block_ids in new_super_blocks.items():
            super_block_data = b''.join([blocks_dict[bid] for bid in block_ids])
            super_blocks_dict[sb_id] = super_block_data
            
        # Reconstruct aligned buffer from super blocks
        aligned_buffer = b''.join([super_blocks_dict[sb_id] for sb_id in super_sequence])
        
        # Apply JSON patch to get the current info
        current_info = jsonpatch.apply_patch(current_info, json.loads(info_patch.decode('utf-8')))
        info_block = json.dumps(current_info).encode('utf-8')
        
        # Convert aligned buffer to unaligned buffer
        buffer_block = _make_unaligned_buffer_block(info_block, aligned_buffer, block_size=block_size)
        
        # Recombine components to get the original file
        reconstructed_file = _recombine_v86_savestate((header_block, info_block, buffer_block))
        reconstructed_files.append(reconstructed_file)
        
    return reconstructed_files


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
    incremental_saves = msgpack.unpackb(savestream_bytes, raw=False)
    
    if index < 0 or index >= len(incremental_saves):
        raise IndexError(f"Index {index} out of range for savestream with {len(incremental_saves)} states")
    
    # Determine block_size from the first non-empty block
    block_size = None
    for frame in incremental_saves:
        if frame['new_blocks']:
            # Get the first block
            first_block_id = next(iter(frame['new_blocks']))
            block_size = len(frame['new_blocks'][first_block_id])
            break
    
    if block_size is None:
        block_size = 256  # Default if no blocks found
    
    # Determine super_block_size from the first super block
    super_block_size = None
    for frame in incremental_saves:
        if frame['new_super_blocks']:
            # Get the first super block
            first_sb_id = next(iter(frame['new_super_blocks']))
            super_block_size = len(frame['new_super_blocks'][first_sb_id]) * block_size
            break
    
    if super_block_size is None:
        super_block_size = 256 * block_size  # Default if no super blocks found
    
    # Initialize dictionaries to store blocks and super blocks
    blocks_dict = {0: b'\x00' * block_size}  # Initialize with zero block
    super_blocks_dict = {0: b'\x00' * super_block_size}  # Initialize with zero super block
    
    # Initialize info for the first frame
    current_info = {}
    
    # Process all frames up to and including the requested index
    for i, frame in enumerate(incremental_saves[:index+1]):
        # Extract components from the frame
        header_block = frame['header_block']
        info_patch = frame['info_patch']
        super_sequence = frame['super_sequence']
        new_blocks = frame['new_blocks']
        new_super_blocks = frame['new_super_blocks']
        
        # Update blocks dictionary with new blocks
        blocks_dict.update(new_blocks)
        
        # Reconstruct super blocks
        for sb_id, block_ids in new_super_blocks.items():
            super_block_data = b''.join([blocks_dict[bid] for bid in block_ids])
            super_blocks_dict[sb_id] = super_block_data
        
        # Reconstruct aligned buffer from super blocks
        aligned_buffer = b''.join([super_blocks_dict[sb_id] for sb_id in super_sequence])
        
        # Apply JSON patch to get the current info
        current_info = jsonpatch.apply_patch(current_info, json.loads(info_patch.decode('utf-8')))
        info_block = json.dumps(current_info).encode('utf-8')
        
        # If this is the requested index, complete the reconstruction
        if i == index:
            # Convert aligned buffer to unaligned buffer
            buffer_block = _make_unaligned_buffer_block(info_block, aligned_buffer, block_size)
            
            # Recombine components to get the original file
            return _recombine_v86_savestate((header_block, info_block, buffer_block))
    
    # This should never be reached due to the index check at the beginning
    raise RuntimeError("Unexpected error in decode_one")

def decode_len(savestream_bytes: bytes) -> int:
    """
    Get the number of save states contained in a savestream

    Args:
        savestream_bytes (bytes): The compressed savestream

    Returns:
        int: the number of saves in a savestream
    """
    
    incremental_saves = msgpack.unpackb(savestream_bytes, raw=False)
    return len(incremental_saves)


def main():
    import argparse
    import sys
    import os
    
    parser = argparse.ArgumentParser(description="v86 Savestream Utility")
    subparsers = parser.add_subparsers(dest="command", help="Commmand to execute")
    
    # Encode command
    encode_parser = subparsers.add_parser("encode", help="Encode v86 savestates into a savestream")
    encode_parser.add_argument("input_files", nargs="+", help="Input v86 savestate files")
    encode_parser.add_argument("output_files", help="Output savestream file")
    encode_parser.add_argument("--block_size", type=int, default=256, help="Block size for deduplication. Defaults to 256 bytes")
    encode_parser.add_argument("--super_block_size", type=int, help="Super Block Size. Default is 256 * block_size")
    
    #Decode command
    decode_parser = subparsers.add_parser("decode", help="Decode a savestream file into v86 save states")
    decode_parser.add_argument("input_file", help="Input savestream file")
    decode_parser.add_argument("output_dir", help="Output directory for save states")
    decode_parser.add_argument("--index", type=int, default=None, help="Decode only the specified index ")
    
    # Info command
    info_parser = subparsers.add_parser("info", help="Get information about a savestream")
    info_parser.add_argument("input_file", help="Input savestream file")
    
    args = parser.parse_args()
    
    if args.command == "encode":
        v86states = []
        for input_file in args.input_files:
            with open(input_file, "rb") as f:
                v86states.append(f.read())
                
        savestream = encode(v86states, block_size=args.block_size, super_block_size=args.super_block_size)
        
        with open(args.output_file, "wb") as f:
            f.write(savestream)
            
        print(f"Encoded {len(v86states)} save states to {args.output_file}")
    
    elif args.command == "decode":
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
            v86states = decode(savestream)
            for i, v86state in enumerate(v86states):
                output_file = os.path.join(args.output_dir, f"state_{i:04d}.bin")
                with open(output_file, "wb") as f:
                    f.write(v86state)
            print(f"Decoded {len(v86states)} to {output_file}")
                
    elif args.command == "info":
        with open(args.input_file, "rb") as f:
            savestream = f.read()
            
        num_states = decode_len(savestream)
        
        print(f"Savestream file: {args.input_file}")
        print(f"Number of save states: {num_states}")
        print(f"Savestream size: {len(savestream):,} bytes")
        if num_states > 0:
            print(f"Average save state size: {len(savestream) / num_states:,.2f} bytes")  
            
    else:
        parser.print_help()
        sys.exit(1)  

if __name__ == "__main__":
    main()
