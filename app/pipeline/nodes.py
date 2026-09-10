"""NL2SQL 全链路编排（LangGraph 风格状态流）

intent(DIET) → schema_link → slot_extract → sql_generate → validate/execute → structured_answer
"""
from app.config import load_config
from app.executor import runner
from app.generation.sql_gen import generate_sql
from app.nlu.diet_model import DietPredictor
from app.nlu.slots import extract_slots
from app.schema_linking.catalog import load_entity_dict
from app.schema_linking.linker import SchemaLinker

PREDICTOR: DietPredictor | None = None
LINKER: SchemaLinker | None = None
ENTITY_DICT: dict | None = None


def _get_components(cfg=None):
    """懒加载单例：DIET 模型 / Schema Linker / 实体词典"""
    global PREDICTOR, LINKER, ENTITY_DICT
    cfg = cfg or load_config()
    if PREDICTOR is None:
        PREDICTOR = DietPredictor(cfg.nlu.get("model_path", "data/models/diet_model.pt"))
    if LINKER is None:
        LINKER = SchemaLinker(cfg.get("schema_linking", "fuzzy_threshold", default=0.55),
                              cfg.get("schema_linking", "max_tables_in_context", default=4))
    if ENTITY_DICT is None:
        from app.db.schema import get_session_factory
        session = get_session_factory(cfg)()
        ENTITY_DICT = load_entity_dict(session)
        session.close()
    return PREDICTOR, LINKER, ENTITY_DICT


def ask(question: str, cfg=None, use_baseline: bool = False) -> dict:
    """端到端问答。use_baseline=True 时走 v1 基线链路（无 Schema Linking、单一大通用 Prompt/模板），
    用于评测「优化前 → 优化后」对比。"""
    cfg = cfg or load_config()
    predictor, linker, entity_dict = _get_components(cfg)

    # 1. 意图识别（DIET 双任务）
    nlu_out = predictor.predict(question)
    intent = nlu_out["intent"]

    if intent == "chitchat":
        return {"question": question, "intent": intent, "confidence": nlu_out["confidence"],
                "sql": "", "engine": "-", "answer": "您好，我是掌柜问数助手，可以帮您查询销售额、库存、订单、客户等经营数据。",
                "columns": [], "rows": [], "row_count": 0, "chart": "table",
                "linked_tables": [], "slots": {}, "intent_probs": nlu_out.get("intent_probs", {})}

    # 2. Schema Linking（基线模式：跳过，使用全量 schema）
    if use_baseline:
        linked = {"tables": {}, "tokens": [], "evidence": []}
    else:
        linked = linker.link(question)

    # 3. 槽位抽取（DIET 槽位头 + 词典对齐；基线模式关闭别名映射与模糊匹配）
    if use_baseline:
        from app.schema_linking.catalog import ALIAS_MAPS
        bare_dict = {**entity_dict, "product_alias": {}, "category_alias": {}, "shop_alias": {}}
        slots = extract_slots(question, bare_dict, fuzzy_threshold=1.0)
    else:
        slots = extract_slots(question, entity_dict,
                              cfg.get("schema_linking", "fuzzy_threshold", default=0.55))

    # 4. SQL 生成（LLM 优先 / 规则回退；基线模式强制单模板规则引擎）
    if use_baseline:
        from app.generation.sql_gen import RuleEngine
        out = RuleEngine(cfg.row_limit).generate(question, intent, slots, baseline=True)
        engine = "rules-baseline"
    else:
        out, engine = generate_sql(question, intent, slots, linked, cfg)

    # 5. 执行（校验内置于执行器）
    params = out.param_map or {}
    result = runner.execute_sql(out.sql, params, cfg)

    # 6. 结果结构化 + 摘要
    answer_text = (runner.summarize(result, out.chart) if result.get("ok")
                   else f"抱歉，查询执行失败：{result.get('sql_error')}")

    return {
        "question": question,
        "intent": intent,
        "confidence": nlu_out["confidence"],
        "intent_probs": nlu_out.get("intent_probs", {}),
        "slots": {k: v for k, v in slots.items() if v},
        "linked_tables": list(linked["tables"].keys()),
        "engine": engine,
        "sql": out.sql,
        "sql_params": params,
        "chart": out.chart,
        "explanation": out.explanation,
        "ok": result.get("ok", False),
        "answer": answer_text,
        "columns": result.get("columns", []),
        "rows": result.get("rows", []),
        "row_count": result.get("row_count", 0),
    }
