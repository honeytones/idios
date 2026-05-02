#!/bin/bash
set -e
cd ~/idios-repo
CLANG=~/shader-sdk/wasi-sdk-14.0/bin/clang
CLANGPP=~/shader-sdk/wasi-sdk-14.0/bin/clang++
SYSROOT=/home/tones/shader-sdk/wasi-sdk-14.0/share/wasi-sysroot
INCLUDES="-I /home/tones/shader-sdk/beam/bvm"
GENERATE_SID=~/shader-sdk/build/sid-host/bvm/sid_generator/generate-sid

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

if [ -x "$GENERATE_SID" ]; then
    echo "Computing SID via generate-sid..."
    SID_OUTPUT=$("$GENERATE_SID" idios_contract.wasm)
    echo "$SID_OUTPUT"
    SID_LINE=$(echo "$SID_OUTPUT" | grep '^static const ShaderID' || true)
    if [ -n "$SID_LINE" ]; then
        python3 ~/idios-repo/scripts/patch_sid.py "$SID_LINE"
    else
        echo "WARNING: generate-sid produced no s_SID line, skipping header patch"
    fi
else
    echo "NOTE: generate-sid not found at $GENERATE_SID"
fi

echo "Built:"
echo "  idios_contract.wasm ($(stat -c%s idios_contract.wasm) bytes)"
echo "  idios_app.wasm ($(stat -c%s idios_app.wasm) bytes)"
