#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    Tests for v86 savetreams library.
    
    This module contains unit tests for both the public and private API
    of the v86_savestreams module
"""
    
import os
import json
import struct
import tempfile
import subprocess
from pathlib import Path

import pytest
import numpy as np

from v86_savestreams.savestreams import (
    # Public API
    encode, decode, decode_one, decode_len,
    
    #Private API
    _pad_to, _split_v86_savestate, _recombine_v86_savestate,
    _make_aligned_buffer_block, _make_unaligned_buffer_block
)

# Utility functions for creating test data
def create_mock_savestate(state_id, buffer_content=None, buffer_infos=None):
    """Create a mock v86 save state for testing."""
    if buffer_infos is None:
        buffer_infos = [{"offset": 0, "length": len(buffer_content or b'')}]
        
    info = {"buffer_infos": buffer_infos, "state_id": state_id}
    info_block = json.dumps(info).encode('utf-8')
    info_len = len(info_block)
    header_block = struct.pack('IIII', state_id, 0, 0, info_len)
    
    if buffer_content is None:
        buffer_content = bytes([state_id % 256] * 50 + [0] * 50)
        
    # Pad info block to align with 4 bytes
    padding = (4 - (len(info_block) % 4)) % 4
    padded_info = info_block + b'\x00' * padding
    
    return header_block + padded_info + buffer_content, (header_block, info_block, buffer_content)

def create_mock_savestates(count=3):
    """Create multiple mock save states for testing"""
    savestates = []
    components = []
    for i in range(count):
        savestate, component = create_mock_savestate(i)
        savestates.append(savestate)
        components.append(component)
    return savestates, components






def test_pad_to():
    # test padding when already aligned
    assert _pad_to(b'1234', 4) == b'1234'
    
    # test padding when not aligned
    assert _pad_to(b'123', 4) == b'123\x00'
    assert _pad_to(b'1', 4) == b'1\x00\x00\x00'
    
    # test with larger alignment values
    assert len(_pad_to(b'123', 256)) == 256
    assert _pad_to(b'12345', 256)[:5] == b'12345'

def test_split_v86_savestate():
    """Test the _split_v86_savestate function."""
    # Create a mock v86 savestate
    savestate, (header_block, info_block, buffer_block) = create_mock_savestate(0)
        
    # Test splitting
    split_header, split_info, split_buffer = _split_v86_savestate(savestate)
    assert split_header == header_block
    assert split_info == info_block
    assert split_buffer == buffer_block
    
def test_recombine_v86_savestate():
    """Test the _recombine_v86_savestate function"""
    # Create a mock v86 savestate
    savestate, (header_block, info_block, buffer_block) = create_mock_savestate(0)
    
    # Test recombining
    recombined = _recombine_v86_savestate((header_block, info_block, buffer_block))
    
    # The recombined savestate might have different padding than the original
    # so we need to split it again to compare components
    re_header, re_info, re_buffer = _split_v86_savestate(recombined)
    assert re_header == header_block
    assert re_info == info_block
    assert re_buffer == buffer_block
    
def test_make_aligned_buffer_block():
    """Test the _make_aligned_buffer_block function."""
    # Create a mock info block with multiple buffers
    buffer_content = b'0123456789ABCDE'
    buffer_infos = [
        {"offset": 0, "length": 10},
        {"offset": 10, "length": 5}
    ]
    
    _, (_, info_block, _) = create_mock_savestate(0, buffer_content, buffer_infos)
    
    # Test alignment with block size 8
    block_size = 8
    aligned_buffer = _make_aligned_buffer_block(info_block, buffer_content, block_size)
    
    # First buffer should be padded to 16 bytes (10 + padding multiple of 8)
    expected_length = ((10 + block_size - 1) // block_size) * block_size + \
        ((5 + block_size - 1) // block_size) * block_size
    assert len(aligned_buffer) == expected_length
    
    # Check content of aligned buffer
    assert aligned_buffer[:10] == buffer_content[:10]
    # Calculate offset for second buffer in aligned buffer
    second_buffer_offset = ((10 + block_size - 1) // block_size) * block_size
    assert aligned_buffer[second_buffer_offset:second_buffer_offset + 5] == buffer_content[10:15]
    
    
def test_make_unaligned_buffer_block():
    """Test the _make_unaligned_buffer_block function"""
    # Create a mock info block with multiple buffers
    buffer_content = 'b0123456789ABCDE'
    buffer_infos = [
        {"offset": 0, "length": 10},
        {"offset": 10, "length": 5}
    ]
    
    _, (_, info_block, _) = create_mock_savestate(0, buffer_content, buffer_infos)
    
    # First create an aligned buffer
    block_size = 8
    aligned_buffer = _make_aligned_buffer_block(info_block, buffer_content, block_size)
    
    # Then convert back to unaligned buffer
    unaligned_buffer = _make_unaligned_buffer_block(info_block, aligned_buffer)
    
    # The unaligned buffer should contain the original content, but offsets might be different
    # Extract the content based on the new buffer_infos
    new_info = json.loads(info_block.decode('utf-8'))
    first_buffer = unaligned_buffer[new_info["buffer_infos"][0]["offset"]:new_info["buffer_infos"][0]["offset"] + 10]
    second_buffer = unaligned_buffer[new_info["buffer_infos"][1]["offset"]:new_info["buffer_infos"][1]["offset"] + 5]
    
    assert first_buffer == buffer_content[:10]
    assert second_buffer == buffer_content[10:15]
    
# Tests for public API functions

def test_encode_decode_roundtrip():
    """Test the encode and decode functions in a roundtrip scenario"""
    savestates, _ = create_mock_savestate(3)
    
    # Encode the savestates
    savestream = encode(savestates)
    
    # Decode the savestream
    decoded_savestates = decode(savestream)
    
    # Verify the decoded savestates match the original
    assert len(decoded_savestates) == len(savestates)
    for original, decoded in zip(savestates, decoded_savestates):
        # We need to split both to compare components as padding might differ
        orig_components = _split_v86_savestate(original)
        decoded_components = _split_v86_savestate(decoded)
        
        assert orig_components[0] == decoded_components[0] # header
        
        # Compare JSON content of info blocks
        orig_info = json.loads(orig_components[1].decode('utf-8'))
        decoded_info = json.loads(decoded_components[1].decode('utf-8'))
        assert orig_info == decoded_info
        
        assert orig_components[2] == decoded_components[2] # buffer
        
def test_encode_one():
    """Test the decode_one function"""
    # Create mock v86 savestates
    savestates, _ = create_mock_savestates(3)
    
    # Encode the savestates
    savestream = encode(savestates)
    
    # Test decoding specific indices
    for i in range(3):
        decoded = decode_one(savestream, i)
        
        # Compare components
        orig_components = _split_v86_savestate(savestates[i])
        decoded_components = _split_v86_savestate(decoded)
        
        assert orig_components[0] == decoded_components[0] # header
        
        
        # Compare JSON content of info blocks
        orig_info = json.loads(orig_components[1].decode('utf-8'))
        decoded_info = json.loads(decoded_components[1].decode('utf-8'))
        assert orig_info == decoded_info
        
        assert orig_components[2] == decoded_components[2] # buffer
        
    # Test out-of-range index
    with pytest.raises(IndexError):
        decode_one(savestream, 3)
        
    with pytest.raises(IndexError):
        decode_one(savestream, -1)
        
def test_decode_len():
    """Test the decode_len function"""
    # Create mock v86 savestates
    savestates, _ = create_mock_savestates(3)
    
    # Encode the savestates
    savestream = encode(savestates)
    
    # Test length
    assert decode_len(savestream) == 3
    
def test_empty_input():
    """Test encoding and decoding an empty list of savestates"""
    savestream = encode([])
    assert decode_len(savestream) == 0
    assert decode(savestream) == []
    
def test_large_blocks():
    """Test encoding and decoding with different block sizes"""
    # Create mock v86 savestates
    savestates, _ = create_mock_savestates(3)
    
    # Test with different block sizes
    for block_size in [128, 256, 512]:
        savestream = encode(savestates, block_size=block_size)
        decoded = decode(savestream)
        
        assert len(decoded) == len(savestates)
        for original, decoded_state in zip(savestates, decoded):
            # Compare components
            orig_components = _split_v86_savestate(original)
            decoded_components = _split_v86_savestate(decoded_state)
            
            assert orig_components[0] == decoded_components[0] # header
            
            # Compare JSON content of info blocks
            orig_info = json.loads(orig_components[1].decode('utf-8'))
            decoded_info = json.loads(decoded_components[1].decode('utf-8'))
            assert orig_info == decoded_info
            
            assert orig_components[2] == decoded_components[2] # buffer
        
def test_deduplication_efficiency():
    """Test that the deduplication is working effectively"""
    # Create savestates with high redundancy
    # We'll create 10 savestates where most of the data is identical
    savestates = []
    
    base_buffer = bytes([0] * 1000) # 1KB of zeros
    
    for i in range(10):
        # Each savestate has 1KB of zeros, with just 10 bytes that change
        custom_part = bytes([i] * 10)
        buffer = base_buffer[:500] + custom_part + base_buffer[510:]
        
        buffer_infos = [{"offset": 0, "length": len(buffer)}]
        savestate, _ = create_mock_savestate(i, buffer, buffer_infos)
        savestates.append(savestate)
        
    # Encode and check compression ratio
    savestream = encode(savestates)
    
    # Calculate the total size of all savestates
    total_size = sum(len(s) for s in savestates)
    
    # Calculate compression ratio
    compression_ratio = len(savestream) / total_size
    
    # We expect significant compression due to deduplication
    # With nearly identical 1KB savestates, we should get much better than 50% compression
    assert compression_ratio < 0.5, f"Compression ratio {compression_ratio} is higher than expected"
    
    # Verify we can still decode correctly
    decoded = decode(savestream)
    assert len(decoded) == len(savestates)
    
def test_custom_super_block_size():
    """Test encoding and decoding with custom super block size"""
    # Create mock v86 savestates
    savestates, _ = create_mock_savestates(3)
    
    # Test with custom super block size
    block_size = 256
    super_block_size = 1024 # 4 blocks per super block
    
    savestream = encode(savestates, block_size=block_size, super_block_size=super_block_size)
    decoded = decode(savestream)
    
    assert len(decoded) == len(savestates)
    for original, decoded_state in zip(savestates, decoded):
        orig_components = _split_v86_savestate(original)
        decoded_components = _split_v86_savestate(decoded_state)
        
        assert orig_components[0] == decoded_components[0] # header
        
        # Compare JSON content of info blocks
        orig_info = json.loads(orig_components[1].decode('utf-8'))
        decoded_info = json.loads(decoded_components[1].decode('utf-8'))
        assert orig_info == decoded_info
        
        assert orig_components[2] == decoded_components[2]
        
    def test_incremental_changes():
        """Test encoding and decoding savestates with incremental changes"""
        # Start with a base savestate
        base_buffer = bytes([0] * 1000)
        base_info = {"memory": "initial", "cpu": {"eax": 0, "ebx": 0}}
        
        for i in range(5):
            # Modify a small part of the buffer for each savestate
            modified_buffer = bytearray(base_buffer)
            modified_buffer[i*10:(i+1)*10] = bytes([i+1] * 10)
            
            # Modify part of the info for each savestate
            modified_info = base_info.copy()
            modified_info["cpu"] = {"eax": i, "ebx": i*2}
            
            # Create the savestate
            info_block = json.dumps(modified_info).encode('utf-8')
            info_len = len(info_block)
            header_block = struct.pack('IIII', i, 0, 0, info_len)
            
            # Pad info block to align with 4 bytes
            
            padding = (4 - (len(info_block) % 4)) % 4
            padded_info = info_block + b'\x00' * padding
            
            savestate = header_block + padded_info + bytes(modified_buffer)
            savestates.append(savestate)
            
        # Encode and decode
        savestream = encode(savestates)
        decoded = decode(savestream)
        
        # Verify all the savestates are correctly decoded
        assert len(decoded) == len(savestates)
        for i, (original, decoded_state) in enumerate(zip(savestates, decoded)):
            # Compare components
            orig_components = _split_v86_savestate(original)
            decoded_components = _split_v86_savestate(decoded_state)
            
            assert orig_components[0] == decoded_components[0]  # header
            
            # Compare JSON content of info blocks
            orig_info = json.loads(orig_components[1].decode('utf-8'))
            decoded_info = json.loads(decoded_components[1].decode('utf-8'))
            assert orig_info == decoded_info
            
            assert orig_components[2] == decoded_components[2]  # buffer
        

# Tests for CLI functionality
def temp_dir():
    """Create a temporary dictionary for CLI tests"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)
        
        
def test_cli_encode_decode(temp_dir):
    """Test the CLI encode and decode commands"""
    # Create input and output directories
    input_dir = temp_dir / "input"
    input_dir.mkdir()
    output_file = temp_dir / "savestream.bin"
    output_dir = temp_dir / "output"
    output_dir.mkdir()
    
    # Create and save mock savestates
    savestates, _ = create_mock_savestates(3)
    input_files = []
    
    for i, savestate in enumerate(savestates):
        input_file = input_dir / f"state_{i}.bin"
        with open(input_file, "wb") as f:
            f.write(savestate)
        input_files.append(input_file)
        
    # Test CLI encode
    cmd = [
        "python", "-m", "v86_savestreams.savestreams",
        "encode",
        *[str(f) for f in input_files],
        str(output_file)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Encode command failed: {result.stderr}"
    assert output_file.exists(), "Savestream file was not created"
    
    # Test CLI decode
    cmd = [
        "python", -"m", "v86_savestreams.savestreams",
        "decode",
        str(output_file),
        str(output_dir)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Decode command failed: {result.stderr}"
    
    # Verify decoded files match original
    for i in range(3):
        decoded_file = output_dir / f"state_{i:04d}.bin"
        assert decoded_file.exists(), f"Decoded file {decoded_file} does not exist"
        
        with open(decoded_file, "rb") as f:
            decoded = f.read()
        
        # Compare components to handle potential padding differences
        orig_components = _split_v86_savestate(savestates[i])
        decoded_components = _split_v86_savestate(decoded)
        
        assert orig_components[0] == decoded_components[0]  # header
        
        # Compare JSON content of info blocks
        orig_info = json.loads(orig_components[1].decode('utf-8'))
        decoded_info = json.loads(decoded_components[1].decode('utf-8'))
        assert orig_info == decoded_info
        
        assert orig_components[2] == decoded_components[2]  # buffer
        
def test_cli_decode_one(temp_dir):
    """Test the CLI decode command with a specific index."""
    # Create mock savestates
    savestates, _ = create_mock_savestates(3)
    
    # Encode to a savestream file
    savestream = encode(savestates)
    savestream_file = temp_dir / "savestream.bin"
    with open(savestream_file, "wb") as f:
        f.write(savestream)
    
    # Create output directory
    output_dir = temp_dir / "output"
    output_dir.mkdir()
    
    # Test CLI decode with index
    index = 1
    cmd = [
        "python", "-m", "v86_savestreams.savestreams", 
        "decode", 
        str(savestream_file), 
        str(output_dir),
        "--index", str(index)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Decode command failed: {result.stderr}"
    
    # Verify only the specified savestate was decoded
    decoded_file = output_dir / f"state_{index:04d}.bin"
    assert decoded_file.exists(), f"Decoded file {decoded_file} does not exist"
    
    # Check that other files don't exist
    for i in range(3):
        if i != index:
            other_file = output_dir / f"state_{i:04d}.bin"
            assert not other_file.exists(), f"File {other_file} should not exist"
    
    # Verify the decoded file matches the original
    with open(decoded_file, "rb") as f:
        decoded = f.read()
    
    # Compare components
    orig_components = _split_v86_savestate(savestates[index])
    decoded_components = _split_v86_savestate(decoded)
    
    assert orig_components[0] == decoded_components[0]  # header
    
    # Compare JSON content of info blocks
    orig_info = json.loads(orig_components[1].decode('utf-8'))
    decoded_info = json.loads(decoded_components[1].decode('utf-8'))
    assert orig_info == decoded_info
    
    assert orig_components[2] == decoded_components[2]  # buffer


def test_cli_info(temp_dir):
    """Test the CLI info command."""
    # Create mock savestates
    savestates, _ = create_mock_savestates(3)
    
    # Encode to a savestream file
    savestream = encode(savestates)
    savestream_file = temp_dir / "savestream.bin"
    with open(savestream_file, "wb") as f:
        f.write(savestream)
    
    # Test CLI info command
    cmd = [
        "python", "-m", "v86_savestreams.savestreams", 
        "info", 
        str(savestream_file)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Info command failed: {result.stderr}"
    
    # Verify output contains expected information
    output = result.stdout
    assert f"Number of save states: 3" in output, "Output should show 3 save states"
    assert f"Savestream size: {len(savestream):,} bytes" in output, "Output should show savestream size"
    assert "Average size per state:" in output, "Output should show average size"


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
