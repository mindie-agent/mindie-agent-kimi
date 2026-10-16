# MindIE Agent Kimi Code Adapter Documentation Index

[中文](README.md) | English · [Back to Repository Home](../README.en.md)

This directory contains the automatic update, framework stability, failure diagnostics, organizer diagnostics, service continuity, test boundaries, and Windows launch contract documentation for `mindie-agent-kimi`. All documents are available in both English and Chinese.

## Technical Specifications

| Document (English) | 中文版本 | Description |
|---|---|---|
| [Automatic Update](update.en.md) | [自动更新机制](update.md) | Immutable generation staging, atomic pointer switch, rollback receipts, CLI commands, and host reload boundary |
| [Framework Stability](framework-stability.en.md) | [框架稳定性与验证](framework-stability.md) | Everyday usage contract, preflight & test suite execution, Windows atomic config I/O, and acceptance boundaries |
| [Failure Diagnostics](failure-diagnostics.en.md) | [故障诊断与本地取消](failure-diagnostics.md) | Read-only status diagnostics, local process cancellation & EOF cleanup, and remote job reference preservation |
| [Organizer Diagnostics](organizer-diagnostics.en.md) | [整理器故障与取消证据](organizer-diagnostics.md) | Static exit-code categories, process-group lifecycle management, and isolated organizer validation |
| [Service Continuity](service-continuity.en.md) | [插件更新期间的服务连续性](service-continuity.md) | Knowledge service stop/restore across updates, rollback handoff, and daemon acceptance boundaries |
| [Test Boundaries](test-boundaries.en.md) | [测试边界与故障检测](test-boundaries.md) | Cross-platform CI check ordering, independent failure dimensions, and stdio transport boundary tests |
| [Windows Launch Contract](windows-launch-contract.en.md) | [Windows 原生启动契约](windows-launch-contract.md) | Windows shell-free MCP launch, Job object descendant ownership, and native boundary validation |
