import os.path
from ginga.misc.Bunch import Bunch


# my plugins are available here
p_path = os.path.dirname(__file__)


def setup_SubaruOCS():
    spec = Bunch(path=os.path.join(p_path, 'SubaruOCS.py'),
                 module='SubaruOCS', klass='SubaruOCS',
                 ptype='global', enabled=True, start=True,
                 hidden=True, category="Planning")
    return spec


def setup_Gen2Int():
    spec = Bunch(path=os.path.join(p_path, 'Gen2Int.py'),
                 module='Gen2Int', klass='Gen2Int',
                 ptype='global', category="Planning",
                 enabled=True, start=True, hidden=True)
    return spec


def setup_RotCalc():
    spec = Bunch(path=os.path.join(p_path, 'RotCalc.py'),
                 module='RotCalc', klass='RotCalc',
                 ptype='local', category="Experimental",
                 menu="RotCalc", tab='RotCalc',
                 ch_sfx='_TGTS', enabled=False, exclusive=False)
    return spec


def setup_LTCS():
    spec = Bunch(path=os.path.join(p_path, 'LTCS.py'),
                 module='LTCS', klass='LTCS',
                 ptype='local', category="Planning",
                 menu="LTCS", tab='LTCS',
                 ch_sfx='_TGTS', enabled=False, exclusive=False)
    return spec
