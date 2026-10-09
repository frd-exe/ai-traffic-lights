"""Headless controllers implementing backend.contract.interfaces.Controller."""
from .fixed import FixedController
from .webster import WebsterController

__all__ = ["FixedController", "WebsterController"]
