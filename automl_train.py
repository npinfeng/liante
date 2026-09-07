"""CLI compatibility entry point for the MLP-only AutoML engine."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from automl.mlp_trainer import MLPAutoMLTrainer
from config import MODEL_DIR, TRAIN_DATA_DIR
from schemas.training import SearchSpace


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Liante MLP-only AutoML")
    parser.add_argument("--data-dir", type=Path, default=TRAIN_DATA_DIR)
    parser.add_argument("--save-dir", "--model-dir", dest="model_dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--channels", type=int, nargs="+", default=list(range(8)))
    parser.add_argument("--n-trials", "--trials", dest="n_trials", type=int, default=30)
    parser.add_argument("--max-epochs", "--epochs", dest="max_epochs", type=int, default=500)
    parser.add_argument("--patience", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.n_trials < 1 or args.max_epochs < 1 or args.patience < 1:
        raise SystemExit("n_trials、max_epochs 和 patience 必须大于 0")
    if any(channel < 0 or channel > 7 for channel in args.channels):
        raise SystemExit("channels 只允许 0 到 7")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    trainer = MLPAutoMLTrainer(args.data_dir, args.model_dir)
    results = []
    for channel in args.channels:
        result = trainer.train_channel(
            channel,
            n_trials=args.n_trials,
            max_epochs=args.max_epochs,
            patience=min(args.patience, args.max_epochs),
            random_seed=args.seed + channel,
            search_space=SearchSpace(),
        )
        results.append(result)

    report_path = args.model_dir / "mlp_automl_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.getLogger(__name__).info("MLP AutoML report saved=%s", report_path)


if __name__ == "__main__":
    main()
