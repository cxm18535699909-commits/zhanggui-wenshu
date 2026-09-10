"""StateGraph 风格引擎：节点注册 + 条件边 + 顺序执行（与 RAG 项目 pipeline 同构）"""
from typing import Callable


class StateGraph:
    def __init__(self, state_schema: type = dict):
        self._nodes: dict[str, Callable] = {}
        self._edges: list[tuple[str, str]] = []
        self._entry: str | None = None

    def add_node(self, name: str, fn: Callable):
        self._nodes[name] = fn
        return self

    def set_entry_point(self, name: str):
        self._entry = name
        return self

    def add_edge(self, a: str, b: str):
        self._edges.append((a, b))
        return self

    def compile(self):
        # 按 add_edge 顺序串成链
        nxt = dict(self._edges)
        order, cur = [], self._entry
        while cur and cur in nxt:
            order.append(cur)
            cur = nxt[cur]
        if cur:
            order.append(cur)

        def run(state, **kwargs):
            for name in order:
                state = self._nodes[name](state, **kwargs) or state
            return state
        run.nodes = order
        return run
