# iflow-lab web 平台容器化（宿主机 EDA 模式）

## 定位

本目录是 **web 服务层** 的容器化：`server`（FastAPI 后端）+ `frontend`（Next.js 前端）。
EDA 工具链（iEDA / OpenROAD / yosys / iverilog / verilator / verible / sby / netgen）
与 PDK、RTL 用例都装在宿主机上，容器通过挂载调用——**先让平台可部署，EDA 容器化是后续课题**。

## 启动

```bash
cd /home/xu/ic_agent_os
docker compose -f docker/web/docker-compose.yml up -d --build
```

- 前端: http://localhost:3000
- 后端: http://localhost:8000（前端代码里 API 地址为 localhost:8000，浏览器直连宿主机端口）

## 挂载约定（必须与宿主机路径一致）

| 路径 | 用途 | 读写 |
|---|---|---|
| /home/xu/iEDA | iEDA 二进制 + 脚本 + 工艺文件 | ro |
| /home/xu/OpenROAD-flow-scripts | OpenROAD + ORFS PDK 数据 | ro |
| /home/xu/iFlow/rtl | 内置设计用例 (gcd/uart/aes) | ro |
| /usr/bin/{iverilog,vvp,verilator,yosys,sby,sta,netgen} | 仿真/综合工具 | ro |
| /home/xu/ic_agent_os/tmp | 运行产物 (ieda_runs/openroad_runs/...) | rw |
| /home/xu/ic_agent_os/server/{settings.json,.auth_secret} | DeepSeek key / JWT 密钥 | ro |
| /home/xu/ic_agent_os/server/{users.db,experiments} | 用户库 / 实验存档 | rw |
| /tmp/iflow_workspace | 上传设计/liberty/归档/Map | rw |

## 已知限制（如实说明）

- 二进制文件挂载要求容器基础镜像与宿主机 glibc 兼容（均 Debian 系，实测可用）；
  verible 在 /usr/local/bin，若环境不同需调整 compose。
- EDA 工具链完整容器化（含 PDK 数据、多工具版本隔离）在 docker/ 根目录的
  CLI 时代规划里，尚未迁移到 web 平台。
- 后端鉴权默认开启，首次启动后由前端自动注册匿名账号，users.db 挂载保证持久。
