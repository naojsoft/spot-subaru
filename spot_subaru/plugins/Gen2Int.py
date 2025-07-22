"""
Gen2Int.py -- Gen2 interface for SPOT

Requirements
============

naojsoft packages
-----------------
- g2cam

"""
import pandas as pd

# ginga
from ginga import GingaPlugin

# g2cam
from g2base.remoteObjects import remoteObjects as ro


class Gen2Int(GingaPlugin.GlobalPlugin):

    def __init__(self, fv):
        super().__init__(fv)

        # get SubaruOCS preferences
        prefs = self.fv.get_preferences()
        self.settings = prefs.create_category('plugin_Gen2Int')
        self.settings.add_defaults(gen2host='localhost')
        self.settings.load(onError='silent')

    def close(self):
        self.fv.stop_global_plugin(str(self))
        return True

    def sync_targets(self, w, channel):
        try:
            integgui2_proxy = ro.remoteObjectProxy('integgui0')

            tgt_info = integgui2_proxy.get_target_info()

        except Exception as e:
            errmsg = f"error fetching target info: {e}"
            self.logger.error(errmsg, exc_info=True)
            self.fv.show_error(errmsg)
            return

        # separate by filename
        tgt_dct = dict()
        for dct in tgt_info:
            filename = dct['filename']
            tgt_list = tgt_dct.setdefault(filename, [])
            # NOTE: alternative is 'tgtname'
            tgt_list.append((dct['objname'], dct['ra'], dct['dec'],
                             dct['eq'], dct['is_referenced']))

        obj = channel.opmon.get_plugin('Targets')

        for filename, tgt_list in tgt_dct.items():
            tgt_df = pd.DataFrame(columns=['Name', 'RA', 'DEC', 'Equinox',
                                           'IsRef'],
                                  data=tgt_list)

            obj.add_targets(filename, tgt_df, merge=True)

    def start(self):
        gen2host = self.settings.get('gen2host', 'localhost')
        self.logger.info(f"Gen2 host is '{gen2host}'")

        ro.init([gen2host])

    def stop(self):
        pass

    def __str__(self):
        return 'gen2int'
