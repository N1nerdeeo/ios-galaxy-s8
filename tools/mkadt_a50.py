#!/usr/bin/env python3
"""Build the Galaxy A50 (SM-A505FN, Exynos 9610) ADT.

Starts from the donor schema like tools/mkadt_dreamlte.py and replaces the
board facts with the A50's:
  - XNU RAM: the 2 GiB bank 0x880000000-0x8ffffffff
  - GIC-400: GICD 0x12301000, GICC 0x12302000; timer PPI 14 = INTID 30
  - timer 26 MHz
  - CPUs: logical 0-3 = Cortex-A53 MPIDR 0-3 (S-Boot boots 0),
    logical 4-7 = Cortex-A73 MPIDR 0x100-0x103
  - S-Boot framebuffer 1080x2340 XRGB8888 at 0xca000000
  - UFS: 0x13520000 (std HCI), 0x13521100 (vendor HCI), 0x13524000 (PHY)
"""
import argparse
import os
from pathlib import Path

import adt
import mkadt

DRAM_BASE = 0x880000000
# 512 MiB initial memory carveout
DRAM_SIZE = 0x20000000
FB_PHYS, FB_W, FB_H = 0xca000000, 1080, 2340
TIMEBASE = 26000000


def set_prop(node, name, value):
    for i, (key, flags, _) in enumerate(node.props):
        if key == name:
            node.props[i] = (key, flags, value)
            return
    node.props.append((name, 0, value))


def child(node, name):
    for candidate in node.children:
        if candidate.prop('name') == mkadt.cstr(name):
            return candidate
    raise ValueError(f'missing donor ADT node {name}')


def cpu_node(logical):
    a53 = logical < 4
    mpidr = logical if a53 else 0x100 + (logical - 4)
    cluster = 0 if a53 else 1
    return [
        ('device_type', mkadt.cstr('cpu')),
        ('compatible', (mkadt.cstr('arm,cortex-a53') if a53
                        else mkadt.cstr('arm,cortex-a73') + mkadt.cstr('arm,cortex-a53')) +
         mkadt.cstr('ARM,v8')),
        ('reg', mkadt.u32(mpidr)),
        ('cpu-id', mkadt.u32(logical)),
        ('cluster-id', mkadt.u32(cluster)),
        ('cluster-type', mkadt.u32(0)),
        ('die-cluster-id', mkadt.u32(cluster)),
        ('cluster-core-id', mkadt.u32(logical % 4)),
        ('l2-cache-id', mkadt.u32(cluster)),
        ('l3-cache-id', mkadt.u32(0)),
        ('state', mkadt.cstr('running' if logical == 0 else 'stopped')),
        ('timebase-frequency', mkadt.u32(TIMEBASE)),
        ('clock-frequency', mkadt.u32(1742000000 if a53 else 2314000000)),
        ('bus-frequency', mkadt.u32(100000000)),
        ('memory-frequency', mkadt.u32(1794000000)),
        ('peripheral-frequency', mkadt.u32(TIMEBASE)),
        ('fixed-frequency', mkadt.u32(TIMEBASE)),
    ]


def build(canvas=(FB_W, FB_H)):
    root = mkadt.build(seed=os.urandom(64), qemu=True)
    # The kernel's SweetPlatformExpert personality matches 'samsung,a5xelte'
    # and 'samsung,dreamlte'.
    set_prop(root, 'compatible', mkadt.cstr('samsung,a50') +
             mkadt.cstr('samsung,dreamlte') + mkadt.cstr('samsung,a5xelte') +
             mkadt.cstr('samsung,exynos9610') + mkadt.cstr('AppleARM'))
    chosen = child(root, 'chosen')
    set_prop(chosen, 'dram-base', mkadt.u64(DRAM_BASE))
    set_prop(chosen, 'dram-size', mkadt.u64(DRAM_SIZE))
    set_prop(chosen, 'firmware-version', mkadt.cstr('a50-entry'))
    set_prop(chosen, 'system-firmware-version', mkadt.cstr('a50-entry'))
    cpus = child(root, 'cpus')
    cpu0 = child(cpus, 'cpu0')
    for key, value in cpu_node(0):
        if key != 'cpu-id':
            set_prop(cpu0, key, value)
    for logical in range(1, 8):
        cpus.children.append(mkadt.node(f'cpu{logical}', cpu_node(logical)))
    arm_io = child(root, 'arm-io')
    set_prop(arm_io, 'device_type', mkadt.cstr('a50-io'))
    set_prop(arm_io, 'compatible', mkadt.cstr('samsung,exynos9610-io') + mkadt.cstr('arm-io'))
    set_prop(arm_io, 'ranges', mkadt.u64(0, 0x10000000, 0xf0000000))
    arm_io.children = [n for n in arm_io.children if n.prop('name') == mkadt.cstr('wdt')]
    # GIC-400: GICD physical 0x12301000 -> offset 0x02301000 relative to arm-io ranges (0x10000000)
    #          GICC physical 0x12302000 -> offset 0x02302000 relative to arm-io ranges (0x10000000)
    arm_io.children.append(mkadt.node('gic', [
        ('compatible', mkadt.cstr('arm,cortex-a15-gic')),
        ('interrupt-controller', mkadt.cstr('master')),
        ('virtual-timer-intid', mkadt.u32(30)),
        ('reg', mkadt.u64(0x02301000, 0x1000, 0x02302000, 0x1000)),
    ]))
    arm_io.children.append(mkadt.node('disp0', [
        ('device_type', mkadt.cstr('display-subsystem')),
        ('compatible', mkadt.cstr('samsung,a50-display') +
         mkadt.cstr('samsung,dreamlte-display') +
         mkadt.cstr('samsung,a5xelte-display')),
        ('canvas-width', mkadt.u32(canvas[0])),
        ('canvas-height', mkadt.u32(canvas[1])),
    ]))
    root.children = [n for n in root.children if n.prop('name') not in
                     (mkadt.cstr('sweet-fbcon'), mkadt.cstr('sweet-ufs'))]
    # Framebuffer console
    root.children.append(mkadt.node('a5xelte-fbcon', [
        ('reg', mkadt.u64(FB_PHYS, FB_W * FB_H * 4)),
        ('width', mkadt.u32(FB_W)),
        ('height', mkadt.u32(FB_H)),
        ('stride', mkadt.u32(FB_W * 4)),
    ]))
    # UFS controller (Exynos 9610): Standard HCI 0x13520000, Vendor HCI 0x13521100, PHY 0x13524000
    root.children.append(mkadt.node('dreamlte-ufs', [
        ('reg', mkadt.u64(0x13520000, 0x200, 0x13521100, 0x200, 0x13524000, 0x800)),
    ]))
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--canvas', default=f'{FB_W}x{FB_H}',
                        help='display size presented to iOS, WxH')
    args = parser.parse_args()
    blob = adt.serialize(build(tuple(int(v) for v in args.canvas.split('x'))))
    args.output.write_bytes(blob)
    print(f'{args.output}: {len(blob)} bytes')
