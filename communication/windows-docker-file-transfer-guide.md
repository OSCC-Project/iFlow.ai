# Windows ↔ Docker 容器文件传输踩坑记录

> 记录 2026-07-25 更新 ic_agent_os 时遇到的跨环境文件传输问题和解决方案。

## 环境层级

```
Windows 11 (物理机)
  └── WSL2 (Linux 虚拟机)
        └── Docker Desktop
              └── 容器 8b62a6201420 (iFlow 开发环境)
                    └── /opt/siliconcompiler/ic_agent_os/ic_agent_os/
```

## 问题场景

合作者在 Windows 桌面上传了新版本 `ic_agent_os_v1.1_20260716.tar.gz`，需要将其拷贝进 Docker 容器以更新 `/opt/siliconcompiler/ic_agent_os/ic_agent_os/`。

## 踩坑过程

### ❌ 尝试 1: Windows 资源管理器直接访问 `\\wsl$\`

在资源管理器输入 `\\wsl$\` 只能看到 WSL2 Linux 发行版的文件，**看不到 Docker 容器内部文件**。容器文件系统是 overlay 文件系统，由 Docker Desktop 的私有 VM 管理。

### ❌ 尝试 2: `\\wsl$\opt\...`

在 Windows 地址栏输入 `\\wsl$\opt\siliconcompiler\ic_agent_os\` → 找不到。因为 `/opt/` 在容器内部，不在 WSL2 层面。

### ❌ 尝试 3: 容器内使用 `docker cp`

容器内有 `/var/run/docker.sock` 和 `/usr/bin/docker`，但 docker CLI 无法连接 daemon：
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock.
Is the docker daemon running?
```

### ❌ 尝试 4: 从 `docker-desktop` WSL 发行版执行 docker cp

```cmd
wsl -d docker-desktop docker cp /home/ic_agent_os_v1.1_20260716.tar 8b62a6201420:/tmp/
```
报错：
```
It looks like you have tried to invoke the docker CLI from the docker-desktop WSL2 distribution.
This is not supported.
```

### ❌ 尝试 5: 直接用 Windows 路径

```cmd
docker cp C:\Users\yanzu\Desktop\ic_agent_os_v1.1_20260716.tar 8b62a6201420:/tmp/
```
报错：`The system cannot find the file specified` —— 因为文件已从桌面移到了 `\\wsl.localhost\docker-desktop\home\`。

### ❌ 尝试 6: copy 回桌面

```cmd
copy \\wsl.localhost\docker-desktop\home\ic_agent_os_v1.1_20260716.tar C:\Users\yanzu\Desktop\
```
报错：`系统找不到指定的文件。`

原因：**这次找的是 `.tar` 但实际文件是 `.tar.gz`**。之前已知文件名有 `ic_agent_os_v1.1_20260716.tar.gz` 但 copy 命令写的是 `.tar`。

### ❌ 尝试 7: docker cp 命令格式错误

```cmd
docker cp "\\wsl.localhost\docker-desktop\home\ic_agent_os_v1.1_20260716.tar.gz"8b62a6201420:/tmp/
```
报错：`'docker cp' requires 2 arguments` —— 引号后面**缺少空格**。

### ❌ 尝试 8: 命令末尾多余参数

```cmd
docker cp "\\wsl.localhost\docker-desktop\home\ic_agent_os_v1.1_20260716.tar.gz" 8b62a6201420:/tmp/8b62a6201420:/tmp/
```
报错：`no such directory` —— 粘贴时目标路径重复了。

### ✅ 最终成功的命令

```cmd
docker cp "\\wsl.localhost\docker-desktop\home\ic_agent_os_v1.1_20260716.tar.gz" 8b62a6201420:/tmp/
```

```
Successfully copied 140kB (transferred 142kB) to 8b62a6201420:/tmp/
```

## 关键经验

1. **Windows 无法直接访问 Docker 容器文件系统** — 只能通过 `docker cp` 中转
2. **`docker cp` 必须在 Windows CMD/PowerShell 中执行**，不能在 `docker-desktop` WSL 发行版中执行
3. **WSL2 的 `\\wsl.localhost\` 路径可以作为 `docker cp` 的源路径** — `docker cp` 会通过 Windows 读取 WSL2 文件再写入容器
4. **容器名用 `docker ps` 获取**（在 Windows CMD 中执行 `docker ps`），或者用 hostname（容器内 `hostname` 命令输出）
5. **检查文件名精确拼写** — `.tar` vs `.tar.gz` 差一个后缀就找不到
6. **注意空格** — Windows 路径如果有空格必须用双引号包裹，引号后跟目标路径时要加空格
7. **一行一条命令** — 不要试图用分号或特殊分隔符合并多条命令，容易粘贴出错

## docker cp 正确格式

```cmd
docker cp "源路径" 容器ID:目标路径
```

- 源路径：可以是 Windows 绝对路径 (`C:\...`) 或 WSL2 路径 (`\\wsl.localhost\...`)
- 容器 ID：`docker ps` 获取或容器内 `hostname`
- 目标路径：容器内绝对路径，**末尾要有 `/`**

## 环境速查

| 项目 | 值 |
|------|-----|
| 容器 ID | `8b62a6201420` |
| 容器 Python | 3.10.14 (`/opt/iFlow/.local/python3.10/bin/python3`) |
| `which python3` | `/opt/iFlow/.local/python3.10/bin/python3` |
| `$VIRTUAL_ENV` | (空, 无标准虚拟环境) |
| 项目根目录 | `/opt/siliconcompiler/ic_agent_os/ic_agent_os/` |
| siliconcompiler | v0.37.12 (editable install, `/opt/siliconcompiler/`) |
| PDK 路径 | `/opt/iFlow/foundry/` (nangate45 + sky130 + asap7) |
