from enum import Enum, auto
import copy


class ThermoMode(Enum):
    ON = auto()
    OFF = auto()


class State:
    temp = 0.0
    total_time = 0.0
    cycle_time = 0.0
    thermo_mode: ThermoMode = ThermoMode.ON

    def __init__(self, temp, total_time, cycle_time, thermo_mode: ThermoMode):
        pass


def controller(ego: State):
    output = copy.deepcopy(ego)
    if ego.thermo_mode == ThermoMode.ON:
        if ego.cycle_time >= 1.0 and ego.cycle_time < 1.1:
            output.thermo_mode = ThermoMode.OFF
            output.cycle_time = 0.0
    if ego.thermo_mode == ThermoMode.OFF:
        if ego.cycle_time >= 1.0 and ego.cycle_time < 1.1:
            output.thermo_mode = ThermoMode.ON
            output.cycle_time = 0.0
    return output
