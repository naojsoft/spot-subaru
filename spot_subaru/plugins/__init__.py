import os.path
from ginga.misc.Bunch import Bunch


# my plugins are available here
p_path = os.path.dirname(__file__)


def setup_SubaruOCS():
    spec = Bunch(path=os.path.join(p_path, 'SubaruOCS.py'),
                 module='SubaruOCS', klass='SubaruOCS',
                 ptype='global', enabled=True, start=True,
                 category="Planning")
    return spec


def setup_HSCPlanner():
    spec = Bunch(module='HSCPlanner', ptype='local', workspace='channels',
                 category="Planning", menu="HSC Planner", tab='HSC Planner',
                 ch_sfx='_FIND', enabled=True, exclusive=False)
    return spec


def setup_RotCalc():
    spec = Bunch(module='RotCalc', ptype='local', workspace='channels',
                 category="Planning", menu="RotCalc", tab='RotCalc',
                 ch_sfx='_TGTS', enabled=False, exclusive=False)
    return spec
