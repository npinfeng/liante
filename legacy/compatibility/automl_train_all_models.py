"""Deprecated compatibility entry point; AutoML now searches MLP models only."""

import logging

from automl_train import main


if __name__ == "__main__":
    logging.warning("automl_train_all_models.py 已弃用；正在调用 MLP-only AutoML")
    main()
