# External model column-level downstream impact 验收报告

> 阶段：阶段 3（验收闭环）
> 验收日期：2026-06-08
> 关联方案 doc：.cyralis/features/2026-06-08-external-model-column-impact/external-model-column-impact-design.md

## 1. 接口契约核对

对照方案第 2.1 节名词层逐一核查。

**接口示例逐项核对**：
- [x] 示例 A（`sqlmesh/core/plan/builder.py::_directly_modified_output_columns`）：old external `changed_col VARCHAR(32)` → new `changed_col VARCHAR(64)`，代码实际行为通过 `tests/core/test_plan.py::test_external_model_column_level_indirect_modification_for_varchar_length_change` 验证为只把读取 `changed_col` 的下游纳入 indirect modified，一致。
- [x] 示例 B（`sqlmesh/core/plan/builder.py::_directly_modified_output_columns`）：external columns 已知但 `stamp` / `gateway` / `physical_schema` / `physical_version` / `physical_properties` 等非列级 data-hash 输入变化，代码实际行为通过 parametrized fallback test 验证为返回保守 all-downstream，一致。

**名词层“现状 → 变化”逐项核对**：
- [x] `ExternalModel`：身份语义不变，未改 `ExternalModel.is_sql`；代码只在 PlanBuilder 中用 `new.model.kind.is_external and old.model.kind.is_external` 识别 external root。
- [x] `columns_to_types`：external schema diff 使用已加载 model 的 `columns_to_types`；`_schema_changed_columns(old_columns_to_types, new_columns_to_types)` 复用既有列名规范化逻辑。
- [x] `data_hash` 输入：helper 先剥离 columns 后比较 `_data_hash_values_no_sql`，非列级 data-hash 输入变化返回 `None`；新增测试覆盖 `stamp`、`gateway`、`physical_schema_override`、`physical_version`、known/unknown physical property。
- [x] `PlanBuilder` affected-column contract：`_directly_modified_output_columns` 仍返回 `None | set[str]`；external schema-only 返回列集合，未知/异常返回 `None`。
- [x] `_schema_changed_columns`：未改既有实现，只复用其输出作为 external affected columns 基础。

**流程图核对**（第 2.2 节开头 mermaid 图）：
- [x] A → B：PlanBuilder direct roots 调 `_directly_modified_output_columns`，落点 `builder.py:587`。
- [x] B → C/D/E/F：external 分支位于 SQL-only guard 前，落点 `builder.py:595-599`；schema-only classifier 落点 `builder.py:1217-1247`。
- [x] E：classifier 异常 / unknown / non-column change 返回 `None`，复用 all-downstream fallback。
- [x] F → I：返回 affected columns 后复用既有 `_add_column_level_downstream` / `_downstream_columns_impacted_by_parent` BFS。
- [x] C → G：非 external SQL model 继续走既有 SQL path；SQL column-level tests 通过。

## 2. 行为与决策核对

**需求摘要逐项验证**：
- [x] External schema 字段类型 / 长度 / 增删变化可证明为 schema-only 时收窄下游影响范围：新增 external tests 覆盖 type、varchar length、add、remove。
- [x] 相关字段消费者、`select *`、非 projection 依赖、未知 lineage / unsafe 场景仍保守受影响：新增 external tests 覆盖 star、where/non-projection、多跳、missing schema、non-column data change、列顺序变化。
- [x] 无关字段消费者不被 indirect modified：type / varchar / added / removed column tests 均断言稳定列消费者为 `METADATA`。

**明确不做逐项核对**：
- [x] 不把 `ExternalModel.is_sql` 改为 `True`：grep / diff 确认无相关改动。
- [x] 不新增 raw `external_models.yaml` 文本 diff：plan diff 仅 `builder.py`，未新增 YAML diff 读取逻辑。
- [x] 不新增 plan-time warehouse schema 查询：`builder.py` 未新增 engine adapter / warehouse query / information_schema 调用。
- [x] 不新增用户配置开关：`sqlmesh/core/config` 无本 feature diff。
- [x] 不修改 snapshot fingerprint schema / migration：无 snapshot persistence / migration diff。
- [x] 不要求 Python / Seed 下游列级精确过滤：实现仍只通过 existing SQL lineage 处理 SQL downstream，非 SQL unknown 路径保守。

**关键决策落地**：
- [x] D1 Canonical owner 是 `PlanBuilder`：唯一代码改动在 `sqlmesh/core/plan/builder.py`。
- [x] D2 external model 不伪装成 SQL model：未改 `ExternalModel`；external branch 在 SQL-only guard 前单独处理。
- [x] D3 只有能证明 schema-only 才返回列集合：columns 缺失、非列级 data-hash 输入变化、helper 异常均返回 `None`。
- [x] D4 列顺序变化保守：order-only 和 mixed order/type tests 均断言所有 downstream 受影响。

**编排层“现状 → 变化”逐项核对**：
- [x] `_directly_modified_output_columns` 新增 external root 分支，非 external SQL path 保持原顺序和逻辑。
- [x] classifier 返回非 `None` 时复用现有列级 BFS；返回 `None` 时行为与旧 fallback 一致。
- [x] 未改 `_downstream_columns_impacted_by_parent` 核心语义。
- [x] 未改 categorization 编排；测试通过 `change_category` 断言 indirect / metadata 行为。

**流程级约束核对**：
- [x] 错误语义：external classifier 外层 broad `try/except` 返回 `None`，不新增用户可见 plan error。
- [x] 幂等性：helper 只读 new/old snapshot/model 数据，不写状态。
- [x] 顺序约束：external branch 位于 SQL-only guard 前；SQL path 保持原顺序。
- [x] 可观测点：测试断言 `plan.indirectly_modified` 与 downstream `change_category`，未新增日志。
- [x] 安全边界：unsafe / unknown 路径均 fallback，优先避免 false negative。

**挂载点反向核对（可卸载性）**：
- [x] 挂载点 M1：`sqlmesh/core/plan/builder.py::_directly_modified_output_columns` — 实际落点为 external 分支 + `_external_model_directly_modified_output_columns` helper。
- [x] 挂载点 M2：`tests/core/test_plan.py` column-level plan tests — 新增 `_column_level_context_diff` helper 与 external model regression tests。
- [x] 反向核查（grep）：`_external_model_directly_modified_output_columns` / `kind.is_external` / `external_model_column_level` 命中仅在 `builder.py` 和 `tests/core/test_plan.py`，均在挂载点清单内。
- [x] 拔除沙盘推演：移除 `builder.py` external branch + helper 后，production code 回到旧 all-downstream fallback；删除新增 external tests 后无配置、CLI、schema、migration 残留。

## 3. 验收场景核对

- [x] **S1 External 单列类型变化**：old `changed_col INT` → new `changed_col BIGINT`；稳定列下游不 indirect，读取 changed 列下游 indirect。
  - 证据来源：`test_external_model_column_level_indirect_modification_for_type_change`
  - 结果：通过。
- [x] **S2 External 单列长度变化**：old `VARCHAR(32)` → new `VARCHAR(64)`。
  - 证据来源：`test_external_model_column_level_indirect_modification_for_varchar_length_change`
  - 结果：通过。
- [x] **S3 External 新增列**：显式读取旧稳定列不受影响；`select *` 下游受影响。
  - 证据来源：`test_external_model_column_level_indirect_modification_includes_star_downstream_for_added_column`
  - 结果：通过。
- [x] **S4 External 删除列**：读取被删列下游受影响；稳定列消费者不受影响。
  - 证据来源：`test_external_model_column_level_indirect_modification_for_removed_column`
  - 结果：通过。
- [x] **S5 非 projection 依赖**：`WHERE` 使用 changed external column 时下游保守受影响，并继续传播。
  - 证据来源：`test_external_model_column_level_indirect_modification_includes_non_projection_consumers`
  - 结果：通过。
- [x] **S6 多跳传播**：external changed column 经中间 SQL model 输出列传播到后续下游。
  - 证据来源：`test_external_model_column_level_indirect_modification_propagates_multi_hop_lineage`
  - 结果：通过。
- [x] **S7 Missing schema fallback**：old 或 new external model 缺少 columns 时 all-downstream fallback。
  - 证据来源：`test_external_model_column_level_indirect_modification_missing_schema_falls_back_to_all_downstream`
  - 结果：通过。
- [x] **S8 Non-column data change fallback**：columns 不变但 `stamp` / `gateway` / `physical_schema_override` / `physical_version` / physical properties 变化时 all-downstream fallback。
  - 证据来源：parametrized `test_external_model_column_level_indirect_modification_non_column_change_falls_back_to_all_downstream`
  - 结果：通过。
- [x] **S9 列顺序变化**：同名同类型但顺序变化时保守所有下游受影响；混合 order + type 变化同样保守。
  - 证据来源：`test_external_model_column_level_indirect_modification_column_order_change_is_conservative`、`test_external_model_column_level_indirect_modification_mixed_order_and_type_change_is_conservative`
  - 结果：通过。
- [x] **S10 SQL model 回归**：既有 SQL column-level indirect modification tests 结果不变。
  - 证据来源：focused tests 与 full `tests/core/test_plan.py`。
  - 结果：通过。

**验证命令**：
- [x] `.venv/bin/python -m pytest tests/core/test_plan.py -k 'column_level_indirect_modification or external_model_column_level_indirect_modification' -q` → `19 passed, 82 deselected`。
- [x] `.venv/bin/python -m pytest tests/core/test_plan.py -q` → `101 passed`。
- [x] `.venv/bin/ruff check sqlmesh/core/plan/builder.py tests/core/test_plan.py` → `All checks passed!`。
- [x] `git diff --check -- sqlmesh/core/plan/builder.py tests/core/test_plan.py` → 通过。

**独立代码评审 gate**：
- [x] 命中 gate：改动涉及 plan compatibility / public behavior boundary，且跨 code + tests。
- [x] 第一轮 review：无 Critical；Important 要求补 fallback 覆盖和明确 safety boundary。
- [x] 已处理：补充 non-column fallback parametrized tests；external classifier 异常保守返回 `None`。
- [x] 第二轮 review：Critical / Important / Minor 均 None，Assessment：acceptable for feature acceptance。

**前端改动必须浏览器肉眼验证**：
- [x] 无前端改动，不适用。

## 4. 术语一致性

- `ExternalModel`：代码命中保持既有 model 定义；本 feature 没有改其 `is_sql` 身份 ✓
- `external schema diff`：代码未引入该新名词为 public API，内部通过 `columns_to_types` + `_schema_changed_columns` 表达 ✓
- `affected root columns`：继续使用 `_directly_modified_output_columns` 的 `None | set[str]` contract ✓
- `column-level downstream impact`：沿用既有 PlanBuilder column-level tests 命名；新增测试名使用 `external_model_column_level_indirect_modification`，与概念一致 ✓
- 防冲突：raw YAML diff / warehouse schema query / config switch / migration grep 未发现本 feature 新增路径 ✓

## 5. 架构归并

对照方案第 4 节，已实际更新 architecture source doc：

- [x] 架构 doc：`.cyralis/architecture/ARCHITECTURE.md`
  - 归并内容：补齐 SQLMesh 项目简介、核心概念、PlanBuilder / External model / affected root columns / column-level downstream impact 术语、plan building 模块索引、关键架构决定和 safety constraints。
  - 结果：已写入 ✓
- [x] 架构 memory projection：已执行 `cyralis memory sync --kind architecture --cwd .`，输出 `projection sync complete; created: 1`。
- [x] 无更细 plan 子系统架构 doc 存在；当前归并到总入口，后续如 cs-arch backfill plan 子系统，可把相关内容迁移到 plan 子系统 doc。

## 6. requirement 回写

- [x] `requirement` 为空，且方案第 1 节明确这是内部 plan/snapshot 能力增强，不新增用户可见产品能力。
- [x] 结论：无 requirement 回写；不触发 cs-req backfill。

## 7. roadmap 回写

- [x] design frontmatter 未设置 `roadmap` / `roadmap_item`。
- [x] 结论：非 roadmap 起头，跳过 roadmap items.yaml 和主文档回写。

## 8. attention.md 候选盘点

- [ ] 有候选，等待用户决定是否通过 `cs-note` 写入：
  - 候选 1：本仓库当前 shell PATH 下 `pytest` / `python` 不可直接用；验证应优先使用 `.venv/bin/python -m pytest ...`，ruff 使用 `.venv/bin/ruff ...`。本次直接跑 `pytest` 和 `python -m pytest` 均失败，切到 `.venv/bin/python` 后通过。

## 9. 遗留

- 后续优化点（已开 issue 或加入 issue 列表）：无。
- 已知限制：本 feature 只收窄可证明 schema-only 的 external root；任何非列级 data-hash 输入变化、missing schema、helper 异常仍全下游保守传播。
- 实现阶段“顺手发现”列表：`sqlmesh/core/plan/builder.py` 与 `tests/core/test_plan.py` 仍偏大；design 第 2.5 已记录，若继续扩展 plan 分类/传播规则，建议另走 `cs-refactor` 评估是否抽出 column-impact helper 和测试模块。
