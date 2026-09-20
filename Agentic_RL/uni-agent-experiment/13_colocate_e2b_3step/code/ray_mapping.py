import ctypes
import json
import ray
import torch
from runtime_common import ROOT, RUN_ID, save

hip = ctypes.CDLL('libamdhip64.so')
expected = set()
for i in range(torch.cuda.device_count()):
    buf = ctypes.create_string_buffer(64)
    assert hip.hipDeviceGetPCIBusId(buf, 64, i) == 0
    expected.add(buf.value.decode().lower())

ray.init(num_cpus=16, num_gpus=8, include_dashboard=False,
         namespace=RUN_ID+'-mapping', object_store_memory=2*1024**3)


@ray.remote(num_gpus=1)
class Probe:
    def inspect(self):
        import ctypes
        import torch
        visible = torch.cuda.device_count()
        torch.cuda.set_device(0)
        x = torch.ones(32, device='cuda')
        assert float(x.sum()) == 32
        lib = ctypes.CDLL('libamdhip64.so')
        lib.hipDeviceGetPCIBusId.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        lib.hipDeviceGetPCIBusId.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(64)
        assert lib.hipDeviceGetPCIBusId(buf, 64, 0) == 0
        return {'visible_count': visible, 'bus': buf.value.decode().lower()}


try:
    actors = [Probe.remote() for _ in range(8)]
    rows = ray.get([a.inspect.remote() for a in actors])
    result = {'expected_buses': sorted(expected), 'workers': rows,
              'success': len(expected) == 8 and {r['bus'] for r in rows} == expected
                         and all(r['visible_count'] == 1 for r in rows)}
    save(ROOT/'results/ray-gpu-mapping.json', result)
    print(json.dumps(result), flush=True)
    assert result['success']
finally:
    ray.shutdown()
