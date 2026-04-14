# CodeTranslate

高保真增量迁移引擎第一版骨架，强调“依赖图驱动、逐单元迁移、逐单元验证、失败局部修复”。

## 当前已实现

- 项目扫描与入口识别
- Python AST 分析、符号抽取、数据模型抽取、基础调用图
- 迁移 unit 拆分、依赖关系、ready queue 与 unit 状态机
- workspace 持久化
- unit context 裁剪骨架
- migrator / tester / verifier / repairer / orchestrator 主循环骨架
- `uv` 管理的 CLI 工程
- 多语言 adapter 架构骨架，当前已落地 Python / Node.js / Java / Go（Java / Python / Node.js / Go 均补充了复杂静态启发式分析）
- Java 项目补充 Maven 多模块识别、模块布局采样与 baseline build/test 执行结果归档
- unit context 补充相关测试文件、资源文件、Maven 模块信息与 Java->Python 迁移提示

## API Key 放置方式

不要把 key 写进源码，也不要提交到 Git。

推荐方式：

1. 在仓库根目录放本地 `.env`
2. 填入下面这些变量
3. 保证 `.env` 已被 `.gitignore` 忽略

```bash
CODETRANSLATE_API_KEY=your_api_key_here
CODETRANSLATE_BASE_URL=https://oneapi.zaiwenai.com/v1
CODETRANSLATE_MODEL=gpt-4o
```

当前实现会优先读取环境变量，也会自动加载仓库根目录的 `.env`。

## 快速使用

```bash
uv run codetranslate start
```

启动后按提示输入：

- `Project path`
- `Output path`
- `Source language`
- `Target language`
- `Action [analyze|plan|run]`

默认输出目录：

- target: `<project-parent>/<project-name>_translated`
- workspace: `<target-parent>/.codetranslate-workspace`

如果你仍然想走非交互命令，也可以：

```bash
uv run codetranslate \
  --project-root examples/sample_source \
  --workspace-root .demo-workspace \
  --target-root .demo-target \
  --source-language python \
  --target-language python \
  run
```

## Workspace 输出

运行后会生成：

- `.demo-workspace/analysis`
- `.demo-workspace/plan`
- `.demo-workspace/state`
- `.demo-workspace/contexts`
- `.demo-workspace/logs`
- `.demo-workspace/patches`
- `.demo-workspace/reports`
- `.demo-target/`

针对 Java / Maven 项目，额外会生成：

- `analysis/maven_modules.json`
- `analysis/project_layout.json`
- `analysis/project_insights.json` 中的 `java_baseline`

## 当前仍未完整实现

以下能力目前还是第一版边界内的骨架或弱实现：

- DFG / 更完整 CFG
- Java 已补充静态启发式：反射 / 注解 / IoC 容器分析、复杂动态调用提示、中间件识别与语义抽取、异步链路还原；但仍非完整语义级/字节码级分析
- Python / Node.js / Go 已补充复杂静态启发式：入口识别、框架路由/中间件识别、动态调用风险点、异步链路与高风险文件汇总；但仍非完整语义级分析
- 多语言分析结果中的 `project_insights` 已统一为按语言分组结构：`language_insights.<language>.{summary,migration_notes,high_risk_files,details}`，同时保留聚合后的 `summary` / `migration_notes` / `high_risk_files`
- 精细的行为对齐测试生成
- 真实最小 patch 级代码修复
- 模块级和系统级的真实 build / startup / smoke 路径验证
- 多语言真实迁移实现
- 基于持久化分析结果的完整反序列化恢复
