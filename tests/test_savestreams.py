#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    Tests for v86 savetreams library.
    
    This module contains unit tests for both the public and private API
    of the v86_savestreams module
    
"""

import json
import struct
import tempfile
import subprocess
import dictdiffer
import msgpack
from pathlib import Path
import zipfile
import os
import shutil
import sys

import pytest

from v86_savestreams import (
    # Public API
    encode, decode, decode_one, decode_len, trim,
    
    #Private API
    _pad_to, _split_v86_savestate, _recombine_v86_savestate,
    _make_aligned_buffer_block, _make_unaligned_buffer_block
)



@pytest.fixture(scope="session")
def savestates():
    """
    Extract real v86 savestate files from msdos-states.zip for testing.
    
    Returns a list of tuples (filename, content) for each .bin file in the zip
    
    """
    zip_path = Path(__file__).parent / 'msdos-states.zip'
    
    if not zip_path.exists():
        pytest.skip(f"Test data not found: {zip_path}")
        
    savestates = []
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # Get all the .bin files in the zip
        bin_filenames = [f for f in zip_ref.namelist() if f.endswith('.bin')]
        bin_filenames = sorted(bin_filenames)
        
        # Extract content from each file
        for bin_filename in bin_filenames:
            with zip_ref.open(bin_filename) as f:
                file_content = f.read()
                savestates.append((bin_filename, file_content))
                
    # Verify we have the expected number of files
    if not savestates:
        pytest.skip("No .bin files found in the zip")
        
    return savestates

    

# Tests for private API functions

def test_pad_to():
    # test padding when already aligned
    assert _pad_to(b'1234', 4) == b'1234'
    
    # test padding when not aligned
    assert _pad_to(b'123', 4) == b'123\x00'
    assert _pad_to(b'1', 4) == b'1\x00\x00\x00'
    
    # test with larger alignment values
    assert len(_pad_to(b'123', 256)) == 256
    assert _pad_to(b'12345', 256)[:5] == b'12345'
    
    
    
def test_split_and_recombine(savestates):
    assert len(savestates) == 3, f"Expected 3 savestates, got {len(savestates)}"
    
        
    # Test the first .bin file
    bin_file, file_content = savestates[0]
    assert isinstance(file_content, bytes), f"File {bin_file} content is not bytes"
    
    split_state = _split_v86_savestate(file_content)
    recombined = _recombine_v86_savestate(split_state)
    assert recombined == file_content, f"Recombined file does not match original {bin_file}"
    
    
    # Test all .bin files
    for bin_file, file_content in savestates:
        assert isinstance(file_content, bytes), f"File {bin_file} content is not bytes"
        
        header_block, info_block, buffer_block = _split_v86_savestate(file_content)
        recombined = _recombine_v86_savestate((header_block, info_block, buffer_block))
        
        assert recombined == file_content, f"Recombined file does not match original {bin_file}"
    

def test_buffer_block_alignments(savestates):
    assert len(savestates) == 3, f"Expected 3 savestates, got {len(savestates)}"
    
    # Test the first .bin file
    bin_file, file_content = savestates[0]
    assert isinstance(file_content, bytes), "File {bin_file} content is not bytes"
    
    # Split the file content into its components
    header_block, info_block, buffer_block = _split_v86_savestate(file_content)
    
    aligned_buffer = _make_aligned_buffer_block(info_block, buffer_block, block_size=256)
    
    unaligned_buffer = _make_unaligned_buffer_block(info_block, aligned_buffer, block_size=256)
    
    assert unaligned_buffer == buffer_block, f"Unaligned buffer does not match original buffer block"
    
    # Recombine the components
    recombined = _recombine_v86_savestate((header_block, info_block, buffer_block))
    assert recombined == file_content, f"Recombined file does not match original {bin_file}"
    
    # Test multiple .bin files
    for savestate in savestates:
        bin_file, file_content = savestate
        assert isinstance(file_content, bytes), f"File {bin_file} content is not bytes"
        
        # Split the file content into its components
        header_block, info_block, buffer_block = _split_v86_savestate(file_content)
        
        # Create aligned buffer block
        aligned_buffer = _make_aligned_buffer_block(info_block, buffer_block, block_size=256)
        
        # Create unaligned buffer block
        unaligned_buffer = _make_unaligned_buffer_block(info_block, aligned_buffer, block_size=256)
        
        assert unaligned_buffer == buffer_block, f"Unaligned buffer does not match original buffer block"
        
        # Recombine the components
        recombined = _recombine_v86_savestate((header_block, info_block, buffer_block))
        assert recombined == file_content, f"Recombined file does not match original {bin_file}"
    
# Tests for public API functions
def test_encode_decode_roundtrip(savestates):
    """Test the encode and decode functions in a roundtrip scenario"""
    
    # get only the file contents of the savestates variable
    savestream = [s[1] for s in savestates]
    
    # Assert that each entry in savestream is of type bytes
    for i, save in enumerate(savestream):
        assert isinstance(save, bytes), f"Entry {i} in savestream is not bytes"
        
    # Encode the savestates
    savestream = encode(savestream)
    assert isinstance(savestream, bytes), "Encoded savestream is not bytes"
    
    # Decode the savestream
    decoded_savestates = decode(savestream)
    
    # Verify the decoded savestates match the original
    assert len(list(decoded_savestates)) == len(savestates), f"Decoded savestates length {len(decoded_savestates)} does not match original {len(savestates)}"
    
    # Verify the types of the decoded savestates match the original
    for i, decoded in enumerate(decoded_savestates):
        assert isinstance(decoded, bytes), f"Decoded savestate {i} is not bytes"
        assert isinstance(savestates[i][1], bytes), f"Original savestate {i} is not bytes"
        assert savestates[i][1] == decoded, f"Decoded savestate {i} does not match original state {i}"
        
def test_decode_one(savestates):
    """Test the decode_one function"""
    
    # get only the file contents of the savestates variable
    savestream = [s[1] for s in savestates]
    
    # Assert each entry is of type bytes
    for i, save in enumerate(savestream):
        assert isinstance(save, bytes), f"Entry {i} in savestream is not of type bytes"
        
    # Encode the savestates
    savestream = encode(savestream)
    
    # Use decode_one to check if each reconstructed savestate is the same as the original
    for i in range(len(savestates)):
        assert decode_one(savestream, i) == savestates[i][1]
    
    

def test_decode_len(savestates):
    """Test the encode and decode functions with length checks"""
    
    # get only the file contents of the savestates variable
    savestream = [s[1] for s in savestates]
    
    # Assert that each entry in savestream is of type bytes
    for i, save in enumerate(savestream):
        assert isinstance(save, bytes), f"Entry {i} in savestreams is not bytes"
        
    # Encode the savestates
    savestream = encode(savestream)
    
    unpacked_savestream = msgpack.unpackb(savestream, strict_map_key = False)
    assert decode_len(savestream) == len(savestates)



# Miscellaneous tests
def test_info_encode_decode_roundtrip(savestates):
    """Test info patch encode/decode"""
    
    state_list = [s[1] for s in savestates]
    
    # Extract all the info segments and diff them with jsonpatch
    previous_info = {}
    reconstructed_info = {}
    
    for state in state_list:
        
        header_block, info_block, buffer_block = _split_v86_savestate(state)
        
        # Decode the info block
        info = json.loads(info_block.decode('utf-8'))
        
        diff = list(dictdiffer.diff(previous_info, info))
        
        # apply patch to reconstructed_info
        reconstructed_info = dictdiffer.patch(diff, reconstructed_info)
        
        #breakpoint()    
        
        assert reconstructed_info == info, f"Reconstructed info does not match original info"
        
        
        
        
    

        
        
def test_decode_bytes(savestates):
    savestream = [s[1] for s in savestates]
    
    info_blocks = []
    
    for i, save in enumerate(savestream):
        header_block, info_block, buffer_block = _split_v86_savestate(save)
        info_blocks.append(info_block)
        assert isinstance(save, bytes)
        
        
    savestream = encode(savestream)
    assert isinstance(savestream, bytes)
    
    incremental_saves = msgpack.unpackb(savestream, strict_map_key=False)
    
    # apply dictdiffer to incremental_saves[0]['info_patch'] with the info block to reconstruct the info block
    # and compare it with the original info block
    reconstructed_info = {}
    
    for save in incremental_saves:
        info_patch = save['info_patch']
        
        
        diff = json.loads(info_patch.decode('utf-8'))
        
        reconstructed_info = dictdiffer.patch(diff, reconstructed_info)
        # make check the same order as the original info block
        reconstructed = json.dumps(reconstructed_info, separators=(',',':')).encode('utf-8')
        
        original = json.dumps(json.loads(info_blocks[i].decode('utf-8')), separators=(',',':')).encode('utf_8')
        # remove spaces from ch
        
        #breakpoint()
    
    assert len(incremental_saves) == len(savestates)
    
def test_trim_savestates_roundtrip(savestates):
    state_list = [s[1] for s in savestates]
    
    full_savestream = encode(state_list)
    trimmed_savestream = trim(full_savestream, 1, 2)
    decoded = list(decode(trimmed_savestream))
    
    assert len(decoded) == 1
    assert decoded[0] == state_list[1]
    
    
def test_empty_input():
    """Test encoding and decoding an empty list of savestates"""
    savestream = encode([])
    assert decode_len(savestream) == 0
    assert list(decode(savestream)) == []
    
        
def test_deduplication_efficiency(savestates):
    """Test that the deduplication is working effectively"""
    savestream = [s[1] for s in savestates]
    
    savestream = encode(savestream)
    
    total_size = 0
    for state in savestates:
        total_size += len(state[1])
        
    compression_ratio = len(savestream) / total_size
    
    assert compression_ratio < 0.5, f"Compression ratio {compression_ratio} is higher than expected"
    
    decoded = decode(savestream)
    
    for i, state in enumerate(decoded):
        assert savestates[i][1] == state, f"Decoded savestate {i} does not match original state {i}"

    
    
# Tests for CLI functionality
def test_cli_encode_decode_roundtrip(savestates):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        input_dir = tmpdir / "input"
        input_dir.mkdir()
        output_stream = tmpdir / "savestream.bin"
        decoded_dir = tmpdir / "decoded"
        decoded_dir.mkdir()
        
        for i, (_, data) in enumerate(savestates):
            with open(input_dir / f"state_{i:04d}.bin", "wb") as f:
                f.write(data)
                
        # --- CLI: Encode ---
        encode_cmd = [
            sys.executable, "-m", "v86_savestreams.__init__",
            "encode",
            *(str(input_dir / f"state_{i:04d}.bin") for i in range(len(savestates))),
            str(output_stream)
        ]  
        result = subprocess.run(encode_cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Encode command failed: {result.stderr}"
        assert output_stream.exists(), "Savestream file was not created"
        
    
        # --- CLI: Decode ---
        decode_cmd = [
            sys.executable, "-m", "v86_savestreams.__init__",
            "decode",
            str(output_stream),
            str(decoded_dir)
        ]
        result = subprocess.run(decode_cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Decode comand failed: {result.stderr}"
        
        # --- Compare original and decoded files ---
        for i in range(len(savestates)):
            original = savestates[i][1]
            decoded_path = decoded_dir / f"state_{i:04d}.bin"
            assert decoded_path.exists(), f"Decoded file {decoded_path} missing"
            
            with open(decoded_path, "rb") as f:
                decoded = f.read()
                
            assert decoded == original, f"Decoded state {i} does not match original state"   

def test_cli_trim(savestates):
    state_list = [s[1] for s in savestates]
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        input_stream_path = tmpdir / "full.savestream"
        output_stream_path = tmpdir / "trimmed.savestream"
        
        with open(input_stream_path, "wb") as f:
            f.write(encode(state_list))
            
        # Run the CLI trim command to keep states 1 and 2
        result = subprocess.run(
            [
                sys.executable, "-m", "v86_savestreams.__init__",
                "trim",
                str(input_stream_path),
                str(output_stream_path),
                "1", "2"
            ],
            capture_output=True, text=True
        )
        
        assert result.returncode == 0, f"Trim command failed: {result.stderr}"
        assert output_stream_path.exists(), "Trimmed savestream file was not created"
        
        with open(output_stream_path, "rb") as f:
            trimmed_savestream = f.read()
            
        decoded = list(decode(trimmed_savestream))
        assert len(decoded) == 1
        assert decoded[0] == state_list[1]
        
        
        # Run the CLI trim command to keep states 1 onwards
        result = subprocess.run(
            [
                sys.executable, "-m", "v86_savestreams.__init__",
                "trim",
                str(input_stream_path),
                str(output_stream_path),
                "1"
            ],
            capture_output=True, text=True
        )
        
        assert result.returncode == 0, f"Trim command failed: {result.stderr}"
        assert output_stream_path.exists(), "Trimmed savestream file was not created"
        
        with open(output_stream_path, "rb") as f:
            trimmed_savestream = f.read()
            
        decoded = list(decode(trimmed_savestream))
        assert len(decoded) == 2
        assert decoded[0] == state_list[1]
        assert decoded[1] == state_list[2]
    

if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
