# 家庭照片智能相册方案修订 V1.1

> 针对 PR #1 Phase 0 诊断的正式修订
> 日期：2026-08-28

## 1. 为什么要修订

Phase 0 已确认：当前 Hermes 执行机 `ai-node02` 并不是原先假设的 GPU 台式机，而且其资源不足以承担 Immich：3.7 GiB RAM、根分区约 4.6 GiB 可用、Intel HD 4600、无 Docker。与此同时，NAS 照片路径尚未明确，`Multimedia/Camera Uploads` 只能视为候选目录。

因此原方案中“执行 Hermes 的机器 = 部署 Immich 的机器”的隐含假设废止。

## 2. 新架构

```text
Hermes Controller (ai-node02)
        |
        | API / SSH
        v
Photo Compute Node  <--- SMB RO ---> QNAP NAS
Immich Server                       原始照片
PostgreSQL
Immich ML (GPU 或 CPU)
```

### 角色定义

- `ai-node02`：仅控制/编排节点，禁止部署 Immich。
- QNAP：只保存原图，原图始终只读。
- Photo Compute Node：实际运行 Immich、PostgreSQL 和 Machine Learning。

## 3. Photo Compute Node 必须显式指定

新增变量：

```bash
PHOTO_COMPUTE_HOST=
PHOTO_COMPUTE_IP=
PHOTO_COMPUTE_MODE=   # gpu | cpu
```

Hermes 不得自动选择目标机。

当前：

```text
ai-node02 = forbidden
l14 = conditional candidate
真正的 8GB GPU 台式机 = preferred target, waiting for identification
```

## 4. 资源 Gate

完整 V1 目标主机：

```text
RAM >= 8 GiB，推荐 >=16 GiB
本地 SSD 可用 >=50 GiB 起步
Docker + Compose 可用
能够访问 QNAP
```

硬性拒绝：

```text
RAM < 6 GiB -> 不部署 Immich
本地可用空间 < 30 GiB -> 不部署 Immich
```

因此 `ai-node02` 明确不合格。

## 5. GPU 规则修订

旧规则废止：

```text
NVIDIA -> CUDA
AMD -> ROCm
Intel -> OpenVINO
```

新规则：

```text
NVIDIA + 驱动/容器验证通过 -> CUDA
AMD + 当前 Immich/ROCm 支持且实机验证通过 -> ROCm
Intel + 当前 Immich/OpenVINO 支持且实机验证通过 -> OpenVINO
其余或不确定 -> CPU
```

不允许仅凭 GPU 厂商推断后端。

### CPU fallback

GPU 不受支持时，如果主机 RAM/磁盘满足要求，可以：

```text
PHOTO_COMPUTE_MODE=cpu
```

继续部署。首次 OCR / Smart Search / Face 会慢，但比猜 GPU 后端更可靠。

`l14` 可作为 CPU 模式临时候选；是否启用其 AMD Renoir ROCm 必须单独验证。

## 6. NAS 路径 Gate

当前 QNAP：

```text
NAS_HOST=192.168.2.102
```

候选：

```text
Multimedia/Camera Uploads
```

它不是已确认家庭图库。

继续前必须人工明确：

```bash
NAS_SHARE=
NAS_PHOTO_SUBDIR=
```

未知路径不得猜。

## 7. NAS 只读账号

推荐创建：

```text
immich_reader
```

仅授予确认后的照片目录 Read Only。

不得退回使用 NAS 管理员账号作为长期运行凭据。

Secret 不得写进 GitHub。

## 8. sudo 修订

Hermes 控制节点不需要免密 sudo。

只有 Photo Compute Node 的一次性基础设施操作需要 sudo：

- 安装 Docker / cifs-utils
- 创建 `/mnt/nas/photos`
- 创建 `/opt/immich`
- 配置 mount/fstab

允许：

1. operator-assisted：管理员现场输入 sudo 密码；或
2. limited sudoers：仅对白名单命令开放。

不建议 `NOPASSWD: ALL`。

## 9. 时区修订

默认：

```text
TZ=Asia/Shanghai
```

宿主机保持 UTC 也可以；不要求修改宿主机时区。

## 10. 只读仍是双层保护

Photo Compute Node：

```text
SMB mount -> ro
```

Docker：

```yaml
- /mnt/nas/photos:/mnt/external/photos:ro
```

两层都必须只读。

写入测试如果成功，立即停止。

## 11. 新执行顺序

### Phase 0A：控制节点识别

确认 Hermes 当前运行在哪台机器，只记录，不安装 Immich。

### Phase 0B：Photo Compute Node 选择

明确 hostname/IP/OS/RAM/磁盘/GPU/Docker。

只有符合 Gate 才继续。

### Phase 0C：NAS 路径确认

人工明确 `NAS_SHARE + NAS_PHOTO_SUBDIR`。

### Phase 0D：权限准备

创建 `immich_reader`，只读授权。

### Phase 1：在 Photo Compute Node 挂载 NAS

只读挂载 + 写入失败测试。

### Phase 2：在 Photo Compute Node 部署 Immich

PostgreSQL、thumbnail、cache 全部放本地 SSD。

### Phase 3：ML Backend

优先验证 GPU；不成功则 CPU fallback。

### Phase 4～8

External Library、AI 索引、Hermes Skill、验收、备份按原方案继续。

## 12. 当前明确禁止

```text
禁止在 ai-node02 部署 Immich
禁止把 Multimedia/Camera Uploads 自动认定为家庭图库
禁止把 Intel HD 4600 自动映射为 OpenVINO
禁止把 AMD Renoir 自动映射为 ROCm
禁止因 sudo 不方便而降低 NAS 只读安全要求
```

## 13. 继续部署前需要用户明确的信息

1. 真正带 8GB 显存的台式机 hostname / LAN IP；
2. 该台式机 OS；
3. NAS 家庭照片的实际 `SHARE_NAME + SUBDIR`；
4. 是否允许在 QNAP 创建 `immich_reader` 只读账号。

如果 GPU 台式机暂时不可用，可以选择：

```text
l14 + CPU 模式
```

作为临时试运行，但不建议使用 `ai-node02`。

## 14. 安全边界不变

- 原图不移动、不删除、不改名、不修改；
- NAS 只读；
- Docker External Library `:ro`；
- PostgreSQL 不放 SMB/NFS；
- Hermes 无 delete_asset / empty_trash；
- Secret 不进入 GitHub；
- 不确认就 fail closed。
