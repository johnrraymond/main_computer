#!/usr/bin/env python3
"""Intel Iris Xe DirectML-only NanoJev head-proxy inference smoke test. No CUDA device use."""
import argparse
import copy
import json
import math
import platform
import statistics
import sys
import time
from pathlib import Path

SEED = 20261031
INPUT_DIM = 1024
HIDDEN_DIM = 512
OUTPUT_DIM = 85  # proxy for sign + 8 digit slots x 10 + action


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', choices=['cpu', 'dml'], required=True)
    p.add_argument('--dml-index', type=int, default=-1, help='-1 = auto-select adapter whose name contains Intel/Iris')
    p.add_argument('--batches', type=int, nargs='+', default=[1, 64])
    p.add_argument('--warmup', type=int, default=12)
    p.add_argument('--steps', type=int, default=60)
    p.add_argument('--report', help='Destination JSON report')
    return p.parse_args()


def write_report(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + '.tmp')
    with open(temp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write('\n')
    temp.replace(target)
    print('report:', target)


def resolve_device(torch, backend, requested_index):
    if backend == 'cpu':
        return torch.device('cpu'), 'CPU', None

    import torch_directml
    count = torch_directml.device_count()
    cards = []
    for index in range(count):
        try:
            name = str(torch_directml.device_name(index))
        except Exception as exc:
            name = f'<name unavailable: {exc}>'
        cards.append((index, name))
    # Enumerate adapters for safety, but never execute on NVIDIA.
    # Only the selected Intel device receives tensors/model weights.
    if requested_index >= 0:
        chosen = requested_index
        if chosen >= count:
            raise RuntimeError(f'DirectML adapter index {chosen} out of range (0..{count - 1})')
    else:
        matches = [(i, name) for i, name in cards if ('intel' in name.lower() or 'iris' in name.lower())]
        if len(matches) != 1:
            raise RuntimeError('Could not unambiguously locate Intel integrated GPU; check device enumeration, drivers, BIOS, or specify --dml-index explicitly')
        chosen = matches[0][0]
    name = cards[chosen][1]
    if 'intel' not in name.lower() and 'iris' not in name.lower():
        raise RuntimeError(f'Refusing to benchmark non-Intel DirectML adapter {chosen}: {name}')
    print(f'Using Intel DirectML adapter [{chosen}] {name}')
    return torch_directml.device(chosen), name, chosen


def run(args):
    import torch
    if args.steps < 1 or args.warmup < 1 or not args.batches or any(x < 1 or x > 4096 for x in args.batches):
        raise ValueError('Steps, warmup, and batch sizes must be positive (batch <= 4096)')
    torch.set_num_threads(min(4, torch.get_num_threads()))
    torch.manual_seed(SEED)

    # Intentionally a reproducible HEAD-SIZED PROXY, NOT the real NanoJev module.
    # 568,405 parameters vs 570,453 in the inspected headless T00 readout inventory.
    class HeadProxy(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.projection = torch.nn.Linear(INPUT_DIM, HIDDEN_DIM)
            self.activation = torch.nn.ReLU()
            self.readout = torch.nn.Linear(HIDDEN_DIM, OUTPUT_DIM)

        def forward(self, x):
            return self.readout(self.activation(self.projection(x)))

    cpu_model = HeadProxy().eval()
    params = sum(p.numel() for p in cpu_model.parameters())
    device, device_name, index = resolve_device(torch, args.backend, args.dml_index)
    # Keep an independent CPU reference. model.to(device) mutates its module.
    device_model = copy.deepcopy(cpu_model).to(device).eval()
    print(f'Backend={args.backend} device={device_name} pytorch={torch.__version__} proxy_params={params:,}')
    report = {
        'test': 'nanojev_head_proxy_inference',
        'warning': 'Only a 2-layer MLP head-sized proxy, not NanoJev/Qwen actual inference or training',
        'backend': args.backend,
        'device_name': device_name,
        'dml_index': index,
        'torch_version': str(torch.__version__),
        'python_version': platform.python_version(),
        'model': {'name': 'headless_T00_sized_proxy', 'input_dim': INPUT_DIM, 'hidden_dim': HIDDEN_DIM, 'output_dim': OUTPUT_DIM, 'parameters': params},
        'measurements': [],
    }
    # DirectML cannot use torch.inference_mode() (DirectML issue #602);
    # torch.no_grad() works without the inference tensor version-counter error.
    with torch.no_grad():
        for batch in args.batches:
            gen = torch.Generator(device='cpu').manual_seed(SEED + batch)
            x_cpu = torch.randn(batch, INPUT_DIM, generator=gen, dtype=torch.float32)
            expected = cpu_model(x_cpu).detach().cpu()
            x_dev = x_cpu.to(device)

            def sync_forward():
                output = device_model(x_dev)
                # Output transfer forces completion of GPU work; matches a decision head returning to host.
                return output.detach().cpu()

            actual = sync_forward()
            diff = (actual - expected).abs()
            max_abs = float(diff.max().item())
            mean_abs = float(diff.mean().item())
            # Top-1 comparison is a coarse diagnostic of decision agreement.
            top1_mismatch = int((actual.argmax(dim=-1) != expected.argmax(dim=-1)).sum().item())
            numeric_pass = bool(math.isfinite(max_abs) and max_abs <= 0.01 and top1_mismatch == 0)
            print(f'batch={batch}: max_abs_error={max_abs:.7g}, mean_abs_error={mean_abs:.7g}, argmax_mismatch={top1_mismatch}; check={"PASS" if numeric_pass else "FAIL"}')
            # Any allocation/weight/device initialization must occur outside the clock.
            for _ in range(args.warmup):
                sync_forward()
            latency = []
            for _ in range(args.steps):
                start = time.perf_counter()
                sync_forward()
                latency.append((time.perf_counter() - start) * 1000.0)
            sorted_latency = sorted(latency)
            median_ms = statistics.median(sorted_latency)
            p95_ms = sorted_latency[math.ceil(0.95 * len(sorted_latency)) - 1]
            throughput = batch * 1000.0 / statistics.mean(sorted_latency)
            item = {
                'batch': batch, 'warmup': args.warmup, 'steps': args.steps,
                'median_ms': median_ms, 'p95_ms': p95_ms,
                'mean_ms': statistics.mean(sorted_latency),
                'items_per_s': throughput,
                'max_abs_error': max_abs,
                'mean_abs_error': mean_abs,
                'argmax_mismatches': top1_mismatch,
                'numerical_check_pass': numeric_pass,
            }
            report['measurements'].append(item)
            print(f'  median={median_ms:.3f} ms p95={p95_ms:.3f} ms throughput={throughput:.1f} head inputs/s')
    if args.report:
        write_report(args.report, report)
    return 0 if all(x['numerical_check_pass'] for x in report['measurements']) else 2


def main():
    args = parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f'FAIL: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
