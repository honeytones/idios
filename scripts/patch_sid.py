#!/usr/bin/env python3
"""
Patches the s_SID line in ~/idios-repo/idios_contract.h with whatever
generate-sid produced. Idempotent. If no s_SID line exists yet,
inserts one near the top of namespace Idios. If one exists, replaces it.
"""
import re
import sys
import pathlib

if len(sys.argv) != 2:
    sys.stderr.write("usage: patch_sid 'static const ShaderID s_SID = {...};'\n")
    sys.exit(1)

sid_line = sys.argv[1].strip()
if not sid_line.startswith("static const ShaderID s_SID"):
    sys.stderr.write("not a s_SID declaration: " + sid_line + "\n")
    sys.exit(1)

header = pathlib.Path.home() / "idios-repo" / "idios_contract.h"
src = header.read_text()
indented = "    " + sid_line

existing = re.compile(
    r'(?m)^[ \t]*static const ShaderID s_SID\s*=\s*\{[^}]*\};[ \t]*$'
)

if existing.search(src):
    new_src = existing.sub(indented, src, count=1)
    if new_src == src:
        print("idios_contract.h: s_SID already up to date")
    else:
        header.write_text(new_src)
        print("idios_contract.h: s_SID updated")
    sys.exit(0)

ns_open = re.compile(r'(?m)^namespace\s+Idios\s*\{\s*$')
m = ns_open.search(src)
if not m:
    sys.stderr.write(
        "could not find 'namespace Idios {' in idios_contract.h\n"
        "add this line manually:\n    " + sid_line + "\n"
    )
    sys.exit(1)

insert_at = m.end()
prefix = src[:insert_at]
suffix = src[insert_at:]
new_src = prefix + "\n" + indented + "\n" + suffix
header.write_text(new_src)
print("idios_contract.h: s_SID inserted into namespace Idios")
