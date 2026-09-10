"""DIET 风格双任务 NLU 模型（Dual Intent and Entity Transformer）

参考 Rasa DIET 架构的自研轻量实现：
  char embedding → TransformerEncoder(2层, 4头) →
    ├── 意图头: mask mean-pooling → Linear → 意图 softmax
    └── 槽位头: 逐 token Linear → 各槽位类型 sigmoid（识别句子中提到了哪类槽位）
双任务联合训练，损失 = intent CE + slot BCE。
"""
import json

import numpy as np
import torch
import torch.nn as nn

PAD, UNK = "<pad>", "<unk>"


class DIETModel(nn.Module):
    def __init__(self, vocab_size: int, n_intents: int, n_slots: int,
                 embed_dim: int = 64, n_heads: int = 4, n_layers: int = 2, max_len: int = 24,
                 dropout: float = 0.1):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.pos = nn.Parameter(torch.zeros(1, max_len, embed_dim))
        layer = nn.TransformerEncoderLayer(
            d_model=embed_dim, nhead=n_heads, dim_feedforward=embed_dim * 2,
            batch_first=True, dropout=dropout)
        self.encoder = nn.TransformerEncoder(layer, num_layers=n_layers)
        self.intent_head = nn.Linear(embed_dim, n_intents)
        self.slot_head = nn.Linear(embed_dim, n_slots)

    def forward(self, ids: torch.Tensor):
        mask = (ids == 0)  # True = padding
        x = self.embed(ids) + self.pos[:, : ids.size(1)]
        h = self.encoder(x, src_key_padding_mask=mask)
        valid = (~mask).float().unsqueeze(-1)
        pooled = (h * valid).sum(1) / valid.sum(1).clamp(min=1)
        return self.intent_head(pooled), self.slot_head(h), h


class DietVectorizer:
    """字符级向量化 + 实体 span 对齐"""

    def __init__(self, max_len: int = 24):
        self.max_len = max_len
        self.vocab = {PAD: 0, UNK: 1}

    def fit(self, texts: list[str]):
        for t in texts:
            for ch in t:
                if ch not in self.vocab:
                    self.vocab[ch] = len(self.vocab)
        return self

    def encode(self, text: str) -> list[int]:
        ids = [self.vocab.get(ch, 1) for ch in text[: self.max_len]]
        return ids + [0] * (self.max_len - len(ids))

    def save_dict(self) -> dict:
        return {"max_len": self.max_len, "vocab": self.vocab}

    @classmethod
    def load_dict(cls, d: dict) -> "DietVectorizer":
        v = cls(d["max_len"])
        v.vocab = d["vocab"]
        return v


def encode_batch(vz: DietVectorizer, texts: list[str]) -> torch.Tensor:
    return torch.tensor([vz.encode(t) for t in texts], dtype=torch.long)


def build_label_maps(data: dict):
    intents = data["intents"]
    slots = data["slot_types"]
    return {i: idx for idx, i in enumerate(intents)}, {s: idx for idx, s in enumerate(slots)}


def make_targets(examples: list[dict], text2ent: dict, intent2idx: dict, slot2idx: dict,
                 vz: DietVectorizer, n_slots: int):
    """意图标签 + 逐 token 槽位标签（token 落在实体 span 内 → 该槽位置 1）"""
    intents, slot_matrix = [], []
    for ex in examples:
        intents.append(intent2idx[ex["intent"]])
        text = ex["text"]
        mat = np.zeros((vz.max_len, n_slots), dtype=np.float32)
        for ent in ex.get("entities", []):
            start = text.find(ent["value"])
            if start < 0:
                continue
            end = min(start + len(ent["value"]), vz.max_len)
            mat[start:end, slot2idx[ent["type"]]] = 1.0
        slot_matrix.append(mat)
    return torch.tensor(intents), torch.tensor(slot_matrix)


def _train_fold(train_ex: list[dict], all_texts: list[str], data: dict, hp: dict,
                seed: int, verbose: bool = False):
    """训练单个 fold，返回 (model, vectorizer, final_metrics, epoch_log)"""
    intent2idx, slot2idx = build_label_maps(data)
    n_slots = len(slot2idx)

    vz = DietVectorizer(hp.get("max_seq_len", 24)).fit(all_texts)
    model = DIETModel(
        vocab_size=len(vz.vocab), n_intents=len(intent2idx), n_slots=n_slots,
        embed_dim=hp.get("embed_dim", 64), n_heads=hp.get("n_heads", 4),
        n_layers=hp.get("n_layers", 2), max_len=hp.get("max_seq_len", 24),
        dropout=hp.get("dropout", 0.1))

    X = encode_batch(vz, [e["text"] for e in train_ex])
    y_intent, y_slot = make_targets(train_ex, {}, intent2idx, slot2idx, vz, n_slots)

    opt = torch.optim.AdamW(model.parameters(), lr=hp.get("lr", 2e-3),
                            weight_decay=hp.get("weight_decay", 0.01))
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=hp.get("epochs", 40))
    ce = nn.CrossEntropyLoss()
    bce = nn.BCEWithLogitsLoss()
    slot_w = hp.get("slot_loss_weight", 3.0)  # 槽位损失权重（实体 token 占比低，需放大）

    epochs, bs = hp.get("epochs", 40), hp.get("batch_size", 16)
    epoch_log = []
    for ep in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        perm = torch.randperm(len(train_ex), generator=torch.Generator().manual_seed(seed + ep))
        for i in range(0, len(train_ex), bs):
            idx = perm[i:i + bs]
            opt.zero_grad()
            logits_i, logits_s, _ = model(X[idx])
            valid = (X[idx] != 0)  # 只在有效 token 上计算槽位损失
            loss = ce(logits_i, y_intent[idx]) + slot_w * bce(logits_s[valid], y_slot[idx][valid])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            total_loss += loss.item() * len(idx)
        sched.step()
        entry = {"epoch": ep, "loss": round(total_loss / len(train_ex), 4)}
        epoch_log.append(entry)
    return model, vz, epoch_log


def train_diet(data_path: str, model_path: str, log_path: str, hp: dict, seed: int = 42,
               n_folds: int = 5):
    """训练入口：5 折交叉验证产出稳定指标（意图准确率 / macro-F1 / 槽位 F1），
    最终模型用全量语料训练保存。"""
    torch.manual_seed(seed)
    np.random.seed(seed)
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    intent2idx, slot2idx = build_label_maps(data)
    idx2intent = {v: k for k, v in intent2idx.items()}
    n_slots = len(slot2idx)
    examples = data["examples"]
    all_texts = [e["text"] for e in examples]

    # ---- 5 折交叉验证（指标稳定，避免单次划分的偶然性）----
    order = np.random.RandomState(seed).permutation(len(examples))
    folds = np.array_split(order, n_folds)
    cv_results = []
    for f in range(n_folds):
        dev_idx = folds[f]
        train_idx = np.concatenate([folds[j] for j in range(n_folds) if j != f])
        dev_ex = [examples[i] for i in dev_idx]
        train_ex = [examples[i] for i in train_idx]
        torch.manual_seed(seed + f)
        model, vz, _ = _train_fold(train_ex, all_texts, data, hp, seed + f)
        m = evaluate_diet(model, vz, dev_ex, intent2idx, slot2idx, n_slots)
        m["per_intent"] = per_intent_metrics(model, vz, dev_ex, intent2idx, slot2idx, n_slots, idx2intent)
        cv_results.append(m)
        print(f"fold {f + 1}/{n_folds}  n_dev={len(dev_ex)}  "
              f"intent_acc={m['intent_accuracy']:.4f}  macro_f1={m['intent_macro_f1']:.4f}  "
              f"slot_f1={m['slot_f1']:.4f}")

    mean = {
        "intent_accuracy": round(float(np.mean([r["intent_accuracy"] for r in cv_results])), 4),
        "intent_accuracy_std": round(float(np.std([r["intent_accuracy"] for r in cv_results])), 4),
        "intent_macro_f1": round(float(np.mean([r["intent_macro_f1"] for r in cv_results])), 4),
        "intent_macro_f1_std": round(float(np.std([r["intent_macro_f1"] for r in cv_results])), 4),
        "slot_f1": round(float(np.mean([r["slot_f1"] for r in cv_results])), 4),
        "slot_f1_std": round(float(np.std([r["slot_f1"] for r in cv_results])), 4),
    }

    # ---- 最终模型：全量语料训练 ----
    model, vz, epoch_log = _train_fold(examples, all_texts, data, hp, seed)
    torch.save({"state_dict": model.state_dict(),
                "vectorizer": vz.save_dict(),
                "config": {k: hp.get(k) for k in ("embed_dim", "n_heads", "n_layers", "max_seq_len", "dropout")},
                "intent2idx": intent2idx, "slot2idx": slot2idx}, model_path)

    # per-intent 汇总（各 fold 平均）
    per_intent_agg = {}
    for name in data["intents"]:
        vals = [r["per_intent"].get(name, {"precision": 0, "recall": 0, "f1": 0}) for r in cv_results]
        per_intent_agg[name] = {k: round(float(np.mean([v[k] for v in vals])), 4) for k in ("precision", "recall", "f1")}

    log = {"config": hp, "n_examples": len(examples),
           "intents": data["intents"],
           "cv": {"n_folds": n_folds, "folds": cv_results, "mean": mean},
           "final_metrics": {**mean, "per_intent": per_intent_agg},
           "final_train_epoch_log": epoch_log[-3:]}
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    print(f"\n5 折交叉验证 · 意图准确率={mean['intent_accuracy']:.4f}±{mean['intent_accuracy_std']:.4f}  "
          f"macro-F1={mean['intent_macro_f1']:.4f}±{mean['intent_macro_f1_std']:.4f}  "
          f"槽位F1={mean['slot_f1']:.4f}±{mean['slot_f1_std']:.4f}")
    print(f"最终模型（全量语料训练）已保存: {model_path}\n训练日志: {log_path}")
    return log


@torch.no_grad()
def evaluate_diet(model, vz, examples, intent2idx, slot2idx, n_slots) -> dict:
    """意图准确率 / macro-F1 + 槽位类型级 F1（token 级均值）"""
    model.eval()
    X = encode_batch(vz, [e["text"] for e in examples])
    logits_i, logits_s, _ = model(X)
    pred = logits_i.argmax(-1).tolist()
    gold = [intent2idx[e["intent"]] for e in examples]

    acc = float(np.mean(np.array(pred) == np.array(gold)))
    f1s = []
    for c in range(len(intent2idx)):
        tp = sum(1 for p, g in zip(pred, gold) if p == c and g == c)
        fp = sum(1 for p, g in zip(pred, gold) if p == c and g != c)
        fn = sum(1 for p, g in zip(pred, gold) if p != c and g == c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    macro_f1 = float(np.mean(f1s))

    # 槽位：token 级二分类 F1（按槽位类型平均）
    slot_probs = torch.sigmoid(logits_s).numpy()
    slot_f1s = []
    for s_idx in range(n_slots):
        tp = fp = fn = 0
        for k, ex in enumerate(examples):
            text = ex["text"]
            gold_mask = np.zeros(len(text), dtype=bool)
            for ent in ex.get("entities", []):
                st = text.find(ent["value"])
                if st >= 0 and ent["type"] == list(slot2idx)[s_idx]:
                    gold_mask[st:st + len(ent["value"])] = True
            pred_mask = slot_probs[k, : len(text), s_idx] > 0.5
            tp += int((gold_mask & pred_mask).sum())
            fp += int((~gold_mask & pred_mask).sum())
            fn += int((gold_mask & ~pred_mask).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        slot_f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return {"intent_accuracy": round(acc, 4), "intent_macro_f1": round(macro_f1, 4),
            "slot_f1": round(float(np.mean(slot_f1s)), 4)}


@torch.no_grad()
def per_intent_metrics(model, vz, examples, intent2idx, slot2idx, n_slots, idx2intent) -> dict:
    model.eval()
    X = encode_batch(vz, [e["text"] for e in examples])
    pred = model(X)[0].argmax(-1).tolist()
    gold = [intent2idx[e["intent"]] for e in examples]
    out = {}
    for c, name in idx2intent.items():
        tp = sum(1 for p, g in zip(pred, gold) if p == c and g == c)
        fp = sum(1 for p, g in zip(pred, gold) if p == c and g != c)
        fn = sum(1 for p, g in zip(pred, gold) if p != c and g == c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[name] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}
    return out


class DietPredictor:
    """推理封装：意图 + 槽位类型检测"""

    def __init__(self, model_path: str):
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        self.vz = DietVectorizer.load_dict(ckpt["vectorizer"])
        self.intent2idx = ckpt["intent2idx"]
        self.slot2idx = ckpt["slot2idx"]
        self.idx2slot = {v: k for k, v in self.slot2idx.items()}
        self.idx2intent = {v: k for k, v in self.intent2idx.items()}
        mcfg = ckpt.get("config", {})
        self.model = DIETModel(
            vocab_size=len(self.vz.vocab), n_intents=len(self.intent2idx),
            n_slots=len(self.slot2idx),
            embed_dim=mcfg.get("embed_dim", 64), n_heads=mcfg.get("n_heads", 4),
            n_layers=mcfg.get("n_layers", 2), max_len=self.vz.max_len,
            dropout=mcfg.get("dropout", 0.1))
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()

    @torch.no_grad()
    def predict(self, text: str, threshold: float = 0.5) -> dict:
        X = torch.tensor([self.vz.encode(text)])
        logits_i, logits_s, _ = self.model(X)
        probs = torch.softmax(logits_i, -1)[0]
        intent = self.idx2intent[int(probs.argmax())]
        slot_probs = torch.sigmoid(logits_s)[0].numpy()
        slots = {}
        for s_idx, s_name in self.idx2slot.items():
            token_p = slot_probs[: len(text), s_idx]
            if float(token_p.max()) > threshold:
                # 取连续高分行段作为槽位 span，交由上层做词典对齐
                spans, cur = [], []
                for i, p in enumerate(token_p):
                    if p > threshold:
                        cur.append(i)
                    elif cur:
                        spans.append(cur)
                        cur = []
                if cur:
                    spans.append(cur)
                best = max(spans, key=lambda s: float(token_p[s].sum()))
                slots[s_name] = {"span": (best[0], best[-1] + 1), "score": round(float(token_p[best].mean()), 4)}
        return {"intent": intent, "confidence": round(float(probs.max()), 4),
                "intent_probs": {self.idx2intent[i]: round(float(p), 4) for i, p in enumerate(probs) if p > 0.01},
                "slots": slots}
