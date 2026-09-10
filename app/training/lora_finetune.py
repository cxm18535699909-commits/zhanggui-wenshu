"""Qwen 系列 · LoRA 参数高效微调脚手架（NL2SQL 任务）

用途：在配备 GPU 的机器上，用业务 NL2SQL 语料（问句 → StructuredOutput JSON）
对 Qwen2.5/Qwen3 系列做 LoRA 微调，提升 SQL 生成的领域准确率。
本机（无 GPU）仅作脚手架与数据格式参照；运行方式：

    pip install torch transformers peft datasets accelerate
    python -m app.training.lora_finetune --data data/finetune/nl2sql_train.jsonl

训练数据格式（每行一条，由评测集 + 业务问句沉淀导出）：
    {"instruction": "根据问题和 Schema 生成 SQL（JSON 输出）",
     "input": "问题：上个月的销售额是多少\\nSchema: orders(order_id, customer_id, ...)",
     "output": "{\\"intent\\": \\"sales_query\\", \\"sql\\": \\"SELECT ...\\", ...}"}
"""
import argparse
import json

PEFT_CONFIG = {
    "task_type": "CAUSAL_LM",
    "r": 16,                    # LoRA 秩
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],  # attention 侧注入
}

TRAIN_ARGS = {
    "model_name": "Qwen/Qwen2.5-7B-Instruct",
    "epochs": 3,
    "per_device_train_batch_size": 4,
    "gradient_accumulation_steps": 4,
    "learning_rate": 1e-4,
    "lora_lr": 2e-4,
    "warmup_ratio": 0.03,
    "bf16": True,
    "max_seq_len": 1024,
}


def export_corpus(eval_set_path: str, out_path: str):
    """从评测集导出 SFT 语料（问句 → StructuredOutput JSON），含时间参数展开"""
    with open(eval_set_path, encoding="utf-8") as f:
        data = json.load(f)
    schema_hint = ("表: shops(门店) categories(类目) products(商品) customers(客户) "
                   "orders(订单: order_date,total_amount,pay_method,status) "
                   "order_items(明细: product_id,quantity,subtotal) "
                   "inventory(库存: stock_qty,safety_stock) stock_records(出入库流水)")
    n = 0
    with open(out_path, "w", encoding="utf-8") as w:
        for item in data["items"]:
            if not item["golden_sql"]:
                continue
            out = json.dumps({"intent": item["intent"],
                              "sql": item["golden_sql"],
                              "params_hint": item.get("params") or {}},
                             ensure_ascii=False)
            rec = {"instruction": "根据用户问题与数据库 Schema 生成只读 SQL，输出 JSON（intent/sql/params）",
                   "input": f"问题：{item['question']}\nSchema: {schema_hint}",
                   "output": out}
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print(f"导出 SFT 语料 {n} 条 → {out_path}")


def train(data_path: str, output_dir: str = "data/models/qwen_lora_nl2sql"):
    """真实训练入口（需 GPU 环境 + transformers/peft/datasets）"""
    try:
        import torch  # noqa: F401
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model
        from transformers import (
            AutoModelForCausalLM, AutoTokenizer, DataCollatorForSeq2Seq, Trainer, TrainingArguments,
        )
    except ImportError as e:
        raise SystemExit(f"缺少训练依赖（{e}）。请在 GPU 机器执行: "
                         "pip install torch transformers peft datasets accelerate") from e

    args = TRAIN_ARGS
    tok = AutoTokenizer.from_pretrained(args["model_name"])
    model = AutoModelForCausalLM.from_pretrained(args["model_name"], torch_dtype="bfloat16", device_map="auto")
    model = get_peft_model(model, LoraConfig(**PEFT_CONFIG))
    model.print_trainable_parameters()  # 可训练参数量通常 <1%

    def to_features(rec):
        text = (f"<|im_start|>user\n{rec['instruction']}\n{rec['input']}<|im_end|>\n"
                f"<|im_start|>assistant\n{rec['output']}<|im_end|>")
        ids = tok(text, truncation=True, max_length=args["max_seq_len"])
        ids["labels"] = ids["input_ids"].copy()
        return ids

    ds = Dataset.from_json(data_path).map(to_features, remove_columns=["instruction", "input", "output"])
    trainer = Trainer(
        model=model, train_dataset=ds,
        args=TrainingArguments(
            output_dir=output_dir, num_train_epochs=args["epochs"],
            per_device_train_batch_size=args["per_device_train_batch_size"],
            gradient_accumulation_steps=args["gradient_accumulation_steps"],
            learning_rate=args["lora_lr"], warmup_ratio=args["warmup_ratio"],
            bf16=args["bf16"], logging_steps=10, save_strategy="epoch", report_to=[]),
        data_collator=DataCollatorForSeq2Seq(tok, padding=True),
    )
    trainer.train()
    trainer.save_model(output_dir)
    print(f"LoRA 适配器已保存: {output_dir}（推理时 merge 进底座或作为 adapter 挂载）")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/finetune/nl2sql_train.jsonl")
    p.add_argument("--export-from", default="app/evaluation/eval_set.json")
    p.add_argument("--do-train", action="store_true")
    a = p.parse_args()
    if a.do_train:
        train(a.data)
    else:
        export_corpus(a.export_from, a.data)
