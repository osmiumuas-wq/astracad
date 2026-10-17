from .autocad import AutoCadAdapter
from .creo import CreoAdapter
from .fusion import FusionAdapter
from .solidworks import SolidWorksAdapter

ALL = {"solidworks": SolidWorksAdapter, "creo": CreoAdapter, "fusion": FusionAdapter,
       "autocad": AutoCadAdapter}
