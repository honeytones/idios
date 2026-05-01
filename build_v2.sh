#!/bin/bash
set -e
cd ~/idios-repo
CLANG=~/shader-sdk/wasi-sdk-14.0/bin/clang
CLANGPP=~/shader-sdk/wasi-sdk-14.0/bin/clang++
SYSROOT=/home/tones/shader-sdk/wasi-sdk-14.0/share/wasi-sysroot
INCLUDES="-I /home/tones/shader-sdk/beam/bvm"

echo "Compiling contract shader..."
$CLANG -O3 --target=wasm32 -std=c++17 \
    -Wl,--export-dynamic,--no-entry,--allow-undefined \
    -nostdlib $INCLUDES \
    --output idios_contract.wasm idios_contract.cpp

echo "Compiling app shader..."
$CLANGPP -O3 --target=wasm32-wasi --sysroot=$SYSROOT -std=c++17 \
    -Wl,--export-dynamic,--no-entry,--allow-undefined \
    -nostdlib $INCLUDES \
    --output idios_app.wasm idios_app.cpp

echo "Built:"
echo "  idios_contract.wasm ($(stat -c%s idios_contract.wasm) bytes)"
echo "  idios_app.wasm ($(stat -c%s idios_app.wasm) bytes)"
