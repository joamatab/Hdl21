""" 
# Hdl21 Built-In Generators Library 
"""

from copy import deepcopy
from dataclasses import asdict, replace
from typing import Optional, Tuple, Union

from . import primitives
from .primitives import MosType, MosVth, MosFamily
from .scalar import Scalar
from .generator import generator
from .module import Module
from .params import paramclass, Param
from .signal import Signal
from .instance import Instance
from .instantiable import Instantiable


@paramclass
class MosParams:
    """Mos Series-Stack Generator Parameters"""

    # These are identical to `primitives.MosParams`
    w = Param(dtype=Optional[Scalar], desc="Width in resolution units", default=None)
    l = Param(dtype=Optional[Scalar], desc="Length in resolution units", default=None)
    nf = Param(dtype=Optional[Scalar], desc="Number of parallel fingers", default=None)
    mult = Param(dtype=Optional[Scalar], desc="Multiplier", default=None)
    tp = Param(dtype=MosType, desc="MosType (Nmos/ Pmos)", default=MosType.NMOS)
    vth = Param(dtype=MosVth, desc="Threshold voltage specifier", default=MosVth.STD)
    family = Param(dtype=MosFamily, desc="Device family", default=MosFamily.NONE)
    model = Param(dtype=Optional[str], desc="Model (Name)", default=None)

    # Number of series fingers: the unique parameter
    nser = Param(dtype=int, desc="Number of series fingers", default=1)

    # def __post_init_post_parse__(self):
    #     """Value Checks"""
    #     # FIXME: re-introduce these, for the case in which the parameters are `Prefixed` and not `Literal` values.
    #     if self.w <= 0:
    #         raise ValueError(f"MosParams with invalid width {self.w}")
    #     if self.l <= 0:
    #         raise ValueError(f"MosParams with invalid length {self.l}")
    #     if self.nf <= 0:
    #         msg = f"MosParams with invalid number parallel fingers {self.nf}"
    #         raise ValueError(msg)


@generator
def Mos(params: MosParams) -> Module:
    """Mos Series-Stack Generator
    Generates a `Module` including `nser` identical series instances of unit-Mos `primitives.Mos`.
    Unit-Mos gate and bulk ports are connected in parallel."""

    # Extract the number of series fingers
    nser = params.nser
    # Extract the remaining params for the unit transistor
    unit_params = asdict(params)
    unit_params.pop("nser")
    unit_params = primitives.Mos.Params(**unit_params)
    # Create the Primitive unit-cell
    unit_xtor = primitives.Mos(unit_params)

    # Initialize our stack-module
    m = Module()
    # Copy the unit-cell ports
    for p in primitives.Mos.port_list:
        m.add(deepcopy(p))

    # Add instances, starting at the source-side
    inst = m.add(name="unit0", val=unit_xtor(s=m.s, g=m.g, b=m.b))
    for iser in range(1, nser):
        prev_inst = inst
        inst = m.add(name=f"unit{iser}", val=unit_xtor(s=prev_inst.d, g=m.g, b=m.b))
    # Finally connect the drain to the last instance
    inst.d = m.d
    # And return the module
    return m


@generator
def Nmos(params: MosParams) -> Module:
    """Nmos Generator. A thin wrapper around `hdl21.generators.Mos`"""
    return Mos(replace(params, tp=MosType.NMOS))


@generator
def Pmos(params: MosParams) -> Module:
    """Pmos Constructor. A thin wrapper around `hdl21.generators.Mos`"""
    return Mos(replace(params, tp=MosType.PMOS))


SeriesConn = Union[Signal, str]


@paramclass
class SeriesParams:
    """Series-Parallel Generator Parameters"""

    # Required
    unit = Param(dtype=Instantiable, desc="Unit cell")
    conns = Param(
        dtype=Tuple[SeriesConn, SeriesConn],
        desc="Series ports (or port-names)",
    )
    # Optional
    nser = Param(dtype=int, desc="Number in series", default=1)


@generator
def Series(params: SeriesParams) -> Module:
    """
    # Series Generator

    Arrays `params.nser` series-stacked Instances of unit-cell `params.unit`.
    The generated `Module` includes the same ports as `unit`.
    The two series-connected ports of `unit` are specified by parameter two-tuple `conns`.
    All other ports of `unit` are wired in parallel, and exposed as ports of the generated `Module`.
    """

    if params.nser < 1:
        raise ValueError(f"Invalid Series() generator with nser={params.nser}")

    unit = params.unit

    # Initialize our stack-module
    m = Module()
    # Copy the unit-cell ports
    for p in unit.ports.values():
        m.add(deepcopy(p))

    # Check for validity of the series-ports
    if isinstance(params.conns[0], str):
        ser0 = m.ports.get(params.conns[0], None)
    elif isinstance(params.conns[0], Signal):
        ser0 = params.conns[0]
    else:  # Unreachable
        raise TypeError
    if isinstance(params.conns[1], str):
        ser1 = m.ports.get(params.conns[1], None)
    elif isinstance(params.conns[1], Signal):
        ser1 = params.conns[1]
    else:  # Unreachable
        raise TypeError
    if ser0 is None or ser1 is None:
        raise ValueError(f"Series: unit does not have ports {params.conns}")

    # Extract all the parallel-connected ports, and
    par_ports = [port for port in unit.ports.values() if port.name not in params.conns]
    par_conns = {port.name: m.add(deepcopy(port)) for port in par_ports}

    for ipar in range(params.nf):
        # Add instances, starting at the `conns[0]`-side
        inst = unit(**par_conns).connect(ser0.name, ser0)
        inst = m.add(name=f"unit_{ipar}_0", val=inst)
        for iser in range(1, params.nser):
            prev_inst = inst
            inst = unit(**par_conns)
            inst.connect(ser0.name, getattr(prev_inst, ser1.name))
            inst = m.add(name=f"unit_{ipar}_{iser}", val=inst)
        # Finally connect the last series-port to the last instance
        inst.connect(ser1.name, ser1)
    # And return the module
    return m


def _seriesconns(
    unit: Module, conns: Tuple[SeriesConn, SeriesConn]
) -> Tuple[Optional[Signal], Optional[Signal]]:
    return (
        _seriesconn(unit, conns[0]),
        _seriesconn(unit, conns[1]),
    )


def _seriesconn(m: Module, conn: SeriesConn) -> Optional[Signal]:
    if isinstance(conn, Signal):
        return conn
    if isinstance(conn, str):
        return m.ports.get(conn, None)
    raise TypeError(f"Invalid Series connection {conn}")


def Wrapper(m: Module) -> Module:
    """
    # Module Wrapper Creator

    Adds a `Module` hierarchy layer around argument-`Module` `m`.
    Creates an `Instance` of `m` and clones its ports, connecting each.

    Note: `Wrapper` is generally aways more helpful when the returned `Module` is modified after the fact.
    Hence while `Wrapper` is a *function that returns a `Module`*, it is *not* an `hdl21.Generator`,
    which cache and unique-name their results.
    Callers of `Wrapper` are therefore responsible for considerations such as unique naming.
    """

    # FIXME: find this function a home with a less-confusing name!
    from .instantiable import io

    # Initialize our wrapper-module
    wrapper = Module(name=f"{m.name}Wrapper")

    # Copy the inner-cell ports
    # Note this also serves as the connections-dict to the inner instance
    wrapper_io = {p.name: wrapper.add(deepcopy(p)) for p in io(m).values()}

    # Create the inner instance
    wrapper.add(Instance(name="inner", of=m)(**wrapper_io))

    # And return the wrapper
    return wrapper
