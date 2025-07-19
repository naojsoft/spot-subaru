"""
LTCS.py -- Laser Tracking Control System plugin

Plugin Type: Local
==================

``LTCS`` is a local plugin, which means it is associated with a channel.
An instance can be opened for each channel.

Usage
=====
``LTCS`` is normally used in conjunction with the plugin ``PolarSky``.

Requirements
============

naojsoft packages
-----------------
- ginga
"""
# stdlib
import os

# ginga
from ginga.gw import Widgets
from ginga import GingaPlugin

from spot_subaru.util import ltcs


class LTCS(GingaPlugin.LocalPlugin):
    """TODO
    """
    def __init__(self, fv, fitsimage):
        super().__init__(fv, fitsimage)

        # get preferences
        prefs = self.fv.get_preferences()
        self.settings = prefs.create_category('plugin_LTCS')
        self.settings.add_defaults(update_interval=1.0,
                                   ltcs_db_cfg_path=None)
        self.settings.load(onError='silent')

        # these are set in callbacks
        self.site_obj = None
        self.dt_utc = None
        self.cur_tz = None
        self.visplot = None

        self._last_update_dt = None
        ltcs_db_cfg_path = self.settings.get('ltcs_db_cfg_path', None)
        if ltcs_db_cfg_path is None:
            ltcs_db_cfg_path = os.path.join(os.environ['CONFHOME'],
                                            'lgs', 'ltcs.yml')

        self.collisions = ltcs.Collisions(self.logger, ltcs_db_cfg_path)

        self.gui_up = False

    def build_gui(self, container):
        if not self.chname.endswith('_TGTS'):
            raise Exception(f"This plugin is not designed to run in channel {self.chname}")

        obj = self.channel.opmon.get_plugin('SiteSelector')
        self.site_obj = obj.get_site()
        self.dt_utc, self.cur_tz = obj.get_datetime()
        obj.cb.add_callback('site-changed', self.site_changed_cb)
        obj.cb.add_callback('time-changed', self.time_changed_cb)

        self.visplot = self.channel.opmon.get_plugin('Visibility')

        top = Widgets.VBox()
        top.set_border_width(4)

        fr = Widgets.Frame("Collisions / LTCS")

        captions = (("Collision status:", 'llabel', 'col_status', 'llabel'),
                    ("col_time_label", 'llabel', 'col_time_countdown', 'llabel'),
                    )
        w, b = Widgets.build_info(captions)
        self.w = b
        b.col_time_label.set_text("Time left:")
        b.col_time_countdown.set_text("")
        b.col_status.set_text("")

        fr.set_widget(w)
        top.add_widget(fr)

        btns = Widgets.HBox()
        btns.set_border_width(4)
        btns.set_spacing(3)

        btn = Widgets.Button("Close")
        btn.add_callback('activated', lambda w: self.close())
        btns.add_widget(btn, stretch=0)
        btn = Widgets.Button("Help")
        btn.add_callback('activated', lambda w: self.help())
        btns.add_widget(btn, stretch=0)
        btns.add_widget(Widgets.Label(''), stretch=1)

        top.add_widget(btns, stretch=0)

        container.add_widget(top, stretch=1)
        self.gui_up = True

    def close(self):
        self.fv.stop_local_plugin(self.chname, str(self))
        return True

    def help(self):
        name = str(self).upper()
        self.fv.help_text(name, self.__doc__, trim_pfx=4)

    def start(self):
        self.collisions.connect_db()

    def stop(self):
        self.visplot.set_collisions(None)
        self.collisions.disconnect_db()
        self.gui_up = False

    def redo(self):
        pass

    def check_collisions(self):
        self.fv.assert_nongui_thread()
        #self.collisions.update(self.dt_utc.astimezone(self.cur_tz))
        self.collisions.update(self.dt_utc)

        if self.gui_up:
            self.fv.gui_do(self.update_coll_window_status)

    def update_coll_window_status(self):
        status = self.collisions.get_status()
        if status is not None:
            self.logger.info("updating visibility plot")
            self.visplot.set_collisions(status.ltcs_collisions)

            reason = status.collisions_str
            time_remain = status.remain_str
            if not status.ok_collisions:
                self.w.col_status.set_text(f'CLOSED: {reason}')
                self.w.col_time_countdown.set_text(f'{time_remain} until opening')
            else:
                self.w.col_status.set_text(f'OPEN: {reason}')
                self.w.col_time_countdown.set_text(f'{time_remain} until closing')
        else:
            self.w.col_status.set_text('ERROR')
            self.w.col_time_countdown.set_text('')

    def site_changed_cb(self, cb, site_obj):
        self.logger.debug("site has changed")
        self.site_obj = site_obj
        obj = self.channel.opmon.get_plugin('SiteSelector')
        self.dt_utc, self.cur_tz = obj.get_datetime()

    def time_changed_cb(self, cb, time_utc, cur_tz):
        old_dt_utc = self.dt_utc
        self.dt_utc = time_utc
        self.cur_tz = cur_tz

        if (self._last_update_dt is None or
            abs((self.dt_utc - self._last_update_dt).total_seconds()) >
            self.settings.get('update_interval')):
            self._last_update_dt = time_utc
            # calculate LTCS window status
            if self.gui_up:
                self.fv.nongui_do(self.check_collisions)

    def __str__(self):
        return 'ltcs'
