#!/bin/bash
# Kernel Mach-O -> Galaxy A50 (SM-A505FN, Exynos 9610) BOOT image.
# Usage:
#   make-boot-a50.sh [KERNEL] [OUT_IMG] [boot-args...]
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
kernel=${1:-"$repo/out/kernel/kernel.development.a5xelte"}
out=${2:-"$repo/out/boot-xnu-a50.img"}
shift 2 2>/dev/null || true

args=${*:-"cpus=8 rd=ufsroot0 ufsroot=userdata ufsrw=1 rootrw=1 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 cs_enforcement_disable=1 amfi_get_out_of_my_way=1 amfi_allow_any_signature=1 cs_debug=1 wdt_test=no_user_checkin a5fb_noconsole=1 sweetfb_noconsole=1 a5fb_keep_on=1 sweetfb_native=1 debug=0xc serial=3 keepsyms=1 -noprogress wdt=-1"}

work="$repo/out/a50"
mkdir -p "$work" "$(dirname "$out")"

echo "=== 1. Building shim/a50 loader ==="
bash "$repo/shim/a50/build.sh"

echo "=== 2. Generating Apple Device Tree (a50.adt) ==="
python3 "$repo/tools/mkadt_a50.py" "$work/a50.adt"

echo "=== 3. Packaging SWEETXNU payload (Mach-O + ADT + boot-args) ==="
python3 "$repo/tools/mkpayload.py" "$kernel" "$work/a50.adt" "$work/payload.bin" --cmdline "$args"

echo "=== 4. Embedding payload into Image (Image-with-xnu) ==="
python3 - "$repo/shim/a50/out/Image" "$work/payload.bin" "$work/Image-with-xnu" << 'EOF'
import struct, sys

loader_path = sys.argv[1]
payload_path = sys.argv[2]
out_path = sys.argv[3]

with open(loader_path, 'rb') as f:
    loader_data = bytearray(f.read())

pad1 = (4096 - (len(loader_data) % 4096)) % 4096
loader_data.extend(b'\x00' * pad1)
loader_aligned_size = len(loader_data)

with open(payload_path, 'rb') as f:
    payload_data = f.read()

combined = loader_data + payload_data
pad2 = (4096 - (len(combined) % 4096)) % 4096
combined.extend(b'\x00' * pad2)
total_size = len(combined)

assert combined[0x38:0x3c] == b'ARM\x64', "Invalid ARM64 Linux kernel header magic"

# Update 64-bit image_size at offset 0x10 (little-endian uint64)
struct.pack_into('<Q', combined, 0x10, total_size)

with open(out_path, 'wb') as f:
    f.write(combined)

print(f"Combined kernel image: loader={len(loader_data)} (padded {pad1}), payload={len(payload_data)}, total={total_size} (padded {pad2})")
print(f"Payload starts at offset: 0x{loader_aligned_size:x}")
print(f"Header image_size set to: 0x{total_size:x} ({total_size} bytes)")
EOF

echo "=== 5. Assembling boot-xnu-a50.img with mkbootimg ==="
mkbootimg \
  --kernel "$work/Image-with-xnu" \
  --base 0x10000000 \
  --kernel_offset 0x00008000 \
  --tags_offset 0x00000100 \
  --pagesize 2048 \
  --header_version 1 \
  --board SRPRL05B007KU \
  --os_version 12.0.0 \
  --os_patch_level 2022-09 \
  -o "$out"

img_size=$(stat -c %s "$out")
echo "Generated $out: $img_size bytes"

MAX_BOOT_SIZE=57671680 # 55 MiB boot partition
if [ "$img_size" -gt "$MAX_BOOT_SIZE" ]; then
    echo "ERROR: Image size $img_size exceeds boot partition limit $MAX_BOOT_SIZE" >&2
    exit 1
fi
echo "SUCCESS: Image size is well within the 55 MiB boot partition limit (using $((img_size * 100 / MAX_BOOT_SIZE))%)."
sha256sum "$out"
