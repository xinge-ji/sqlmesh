# SQLMesh 架构总入口

> 状态：current
> 创建日期：2026-06-08
> 最近更新：2026-06-08

## 1. 项目简介

SQLMesh 是一个数据转换与部署框架，围绕模型加载、快照指纹、计划生成和执行部署来管理数据项目变更。核心价值是让用户在虚拟环境中预览和应用模型变更，并尽量只重建或标记真正受影响的下游。

## 2. 核心概念 / 术语表

- **Model**：SQLMesh 项目的节点定义，包括 SQL model、Python model、Seed model 和 External model 等。
- **External model**：由 `external_models.yaml` 或 `external_models/*.yaml` 加载的项目外部表声明；它是 symbolic model，不渲染 SQL 查询，也不改变为 SQL model。
- **Snapshot**：模型的不可变版本，使用 fingerprint / data hash / metadata hash 感知模型内容和依赖变化。
- **PlanBuilder**：`sqlmesh/core/plan/builder.py` 中的计划构建器，负责从 `ContextDiff` 计算 direct / indirect modified snapshots、分类变更并准备 plan。
- **Affected root columns**：directly modified root 的受影响输出列集合；`None` 表示未知或语义变化，需要保守传播到全部下游；`set[str]` 表示可进入列级传播。
- **Column-level downstream impact**：PlanBuilder 在已知受影响列时复用 SQL lineage，只把实际依赖这些列的 SQL 下游纳入 indirect modified；无法证明时回退全量传播。

## 3. 子系统 / 模块索引

- **Model loading / definition**：`sqlmesh/core/loader.py` 加载 external model YAML；`sqlmesh/core/model/definition.py` 定义 `ExternalModel` 及 `columns_to_types` / data hash 输入。
- **Snapshot versioning**：`sqlmesh/core/snapshot/definition.py` 持有 snapshot fingerprint，并通过 parent data hash 让下游感知父节点数据变化。
- **Plan building**：`sqlmesh/core/plan/builder.py` 是 direct / indirect modified 传播 owner；其中 `_directly_modified_output_columns` 决定 direct root 是否能进入列级传播，`_add_column_level_downstream` / `_downstream_columns_impacted_by_parent` 执行下游 BFS 与 SQL lineage 判断。
- **Plan regression tests**：`tests/core/test_plan.py` 覆盖 SQL model 和 external model 的列级下游影响场景。

## 4. 关键架构决定

- Plan 的下游影响收窄逻辑归属 `PlanBuilder`，不下沉到 loader、YAML diff 或 model 定义层。
- External model 仍保持非 SQL / symbolic 身份；即使它有 `columns_to_types`，也不伪装成可 render query 的 SQL model。
- External model 的 schema-only direct change 可以转成 affected root columns，并复用既有 SQL lineage 下游传播；direct external change 的 fingerprint 和 breaking/non-breaking 分类语义不在这里改写。

## 5. 已知约束 / 硬边界

- 列级传播只能在“已知受影响列集合”时收窄；未知、非 schema-only、非 SQL lineage、render/normalize 异常等情况必须回退 `None`，宁可 false positive 也不能 false negative。
- External model schema diff 使用已加载 model 的 `columns_to_types`，不在 plan 阶段读取 raw `external_models.yaml` 文本 diff，也不查询 warehouse 真实 schema。
- External schema-only 判断必须把非列级 data-hash 输入（如 `stamp`、`gateway`、`physical_schema`、`physical_version`、`physical_properties` 等）纳入安全边界；这些变化不能被误判为仅列变更。
- 列顺序变化当前无法表达为“只影响 star/order-sensitive consumer”，因此保守标记所有列受影响。
