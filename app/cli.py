"""掌柜问数 · 命令行入口

  python -m app.cli build-db      # 建库 + 灌入模拟数据
  python -m app.cli train-nlu     # 训练 DIET 意图识别模型（产出训练日志）
  python -m app.cli ask -q "..."  # 端到端问答
  python -m app.cli eval          # 评测：baseline(v1) vs optimized(v2) 对比报告
  python -m app.cli serve         # 启动 FastAPI Web 服务
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_config  # noqa: E402


def cmd_build_db(_):
    from app.db.seed import build_db
    stats = build_db()
    print("数据库构建完成：")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_train_nlu(_):
    from app.nlu.diet_model import train_diet
    cfg = load_config()
    hp = cfg.nlu
    train_diet(str(PROJECT_ROOT / hp["nlu_data"]), str(PROJECT_ROOT / hp["model_path"]),
               str(PROJECT_ROOT / hp["training_log"]), hp, seed=hp.get("seed", 42))


def cmd_ask(args):
    from app.pipeline.nodes import ask
    out = ask(args.question, load_config())
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_eval(_):
    from app.evaluation.metrics import run_eval
    base = run_eval(mode="baseline")
    opt = run_eval(mode="optimized")
    report = {"baseline_v1": base, "optimized_v2": opt,
              "improvement": {"sql_exec_accuracy": round(opt["sql_exec_accuracy"] - base["sql_exec_accuracy"], 4),
                              "intent_accuracy": round(opt["intent_accuracy"] - base["intent_accuracy"], 4)}}
    out_path = PROJECT_ROOT / "data" / "logs" / "eval_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n评测报告已保存: {out_path}")


def cmd_serve(_):
    import uvicorn
    from app.api.server import app
    cfg = load_config()
    uvicorn.run(app, host=cfg.get("api", "host", default="127.0.0.1"),
                port=cfg.get("api", "port", default=8001))


def main():
    p = argparse.ArgumentParser(prog="zhanggui-wenshu")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-db").set_defaults(fn=cmd_build_db)
    sub.add_parser("train-nlu").set_defaults(fn=cmd_train_nlu)
    ap = sub.add_parser("ask")
    ap.add_argument("-q", "--question", required=True)
    ap.set_defaults(fn=cmd_ask)
    sub.add_parser("eval").set_defaults(fn=cmd_eval)
    sub.add_parser("serve").set_defaults(fn=cmd_serve)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
