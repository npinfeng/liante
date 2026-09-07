"""Deprecated entry point; delegates to the MLP-only AutoML CLI."""

import logging

from automl_train import main


if __name__ == "__main__":
    logging.warning("train_mlp.py 已弃用；正在调用 automl_train.py（仅搜索 MLP）")
    main()
