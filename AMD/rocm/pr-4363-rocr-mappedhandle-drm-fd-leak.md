# PR #4363 分析：ROCr `MappedHandle` 析构后仍持有 DRM FD

## 元信息

| 项 | 值 |
|---|---|
| PR | [ROCm/rocm-systems#4363](https://github.com/ROCm/rocm-systems/pull/4363) |
| 标题 | `rocr: Fix ROCr MappedHandle holding DRM FD after destruction` |
| Merge commit | `e27ce55c5ed8c7f22cb64d50d9f8239b694fc179`（短 SHA `e27ce55c5e`） |
| 作者 | Nick Kuo (amd-nicknick) |
| 日期 | 2026-04-01 19:18:02 +0800 |
| 目标分支 | `develop` |
| 父提交 | `297c2fc84e`（base）+ `2649fbc99f`（PR head） |
| 改动规模 | 2 文件，+15 −6 |

改动文件：

- `projects/rocr-runtime/runtime/hsa-runtime/core/runtime/runtime.cpp`
- `projects/rocr-runtime/runtime/hsa-runtime/core/util/lnx/os_linux.cpp`

---

## TL;DR

ROCr 为了满足 `kfd_peerdirect` 的需求，在**每次** `hipMemMap` 时都会偷偷用 `MAP_FIXED`
把用户预留的 VA 盖成一个指向 DRM 设备 FD 的 `MAP_SHARED` 映射。

而 unmap 路径上只用 `mprotect` 改了权限，**从来没有解除这个映射** ——
内核里 GEM 对象的引用计数因此永远降不到 0，导致 `hipMemRelease` 名义上释放了内存、
实际显存要一直拖到 `hipMemAddressFree` 才回收。

修复：在 `~MappedHandleAllowedAgent()` 的 CPU 分支里，用一次匿名 `MAP_FIXED` mmap
（`os::UncommitMemory`）把这段 VA 换回匿名内存 —— **引用还掉，地址留住**。

---

## 一、对象模型：VMEM API 把「地址」和「内存」拆开了

传统 `hipMalloc` 一步到位（分配物理内存 + 返回可用指针）。VMEM API
（`hipMemAddressReserve` / `hipMemCreate` / `hipMemMap` / `hipMemSetAccess`，
对应 CUDA 的 `cuMemCreate` 系列）把它拆成三个独立生命周期的对象：

| 对象 | ROCr 类 | 代表什么 | 谁持有 |
|---|---|---|---|
| 地址预留 | `AddressHandle` | 一段虚拟地址区间（VA），无后备内存 | `reserved_address_map_` |
| 物理内存 | `MemoryHandle` | 真实的显存/主机内存 BO，**无地址** | `memory_handles`（`unique_ptr`，析构即真正释放） |
| 映射关系 | `MappedHandle` | 把某个 `MemoryHandle` 绑到某段 VA 上 | `mapped_handle_map_` |

`MappedHandle` 内部还有一层（`core/inc/runtime.h:1136`）：

```cpp
std::map<Agent*, MappedHandleAllowedAgent> allowed_agents;
```

**每个被授权访问这块内存的 agent（CPU 或某个 GPU），对应一个 `MappedHandleAllowedAgent`。**
这个对象的职责就是「让 agent X 能访问这段 VA」，它的析构函数负责撤销这件事 ——
`core/inc/runtime.h:1108`，也就是本 PR 修改的类。

### 引用计数

`MemoryHandle` 上有两个计数（`core/inc/runtime.h:1090-1091`）：

- `ref_count` —— 用户句柄引用数：`hipMemCreate` 置 1，`hipMemRelease` 减 1
- `use_count` —— 有多少个 `MappedHandle` 正在用它：`hipMemMap` 加 1，`hipMemUnmap` 减 1

**两者都归零，`MemoryHandle` 才被 erase → 析构 → 通知驱动释放**
（`core/runtime/runtime.cpp:4186-4195` 与 `:4282-4288`）。

> 这层设计本身没问题，bug 不在这里。

---

## 二、调用链

典型用法（PyTorch caching allocator / `expandable_segments:True` 正是这个模式）：

```
hipMemAddressReserve(&ptr, 4GB)      ← 只 reserve 一次，很大
  ↓ 循环里反复：
hipMemCreate(&h, 256MB)
hipMemMap(ptr+off, 256MB, 0, h)
hipMemSetAccess(ptr+off, 256MB, {GPU0, read-write})
   ... 使用 ...
hipMemUnmap(ptr+off, 256MB)
hipMemRelease(h)
```

下沉路径：`HIP` → ROCclr（`rocclr/device/rocm/rocrctx.hpp`）→ `hsa_amd_vmem_*` → `Runtime::VMemory*`

| HIP API | ROCr 实现 | 位置 |
|---|---|---|
| `hipMemAddressReserve` | `VMemoryAddressReserve` | `runtime.cpp:4023` |
| `hipMemCreate` | `VMemoryHandleCreate` | `runtime.cpp:4123` |
| `hipMemMap` | `VMemoryHandleMap` | `runtime.cpp:4199` |
| `hipMemUnmap` | `VMemoryHandleUnmap` | `runtime.cpp:4242` |
| `hipMemRelease` | `VMemoryHandleRelease` | `runtime.cpp:4175` |
| `hipMemAddressFree` | `VMemoryAddressFree` | `runtime.cpp:4070` |

几个要点：

- **`VMemoryAddressReserve`**：向 KFD 要纯地址（`memFlags.ui32.OnlyAddress = 1`），
  或走 `os::ReserveMemory` 的 `mmap(MAP_PRIVATE|MAP_NORESERVE|MAP_ANONYMOUS)`。
  **此时这段 VA 是匿名映射。**
- **`VMemoryHandleCreate`**：`region->Allocate()` 分配 BO，再 `CreateShareableHandle()`
  拿到 `mmap_offset`（DRM 标准玩法：mmap 设备节点的某个特殊 offset，内核就把对应 BO 映射给你）。
- **`VMemoryHandleUnmap`** 的核心循环（`runtime.cpp:4265-4290`）：

  ```cpp
  for (agentPermsIt = allowed_agents.begin(); agentPermsIt != end; ) {
      agentPermsIt->second.RemoveAccess();               // ① 撤销访问权
      agentPermsIt = allowed_agents.erase(agentPermsIt); // ② erase → 触发析构 ★
  }
  mem_handle->use_count--;
  if (!use_count && !ref_count) ReleaseMemoryHandle(...); // ③ 可能真正释放
  ```

  第 ② 行的 `erase` 就是 `~MappedHandleAllowedAgent()` 被调用的地方。

---

## 三、Bug 根源：一个你从未要求过的 CPU 映射

`MappedHandle` 构造函数（`runtime.cpp:4408-4424`）：

```cpp
if (!mem_handle->imported) {
    /*
     * Create default CPU mapping. This is needed for the kfd_peerdirect drivers
     * to look up the VA when sharing this BO to a third party driver.
     */
    auto cpu_agent = agentOwner()->GetNearestCpuAgent();
    allowed_agents.emplace(cpu_agent, {..., HSA_ACCESS_PERMISSION_NONE});
    agentPermsIt->second.EnableAccess(HSA_ACCESS_PERMISSION_NONE);   // ★
}
```

**每次 `hipMemMap`，ROCr 都无条件额外建一个 `PROT_NONE` 的 CPU 映射。**
用户从未要求 CPU 访问这块显存 —— 这纯粹是 ROCr 内部为了让 `kfd_peerdirect`
（RDMA / GPUDirect 路径）能通过 VA 反查 BO 而留的。

> ⚠️ 因此**纯 GPU-only 的 workload 一样中招**。

`EnableAccess` 的 CPU 分支（`runtime.cpp:4349-4373`）：

```cpp
agent->driver().GetDeviceFd(agent->node_id(), &mmap_fd);   // /dev/dri/renderD128 的 fd
rocr::os::MapMemory(va, size, PROT_NONE, mmap_fd, mem_handle->driver_handle.mmap_offset);
```

`os::MapMemory`（`core/util/lnx/os_linux.cpp:863-864`）：

```c
mmap(va, size, PROT_NONE, MAP_SHARED | MAP_FIXED, drm_fd, mmap_offset);
```

**`MAP_FIXED` 把用户预留的匿名 VA，原地换成了指向 DRM 设备 FD 的 `MAP_SHARED` 映射。**
这就是整个 bug 的物理基础。

### 为什么一个 `PROT_NONE` 映射会「持有」内存？

Linux DRM/GEM 的核心机制。mmap 一个 GEM 对象时内核走 `drm_gem_mmap()`：

```c
drm_gem_object_get(obj);          // ← BO 引用计数 +1
vma->vm_ops = &drm_gem_vm_ops;    // vm_ops->close = drm_gem_vm_close
vma->vm_private_data = obj;
```

**引用计数挂在 VMA 上，不是挂在 protection 上。**
只要 VMA 存在，`obj` 的 refcount 就 ≥ 1，内核绝不释放底层显存 —— 哪怕：

- 权限被改成 `PROT_NONE`（`mprotect` 只动 `vma->vm_page_prot`，不碰 `vm_file` / `vm_private_data`）
- 用户态已调用 `hipMemRelease`
- ROCr 已调用 `DestroyMemoryHandle` 告诉驱动「我不要了」

驱动收到 destroy 请求时只是 `drm_gem_object_put()` 减一次引用，
发现没到 0，就静静地推迟释放。

### 修复前的执行序列

**① `RemoveAccess()`** —— CPU 分支（`runtime.cpp:4384-4393`）：

```cpp
rocr::os::ProtectMemory(va, size, PROT_NONE);   // 只是 mprotect
```

VMA 依然是 `MAP_SHARED` 指向 `drm_fd`，BO refcount 不变。

**② `erase()` → 析构函数** —— 修复前：

```cpp
if (targetAgent->device_type() == kAmdCpuDevice) return;   // ← 直接跑路，什么都没做
```

**这就是 bug。** CPU 分支从建立映射（mmap FD）到销毁，全程没有任何一步解除那个 VMA。
GPU 分支有对称清理（`DestroyMemoryHandle`），CPU 分支只有「建」没有「拆」。

**③ `hipMemRelease`** → `ref_count` 归零 → `~MemoryHandle`（`runtime.cpp:4461-4474`）
→ `DestroyMemoryHandle` → 内核 refcount 2→1，**不释放**。

此时 ROCr 层面所有账都平了（`use_count=0`、`ref_count=0`、对象都析构了），
但**物理显存一分没还**。对应 PR 描述原文：

> The outstanding reference leak to FD causes `hipMemRelease` to not actually release the memory

**④ 真正释放的时刻**：只有 `hipMemAddressFree` → `VMemoryAddressFree`
→ `hsaKmtFreeMemory` / `os::ReleaseMemory`（`munmap` 整段 VA）
→ 触发 `vm_ops->close` → `drm_gem_vm_close` → 最后一次 put → refcount 归零 → 显存回来。

> until `hipMemAddressFree`, where the entire VA range is relinquished including the opened FD range

### 实际影响

VMEM 的整个卖点就是「reserve 一大段 VA，反复 map/unmap 复用」，
而 `hipMemAddressFree` 在这种模式下几乎永远不会被调用。
**结果就是显存单调增长，直到 OOM。**

---

## 四、修复

### 4.1 主修复：`~MappedHandleAllowedAgent()`

`runtime.cpp:4331-4346`：

```cpp
Runtime::MappedHandleAllowedAgent::~MappedHandleAllowedAgent() {
  if (targetAgent->device_type() == kAmdCpuDevice) {
    if (...IsWslDxg()) assert(!"Unimplemented");
    /* Remap the CPU mapping back to anonymous, freeing the DRM FD while retaining VA reservation */
    bool result = rocr::os::UncommitMemory(va, size);
    assert(result && "Failed to remap VA to anonymous");
  } else {
    /* GPU 分支不变：DestroyMemoryHandle */
  }
}
```

`os::UncommitMemory`（`core/util/lnx/os_linux.cpp:909-915`）：

```c
mmap(addr, size, PROT_NONE, MAP_PRIVATE | MAP_FIXED | MAP_NORESERVE | MAP_ANONYMOUS, -1, 0);
```

这一句 `MAP_FIXED` 匿名 mmap 一步做了两件事：

1. **销毁旧 VMA** —— 内核在建立新映射前先 `munmap` 掉重叠区间，触发
   `drm_gem_vm_close` → `drm_gem_object_put` → **BO 引用计数减到位**。这是修复的本质。
2. **立刻填回匿名 `PROT_NONE` 映射** —— VA 的占位没有丢。

配合 `MAP_NORESERVE` + `PROT_NONE`，这个匿名映射不消耗物理页、不占 commit charge，
纯粹是个地址占位符。

### 4.2 附带改动

- `os_linux.cpp`：`mmap` → `::mmap`，显式全局作用域，与同文件 `::munmap` / `::mprotect`
  写法对齐。纯风格，无行为变化。
- `runtime.cpp`：新增 `#include <cassert>`，因为新代码在 CPU 分支引入了 `assert`。
- WSL/DXG 分支加 `assert(!"Unimplemented")`：`MappedHandle` 构造函数在 DXG 上直接
  return（`runtime.cpp:4406`），根本没建这个 CPU 映射，所以析构也不该走到这里 ——
  这是个「将来 DXG 路径打通了记得回来补」的标记。

---

## 五、设计考量

### 为什么不能直接 `munmap`（`os::UnmapMemory`）？

**VA 的所有权不属于这一层。** 这段地址是用户通过 `hipMemAddressReserve` 拿到的，
归 `AddressHandle` 管，只有 `hipMemAddressFree` 才有权归还。

如果这里 `munmap`，就会在用户预留区间中间挖出一个真空洞 —— 之后任何一次
`mmap(NULL, ...)`、动态库加载、走 mmap 路径的 `malloc`，都可能把这个洞抢走。
等用户下次 `hipMemMap` 到同一地址时，`MAP_FIXED` 会**静默覆盖掉别人的映射**，
变成极难排查的内存踩踏。

所以必须用 `UncommitMemory` 而非 `UnmapMemory`：**释放后备资源，但保留地址所有权**。
这正是 `Uncommit` 的命名含义（对称的 `CommitMemory` 在 `os_linux.cpp:901`）。

### 为什么放在析构函数，而不是 `RemoveAccess()`？

语义不同，`RemoveAccess` 是**可逆**的：

- `RemoveAccess()` = 「暂时不让访问」→ `mprotect(PROT_NONE)`，映射关系还在，
  后续 `EnableAccess()` 一个 `mprotect` 就能恢复
- 析构 = 「这个 agent 对这块内存的授权对象彻底没了」→ 才该把底层 FD 引用还掉

而 `VMemoryHandleUnmap` 里这两步紧挨着（`RemoveAccess()` 后立刻 `erase()`，
`runtime.cpp:4270-4274`），所以放在析构里既保住了 `RemoveAccess` 的可逆语义，
又保证 unmap 路径上一定会执行。

**额外好处（RAII）**：不管是走 `hipMemUnmap` 的正常路径，
还是 `MappedHandle` 因异常/进程退出被析构，清理都有保证。

### 顺序为什么是对的？

在 `VMemoryHandleUnmap` 里，`allowed_agents.erase()`（→ 匿名重映射，放掉 VMA 引用）
发生在 `ReleaseMemoryHandle()`（→ `DestroyMemoryHandle`，通知驱动释放）**之前**。

顺序必须如此：先清掉用户态 VMA 引用，驱动的 destroy 请求才能立即生效。
反过来的话，destroy 又会被推迟。

---

## 六、引用计数时间线对比

| 步骤 | 修复前 BO refcount | 修复后 BO refcount |
|---|---|---|
| `hipMemMap` 后（含隐式 CPU 映射） | 2 | 2 |
| `RemoveAccess`（`mprotect`） | 2 | 2 |
| `~MappedHandleAllowedAgent` | **2**（无操作） | **1**（VMA 换成匿名） |
| `hipMemRelease` → `DestroyMemoryHandle` | **1 → 显存不释放** ❌ | **0 → 显存立即释放** ✅ |
| `hipMemAddressFree` | 0 → 此时才释放 | （只是归还 VA） |

---

## 七、一句话总结

> ROCr 为了 peerdirect 需求，在每次 `hipMemMap` 时偷偷用 `MAP_FIXED` 把用户的 VA
> 盖成指向 DRM FD 的共享映射；unmap 时只用 `mprotect` 改了权限，从没解除这个映射，
> 导致内核 GEM 对象引用计数永远降不到 0，显存要拖到 `hipMemAddressFree` 才释放。
> 修复就是在析构时用一次匿名 `MAP_FIXED` mmap 把这段 VA 换回去 —— **引用还掉，地址留住**。

---

## 附录 A：当前 develop 与 PR 原始版本的差异

本文档中的行号与代码引自 **当前 develop**，它在此 PR 之后又有演进：

| PR 原始版本 | 当前 develop |
|---|---|
| `thunkLoader()->IsDXG()` | `thunkLoader()->IsWslDxg()` |
| `driver().DestroyImportedShareableHandle(&shareable_handle)` | `driver().DestroyMemoryHandle(&driver_handle)`，且加了 `owns_driver_handle` 判断 |
| — | 补了 `(void)result` / `(void)status`，消除 release 构建的未使用变量告警 |

**PR 引入的核心逻辑（CPU agent 析构时 `UncommitMemory` 重映射回匿名）原封不动保留至今。**

## 附录 B：复现分析过程的命令

```bash
# 1. 从 PR 号找到 merge commit（GitHub squash/merge 会把 (#PR号) 写进 message）
git log --all --oneline --grep="#4363"

# 2. 查看 commit 详情（作者、日期、父提交）
git show --stat --no-patch e27ce55c5e

# 3. 查看该 PR 对 develop 的真实影响
#    注意：merge commit 要用 base..merge，不能用 base..head
#    （base..head 会混入两个分支分叉期间的所有无关改动）
git diff 297c2fc84e e27ce55c5e
```

> `gh pr view 4363 --repo ROCm/rocm-systems` 走不通 ——
> ROCm 组织禁用了 classic PAT 访问，会报
> `GraphQL: ROCm forbids access via a personal access token (classic)`。
> 直接用 `git log --grep` 更快，也不依赖网络和 token 权限。
