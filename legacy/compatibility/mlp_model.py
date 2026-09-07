"""Deprecated import shim; use :mod:`models.mlp` in new code."""

from models.mlp import ConfigurableMLP, DynamicMLP, MLPConfig

__all__ = ["ConfigurableMLP", "DynamicMLP", "MLPConfig"]
