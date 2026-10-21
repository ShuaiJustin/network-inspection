# GitHub Releases 与软件内更新实施计划

**Goal:** 补齐开发和发布入口，统一应用版本，交付签名验证的 Windows 软件内更新。

**Architecture:** 保留 frontend/backend/src-tauri 主体结构。Rust 保管更新对象及已验证的下载数据；React 仅调用受控命令并显示状态；发布脚本使用同一签名密钥生成安装包与 latest.json。

**Tech Stack:** Tauri 2 updater、React、FastAPI、PowerShell、GitHub Releases。

- [x] 补齐 start-dev.ps1 和 build-windows-installer.ps1：隔离 Python 环境、npm ci、锁定 Cargo、ASCII 构建路径、GUI 子系统验证。
- [x] 统一版本为 0.1.33：Tauri/Cargo/Cargo.lock/frontend/package-lock/backend；添加版本检查与递增脚本。
- [x] 先测试更新状态机：无更新、发现新版、重复操作、下载失败重试、进度、安装暂缓。
- [x] Rust 注册检查/下载/安装命令；签名验证后才保存安装数据；安装前检查后台任务并停止 sidecar。
- [x] 接入启动检查和设置页手动入口、下载进度、安装确认；浏览器模式禁用桌面调用。
- [x] 配置仓库外签名密钥和公钥；提供发布脚本与 GitHub Actions 手动发布工作流，清单和安装包必须一起验证后发布。
- [x] 验证前端测试及构建、后端测试、Rust 检查/测试、PowerShell 解析、实际安装包与清单；记录真实覆盖范围。
- [x] 审查差异与待上传文件，提交并上传源码和签名发布产物，核对远端版本与文件。

仓库：ShuaiJustin/network-inspection，公开；用户已明确授权实施、测试及上传。
