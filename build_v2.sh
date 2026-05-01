#!/bin/bash
set -e
cd ~/idios-repo
~/shader-sdk/wasi-sdk-14.0/bin/clang \
    -O3 \
    --target=wasm32 \
    -std=c++17 \
    -Wl,--export-dynamic,--no-entry,--allow-undefined \
    -nostdlib \
    -I ~/shader-sdk/beam/bvm \
    --output idios_contract.wasm \
    idios_contract.cpp
echo "Built idios_contract.wasm ($(stat -c%s idios_contract.wasm) bytes)"
