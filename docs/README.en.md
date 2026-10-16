# MindIE Agent for Kimi Code · Documentation Index

<a href="README.md">中文</a> | <b>English</b> · <a href="../README.en.md">Back to Repository Home</a>

This directory contains the automatic update design, framework stability guarantees, failure diagnostics, organizer process management, service continuity, test boundaries, and Windows launch contract documentation for `mindie-agent-kimi`. Every document is available in both English and Chinese.

## Technical Guides

| English Document | 中文 | Summary |
|---|---|---|
| [Automatic Update](update.en.md) | [自动更新机制](update.md) | Immutable generation staging, atomic pointer switching, rollback receipts, CLI commands, and host reload boundary |
| [Framework Stability](framework-stability.en.md) | [框架稳定性与验证](framework-stability.md) | Everyday usage contract, preflight & test suite execution, Windows atomic config I/O, and acceptance boundaries |
| [Failure Diagnostics](failure-diagnostics.en.md) | [故障诊断与本地进程取消](failure-diagnostics.md) | Read-only status checks, local process cancellation & `EOF` cleanup, and remote job reference preservation |
| [Organizer Diagnostics](organizer-diagnostics.en.md) | [整理器故障分类与进程组管理](organizer-diagnostics.md) | Static exit-code categories, process-group lifecycle management, and isolated organizer verification |
| [Service Continuity](service-continuity.en.md) | [插件更新期间的服务连续性](service-continuity.md) | Knowledge service stop/restore across updates, rollback handoff, and daemon acceptance boundaries |
| [Test Boundaries](test-boundaries.en.md) | [测试边界与故障检测](test-boundaries.md) | Cross-platform CI check ordering, independent failure dimensions, and stdio transport boundary tests |
| [Windows Launch Contract](windows-launch-contract.en.md) | [Windows 原生启动契约](windows-launch-contract.md) | Windows shell-free MCP launch, Job Object descendant cleanup, and native boundary verification |
