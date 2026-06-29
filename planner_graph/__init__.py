"""Package root for the planner service.

This file exists so the rest of the repo can import `planner_graph` as one
cohesive application package. It connects to every subsystem indirectly by
making the API, runtime, graph, storage, and node modules importable together.
"""

from .app import create_app

__all__ = ["create_app"]
"""Inputs: package imports from runtime, API, scripts, and tests.
Does: marks planner_graph as a Python package without transforming data.
Outputs: package namespace for planner_graph modules.
"""
