"""Hardware discovery package."""

from app.hardware.collector import collect_hardware_inventory, inventory_as_dict

__all__ = ["collect_hardware_inventory", "inventory_as_dict"]
