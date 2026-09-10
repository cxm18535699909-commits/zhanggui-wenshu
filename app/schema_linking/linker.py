"""Schema Linking · 自然语言 → 数据库表/字段关联

规则管线：
1. 同义词精确匹配（表/字段级 synonyms 词典）
2. jieba 分词 + 词典倒排匹配
3. 编辑距离模糊兜底
输出与问句相关的 schema 子集（表 + 字段 + 命中证据），供 SQL 生成 Prompt 剪枝。
"""
import jieba

from app.schema_linking.catalog import TABLE_CATALOG

# jieba 自定义词条（防止商品名/领域词被切碎）
for _tab in TABLE_CATALOG.values():
    for _syn in _tab["synonyms"]:
        jieba.add_word(_syn)
    for _col in _tab["columns"].values():
        for _syn in _col["synonyms"]:
            jieba.add_word(_syn)


def _levenshtein(a: str, b: str) -> float:
    """归一化相似度 1 - editdist/maxlen"""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, lb + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return 1.0 - dp[lb] / max(la, lb)


class SchemaLinker:
    def __init__(self, fuzzy_threshold: float = 0.55, max_tables: int = 4):
        self.fuzzy_threshold = fuzzy_threshold
        self.max_tables = max_tables
        # 构建倒排：词 → [(table, column|None)]
        self.inverted: dict[str, list[tuple[str, str | None, float]]] = {}
        for tname, tinfo in TABLE_CATALOG.items():
            for syn in tinfo["synonyms"]:
                self.inverted.setdefault(syn, []).append((tname, None, 1.0))
            for cname, cinfo in tinfo["columns"].items():
                self.inverted.setdefault(cname, []).append((tname, cname, 0.9))
                for syn in cinfo["synonyms"]:
                    self.inverted.setdefault(syn, []).append((tname, cname, 0.95))

    def link(self, question: str) -> dict:
        """返回 {tables: {t: {zh, columns: [...], evidence: [...]}}, tokens: [...]}"""
        hits: dict[str, dict] = {}
        evidence: list[dict] = []

        def add_hit(table: str, column: str | None, term: str, score: float):
            t = hits.setdefault(table, {"zh": TABLE_CATALOG[table]["zh"],
                                        "columns": [], "evidence": []})
            if column and column not in t["columns"]:
                t["columns"].append(column)
            t["evidence"].append({"term": term, "column": column, "score": round(score, 3)})

        # 1) 词典倒排精确匹配（长词优先）
        for term in sorted(self.inverted, key=len, reverse=True):
            if term in question:
                for table, column, score in self.inverted[term]:
                    add_hit(table, column, term, score)
                    evidence.append({"term": term, "table": table, "column": column,
                                     "match": "synonym", "score": score})

        # 2) jieba 分词 + 模糊兜底（仅补词典未覆盖的词）
        tokens = [w for w in jieba.lcut(question) if len(w) >= 2]
        covered = {e["term"] for e in evidence}
        for tok in tokens:
            if tok in covered:
                continue
            best = None
            for term, refs in self.inverted.items():
                sim = _levenshtein(tok, term)
                if sim >= self.fuzzy_threshold and (best is None or sim > best[0]):
                    best = (sim, term, refs)
            if best:
                sim, term, refs = best
                for table, column, _ in refs:
                    add_hit(table, column, tok, sim * 0.8)
                    evidence.append({"term": tok, "table": table, "column": column,
                                     "match": "fuzzy", "score": round(sim * 0.8, 3)})

        # 3) 无命中 → 保留订单/商品两张核心表作为最小上下文
        if not hits:
            for t in ("orders", "products"):
                hits[t] = {"zh": TABLE_CATALOG[t]["zh"], "columns": [], "evidence": []}

        # 4) 关联表扩展：命中的表自动带上 JOIN 依赖表
        joined = dict(hits)
        join_deps = {
            "order_items": ["orders", "products"],
            "orders": ["customers", "shops"],
            "products": ["categories"],
            "inventory": ["products", "shops"],
        }
        for t in list(hits):
            for dep in join_deps.get(t, []):
                if dep not in joined:
                    joined[dep] = {"zh": TABLE_CATALOG[dep]["zh"], "columns": [], "evidence": []}

        # 5) 裁剪：最多 max_tables 张表（按证据得分排序）
        if len(joined) > self.max_tables:
            def t_score(t):
                return max((e["score"] for e in joined[t]["evidence"]), default=0.3)
            keep = sorted(joined, key=t_score, reverse=True)[: self.max_tables]
            joined = {t: joined[t] for t in keep}

        return {"tables": joined, "tokens": tokens, "evidence": evidence}


def render_schema_context(linked: dict) -> str:
    """把 Linked Schema 子集渲染成 Prompt 用的 schema 描述文本"""
    lines = []
    for tname, info in linked["tables"].items():
        cat = TABLE_CATALOG[tname]
        cols = ", ".join(f"{c}({cat['columns'][c]['zh']})" for c in info["columns"]) or "全部字段"
        lines.append(f"- {tname}【{info['zh']}】{cat['description']}；相关字段: {cols}")
    return "\n".join(lines)
