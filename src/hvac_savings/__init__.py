"""hvac_savings — simulator and reproduction code for

    "HVAC Savings in Saudi Residential Buildings:
     Envelope, Setpoint, and Scheduling
     under a Volume Tariff"  (Hamzah Faraj, Taif University).

Public API:
    simulate(city, env, policy, tariff)   -- run the model (see model.py)
    CURRENT, CODE                         -- baseline / efficient control policies
    ENVELOPES, FLOOR_AREA                  -- envelope U-values, conditioned area
"""
from hvac_savings.model import simulate, CURRENT, CODE, ENVELOPES, FLOOR_AREA

__version__ = "1.0.0"
__all__ = ["simulate", "CURRENT", "CODE", "ENVELOPES", "FLOOR_AREA", "__version__"]
