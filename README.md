# 掌柜问数 · NL2SQL 智能数据分析系统

面向业务人员的自然语言数据查询系统：**用户意图识别（DIET）→ Schema Linking → SQL 生成（LLM/规则双引擎）→ SQL 安全校验执行 → 结果结构化返回**，支持销售额、库存、客户、订单等多维自然语言查询与拒答（幻觉抑制）。

## 实测指标（本机可复现）

| 指标 | 数值 | 来源 |
|---|---|---|
| SQL 执行准确率（v1 单模板基线） | **37.7%**（23/61） | `python -m app.cli eval` |
| SQL 执行准确率（v2 全链路） | **100%**（61/61） | 同上 |
| 提升幅度 | **+62.3 个百分点** | 报告 `improvement` 字段 |
| 意图识别准确率（61 条评测集） | **100%**（61/61） | 同上 |
| 意图识别准确率（5 折交叉验证） | **82.8% ± 6.3%**，macro-F1 **0.770** | `python -m app.cli train-nlu` |
| 槽位识别 F1（token 级） | 50.0% ± 5.2% | 同上 |
| 评测集规模 | 61 条标注 query（53 条含 golden SQL + 8 条拒答样本） | `app/evaluation/eval_set.json` |
| 数据层规模 | 8 张业务表，20287 行（订单 5686 / 明细 14201 / 客户 120 / 商品 27 等） | `python -m app.cli build-db` |

> ⚠️ **复现前提：建库与评测必须同日完成。**
> `app/db/seed.py` 用 `datetime.now()` 生成「近 180 天」订单，评测集中 **22 条** query
> 使用相对时间预设（`month_to_date`、`last_7d`、`last_month`、`today` 等），
> 由 `app/evaluation/metrics.py` 按 `date.today()` 解析为具体日期区间。
> 因此正确顺序是 `build-db` → 紧接着 `eval`。
> 若建库后隔月再评测，「本月」类查询会落在无数据区间而失败。
> 这也是 `data/db/*.db` 与 `data/logs/` 不纳入版本控制的原因（见 `.gitignore`）：
> 提交快照会让仓库随时间失效。

> **两个意图准确率数字口径不同，不要混用**：82.8% 是 163 条训练语料上的 5 折交叉验证结果
> （衡量模型泛化能力，含 chitchat 等难分类），100% 是 61 条精选评测集上的端到端结果
> （衡量全链路在目标场景的可用性）。面试被追问时应说明这一区别。

## 架构

```
┌────────────────────── 离线 ──────────────────────┐
│ nlu_training.json(163条) → DIET 双任务训练        │
│   char-embed → TransformerEncoder(2层/4头)        │
│   ├ 意图头: mean-pool → 9 类意图 softmax          │
│   └ 槽位头: 逐 token sigmoid（7 类槽位）          │
│ db/seed.py → SQLite 建库（8 张表, 5686 订单）     │
└───────────────────────────────────────────────────┘
┌────────────────── 在线问答链路 ──────────────────┐
│ ask → ① DietPredictor 意图+槽位                   │
│      ② SchemaLinker 同义词倒排+编辑距离→schema子集│
│      ③ extract_slots 时间/指标/维度/实体词典对齐   │
│      ④ LLM(Prompt模板+JSON Output) / 规则模板引擎  │
│      ⑤ Pydantic 校验 + SQL 只读白名单 + EXPLAIN    │
│      ⑥ 执行 → 结构化结果 + 摘要 + 图表建议         │
└───────────────────────────────────────────────────┘
```

## 快速开始

```bash
pip install -r requirements.txt

# 1. 建库（SQLite；MySQL DDL 见 app/db/ddl_mysql.sql，config 中 backend 切换）
python -m app.cli build-db

# 2. 训练 DIET 意图识别模型（5 折交叉验证 + 全量重训，产出训练日志）
python -m app.cli train-nlu

# 3. 端到端问答
python -m app.cli ask -q "上个月的销售额是多少"
python -m app.cli ask -q "龙井茶还有多少库存"

# 4. 评测（baseline v1 vs optimized v2 对比报告）
python -m app.cli eval

# 5. 启动 Web 服务（http://127.0.0.1:8001）
python -m app.cli serve
```

## 目录结构

```
app/
├── config.py                  # 配置加载
├── cli.py                     # build-db / train-nlu / ask / eval / serve
├── db/
│   ├── schema.py              # SQLAlchemy ORM（8 张业务表，SQLite/MySQL 双后端）
│   ├── ddl_mysql.sql          # MySQL 8.0 建表 DDL
│   └── seed.py                # 模拟数据生成（近 180 天订单；随机种子固定 42，
│                              #   日期基准取运行当天，故需建库后立即评测）
├── nlu/
│   ├── diet_model.py          # DIET 风格双任务模型（intent + slot 联合训练/推理）
│   ├── nlu_training.json      # 163 条标注语料（9 类意图 / 7 类槽位）
│   └── slots.py               # 时间/指标/维度归一化 + 实体词典对齐
├── schema_linking/
│   ├── catalog.py             # 表/字段中文语义目录 + 业务别名词典
│   └── linker.py              # 倒排精确匹配 → 编辑距离模糊 → 关联表扩展与裁剪
├── generation/
│   ├── prompts.py             # 4 套可复用 Prompt 模板（按意图族路由）+ System 约束
│   ├── sql_gen.py             # LLM 引擎（OpenAI 兼容 + JSON Structured Output）/ 规则模板引擎
│   └── validator.py           # Pydantic StructuredOutput + SQL 只读白名单校验
├── executor/runner.py         # EXPLAIN 预检 + 参数化执行 + 结果结构化/摘要
├── pipeline/nodes.py          # 全链路编排（含 baseline 模式供评测对比）
├── evaluation/
│   ├── eval_set.json          # 61 条标注评测集（含 golden SQL 与时间预设）
│   └── metrics.py             # SQL 执行准确率（结果集等价）+ 意图准确率
├── training/lora_finetune.py  # Qwen LoRA 微调脚手架 + SFT 语料导出（53 条）
└── api/server.py              # FastAPI：/api/ask /api/eval /api/schema /api/stats + Web 前端（共 5 个路由）
```

## 核心实现与代码位置

| 能力 | 代码位置 |
|---|---|
| NL2SQL 核心链路 | `pipeline/nodes.py`（intent → linking → slots → gen → execute → 结构化） |
| **自研 DIET 风格双任务意图识别**（参考 Rasa DIET 架构，非依赖 Rasa） | `nlu/diet_model.py`，训练日志由 `train-nlu` 产出 |
| Schema Linking 映射规则 | `schema_linking/catalog.py` + `linker.py` |
| Qwen LoRA 微调脚手架（PEFT 配置 + SFT 语料导出） | `training/lora_finetune.py` |
| Prompt Engineering + JSON Structured Output | `generation/prompts.py` + `sql_gen.py` |
| Pydantic 输出 schema 校验 | `generation/validator.py: StructuredOutput` |
| SQL 只读白名单 + EXPLAIN 预检 | `generation/validator.py` + `executor/runner.py` |
| SQL 执行与结果结构化 | `executor/runner.py` |
| 评测集与准确率对照（baseline v1 / optimized v2） | `evaluation/`，`python -m app.cli eval` |
| 数据库双后端（SQLite / MySQL） | `db/schema.py`（SQLAlchemy）+ `db/ddl_mysql.sql` |

## 接入真实 LLM / MySQL / 微调

1. **LLM 生成引擎**：config.yaml `generation.api_base/api_key` 填入任意 OpenAI 兼容服务（如 Qwen/DeepSeek），自动启用 Prompt 模板 + `response_format: json_object`；失败自动回退规则引擎。**当前默认 `api_base` 为空，走规则模板引擎**，上述 100% 准确率即规则引擎在全链路（Schema Linking + 分意图模板 + 安全校验）下的结果。
2. **MySQL**：`pip install pymysql`，`database.backend: "mysql"` 并填入连接信息，执行 `app/db/ddl_mysql.sql`。当前默认 SQLite。
3. **LoRA 微调**：GPU 机器上 `python -m app.training.lora_finetune --do-train`（需 transformers/peft/datasets）。

> ⚠️ **关于 LoRA 微调的诚实说明**：`training/lora_finetune.py` 是**训练脚手架 + SFT 语料导出逻辑**
> （PEFT 配置、数据格式、`export_corpus` 从 53 条含 golden SQL 的评测样本导出 JSONL），
> **本项目从未实际执行过 LoRA 训练**——无 GPU 环境、`data/finetune/` 语料未导出、无 LoRA 适配器产出。
> 当前系统的准确率完全来自**规则引擎 + DIET 意图识别**，与 LoRA 无关。
> 脚手架代码可在 GPU 机器上直接运行，但请勿将「已微调」写入任何材料。

---

## 设计取舍

| 决策 | 取舍 | 理由 |
|---|---|---|
| 默认规则模板引擎而非 LLM 生成 SQL | 牺牲自然语言泛化能力 | 离线可复现 100% 准确率；LLM 路径代码已就绪，填 `api_base` 即切换，失败自动回退规则引擎 |
| 自研 DIET 风格双任务模型而非引入 Rasa | 需自实现 Transformer 编码器与双头 | 零重依赖、可讲清每个技术决策；意图+槽位联合训练对齐 DIET 范式 |
| 字符级而非词级 Embedding | 无法利用预训练词向量 | 中文业务问句短（max_len=24），字符级对未见词与专名更鲁棒，无需额外分词依赖 |
| Schema Linking 用同义词词典 + 编辑距离而非向量检索 | 词典需人工维护 | 业务字段别名有限且稳定，规则法可解释、可调；向量法对 8 张表的规模属过度设计 |
| SQL 安全校验三重防线（白名单 + EXPLAIN + Pydantic） | 增加约 60 行校验代码 | NL2SQL 直接面向业务库，只读约束与语法预检是生产必需，非可选 |
| 评测以「执行结果集等价」判定而非 SQL 文本比对 | 需实际连库执行 | 同一语义可有多种正确 SQL 写法，文本比对会误判；结果集等价（排序无关）才是业务正确性标准 |

---

## 已知局限

1. **评测结果依赖运行当天日期**：22/61 条 query 用相对时间预设，建库后隔月评测会失败（见「实测指标」节说明）
2. **规则引擎的语义上限**：意图路由与 SQL 模板基于规则，对超出 9 类意图或复杂嵌套查询无能为力；LLM 路径可解但需配置外部服务
3. **DIET 槽位 F1 仅 50%**：意图识别（82.8%）显著优于槽位识别，字符级模型对实体边界定位能力有限
4. **LoRA 微调未实际执行**：仅有脚手架，无微调产出（见上方说明）
5. **模拟数据非真实业务**：茶叶零售数据由固定种子生成，结构与真实库对齐但数值虚构
6. **无鉴权与多租户**：`/api/*` 接口裸露，仅适用于本地开发

---

## 复现步骤

```bash
cd zhanggui-wenshu
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 含 torch，首次安装较大

python -m app.cli build-db               # 建库：8 张表 / 20287 行（约 1.6 秒）
python -m app.cli train-nlu              # 训练 DIET：5 折 CV，意图 82.8%±6.3%（约 27 秒）
python -m app.cli eval                   # 期望：v1 37.7% → v2 100%，意图 100%
python -m app.cli ask -q "上个月的销售额是多少"
python -m app.cli serve                  # Web: http://127.0.0.1:8001
```

> **务必按顺序执行**：`build-db` 与 `eval` 之间不要跨天，否则相对时间类查询会因数据区间错位而失败。
> `data/db/*.db`、`data/models/*.pt`、`data/logs/` 均不纳入版本控制，
> 由上述命令在本地重建（见 `.gitignore`）。

## License

MIT
