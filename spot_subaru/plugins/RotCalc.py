"""
RotCalc.py -- Rotation check and calculator

Requirements
============

naojsoft packages
-----------------
- g2cam
- ginga
"""
from datetime import timedelta
import os

import numpy as np

from naoj.util import rot as naoj_rot

# ginga
from ginga.gw import Widgets, GwHelp
from ginga.misc import Bunch
from ginga import GingaPlugin
from ginga.util import wcs

# local
from spot.util import calcpos


default_report = os.path.join(os.path.expanduser('~'), "rot_report.csv")


class RotCalc(GingaPlugin.LocalPlugin):

    def __init__(self, fv, fitsimage):
        # superclass defines some variables for us, like logger
        super().__init__(fv, fitsimage)

        # get preferences
        prefs = self.fv.get_preferences()
        self.settings = prefs.create_category('plugin_RotCalc')
        self.settings.add_defaults(telescope_update_interval=3.0,
                                   default_report=default_report,
                                   follow_telescope=False)
        self.settings.load(onError='silent')

        self.viewer = self.fitsimage

        self.columns = [('Time', 'time'),
                        ('Name', 'name'),
                        ('RA', 'ra_str'),
                        ('DEC', 'dec_str'),
                        ('PA', 'pa_deg'),
                        # ('Rot cur', 'rot_cur_deg'),
                        ('Rot1 start', 'rot1_start_deg'),
                        ('Rot1 stop', 'rot1_stop_deg'),
                        ('Rot2 start', 'rot2_start_deg'),
                        ('Rot2 stop', 'rot2_stop_deg'),
                        # ('Min Rot Move', 'min_rot_move'),
                        # ('Max Rot Time', 'max_rot_time'),
                        ('Sugg Rot', 'rot_chosen'),
                        ('El start', 'el_start_deg'),
                        ('El stop', 'el_stop_deg'),
                        # ('Cur Az', 'az_cur_deg'),
                        ('Az1 start', 'az1_start_deg'),
                        ('Az1 stop', 'az1_stop_deg'),
                        ('Az2 start', 'az2_start_deg'),
                        ('Az2 stop', 'az2_stop_deg'),
                        # ('Alt', 'alt_deg'),
                        # ('Min Az Move', 'min_az_move'),
                        # ('Max Az Time', 'max_az_time'),
                        ('Sugg Az', 'az_chosen'),
                        ]
        self.rot_deg = 0.0
        self.rot_cmd_deg = 0.0
        self.az_deg = 0.0
        self.az_cmd_deg = 0.0
        self.pa_deg = 0.0
        self.delay_sec = 0
        self.time_sec = 15 * 60
        self.insname = 'PFS'
        self.rot_min_deg = -174.0
        self.rot_max_deg = +174.0
        self.az_min_deg = -270.0
        self.az_max_deg = +270.0
        self.tbl_dct = dict()
        self.time_str = None
        self.targets = None
        self._cur_target = None
        self._autosave = False
        # these are set via callbacks from the SiteSelector plugin
        self.site = None
        self.dt_utc = None
        self.cur_tz = None
        self.gui_up = False

    def build_gui(self, container):

        # initialize site and date/time/tz
        obj = self.channel.opmon.get_plugin('SiteSelector')
        self.site = obj.get_site()
        obj.cb.add_callback('site-changed', self.site_changed_cb)
        self.dt_utc, self.cur_tz = obj.get_datetime()
        obj.cb.add_callback('time-changed', self.time_changed_cb)
        self.targets = self.channel.opmon.get_plugin('Targets')
        self.telpos = self.channel.opmon.get_plugin('TelescopePosition')
        self.telpos.cb.add_callback('telescope-status-changed',
                                    self.telpos_changed_cb)

        top = Widgets.VBox()
        top.set_border_width(4)

        fr = Widgets.Frame("Pointing")

        captions = (('RA:', 'label', 'ra', 'llabel', 'DEC:', 'label',
                     'dec', 'llabel'),
                    ('Equinox:', 'label', 'equinox', 'llabel',
                     'Name:', 'label', 'tgt_name', 'llabel'),
                    ('Get Selected', 'button', '_sp1', 'spacer',
                     "Follow telescope", 'checkbox')
                    )

        w, b = Widgets.build_info(captions)
        self.w = b
        b.ra.set_text('')
        b.dec.set_text('')
        b.equinox.set_text('')
        b.tgt_name.set_text('')
        b.get_selected.set_tooltip("Get the coordinates from the selected target in Targets table")
        b.get_selected.add_callback('activated', self.get_selected_target_cb)
        b.follow_telescope.set_tooltip("Set pointing to telescope position")
        b.follow_telescope.set_state(self.settings['follow_telescope'])
        self.w.update(b)
        fr.set_widget(w)
        top.add_widget(fr, stretch=0)

        fr = Widgets.Frame("PA / Exp Time")

        captions = (("Calculate", 'button',
                     'Delay (sec):', 'label', 'delay', 'spinbox',
                     'PA (deg):', 'label', 'pa', 'entry',
                     'Exp time (sec):', 'label', 'secs', 'entry'),
                    )

        w, b = Widgets.build_info(captions)
        self.w.update(b)

        fr.set_widget(w)
        top.add_widget(fr, stretch=0)

        b.delay.set_limits(0, 3600, incr_value=60)
        b.delay.set_tooltip("Delay until I want to observe this target (sec)")
        b.delay.set_value(0)
        b.pa.set_text("0.00")
        b.pa.set_tooltip("Set desired Position Angle (deg)")
        b.secs.set_text("{}".format(15 * 60.0))
        b.secs.set_tooltip("Number of seconds on target")
        b.calculate.set_tooltip("Calculate rotator and azimuth choices")
        b.calculate.add_callback('activated', self.calc_rotations_cb)

        fr = Widgets.Frame("Current Rot / Az")
        captions = (('Cur Rot:', 'label', 'cur_rot', 'llabel',
                     'Cmd Rot:', 'label', 'cmd_rot', 'llabel',
                     'Cur Az:', 'label', 'cur_az', 'llabel',
                     'Cmd Az:', 'label', 'cmd_az', 'llabel'),
                    )

        w, b = Widgets.build_info(captions)
        self.w.update(b)
        fr.set_widget(w)
        top.add_widget(fr, stretch=0)

        self.w.rot_tbl = Widgets.TreeView(auto_expand=True,
                                          selection='single',
                                          sortable=True,
                                          use_alt_row_color=True)
        self.w.rot_tbl.setup_table(self.columns, 1, 'time')
        top.add_widget(self.w.rot_tbl, stretch=1)

        self.w.rot_tbl.set_optimal_column_widths()

        #top.add_widget(Widgets.Label(''), stretch=1)

        # fr = Widgets.Frame("Report")

        # captions = (('File:', 'label', 'filename', 'entry',
        #              'Save', 'button', 'Auto Save', 'checkbox'),
        #             )

        # w, b = Widgets.build_info(captions)
        # self.w.update(b)
        # b.save.add_callback('activated', self.save_report_cb)
        # b.auto_save.set_state(self._autosave)
        # b.auto_save.add_callback('activated', self.autosave_cb)
        # fr.set_widget(w)
        # top.add_widget(fr, stretch=0)

        btns = Widgets.HBox()
        btns.set_border_width(4)
        btns.set_spacing(3)

        btn = Widgets.Button("Close")
        btn.add_callback('activated', lambda w: self.close())
        btns.add_widget(btn, stretch=0)
        btn = Widgets.Button("Help")
        #btn.add_callback('activated', lambda w: self.help())
        btns.add_widget(btn, stretch=0)
        btns.add_widget(Widgets.Label(''), stretch=1)

        top.add_widget(btns, stretch=0)

        container.add_widget(top, stretch=1)
        self.gui_up = True

    def close(self):
        self.fv.stop_local_plugin(self.chname, str(self))
        return True

    def start(self):
        pass

    def stop(self):
        self.gui_up = False

    def calc_rotations_cb(self, w):
        self.w.rot_tbl.clear()
        self.tbl_dct = dict()

        self.delay_sec = float(self.w.delay.get_value())
        self.pa_deg = float(self.w.pa.get_text().strip())
        self.time_sec = float(self.w.secs.get_text().strip())
        name = self.w.tgt_name.get_text().strip()
        ra_str = self.w.ra.get_text().strip()
        dec_str = self.w.dec.get_text().strip()

        ra_deg = wcs.hmsStrToDeg(ra_str)
        dec_deg = wcs.dmsStrToDeg(dec_str)
        equinox = 2000.0
        body = calcpos.Body(name, ra_deg, dec_deg, equinox)

        start_time = self.dt_utc + timedelta(seconds=self.delay_sec)
        self.time_str = start_time.astimezone(self.cur_tz).strftime("%H:%M:%S")
        cres_start = body.calc(self.site.observer, start_time)
        cres_stop = body.calc(self.site.observer,
                              start_time + timedelta(seconds=self.time_sec))

        status = self.site.get_status()
        obs_lat_deg = status['latitude_deg']

        # CHECK POSSIBLE ROTATIONS
        res = naoj_rot.calc_possible_rotations(cres_start.pang_deg,
                                               cres_stop.pang_deg, self.pa_deg,
                                               self.insname,
                                               dec_deg, obs_lat_deg)
        rot1_start_deg, rot1_stop_deg = res[0]
        rot2_start_deg, rot2_stop_deg = res[1]

        rot_start, rot_stop = naoj_rot.calc_optimal_rotation(rot1_start_deg,
                                                             rot1_stop_deg,
                                                             rot2_start_deg,
                                                             rot2_stop_deg,
                                                             self.rot_deg,
                                                             self.rot_min_deg,
                                                             self.rot_max_deg)

        # CHECK POSSIBLE AZIMUTHS
        az_choices = naoj_rot.calc_possible_azimuths(dec_deg,
                                                     cres_start.az_deg,
                                                     cres_stop.az_deg,
                                                     obs_lat_deg)
        az1_start_deg = np.nan
        az1_stop_deg = np.nan
        az2_start_deg = np.nan
        az2_stop_deg = np.nan
        if len(az_choices) > 0:
            az1_start_deg, az1_stop_deg = az_choices[0]
        if len(az_choices) > 1:
            az2_start_deg, az2_stop_deg = az_choices[1]

        az_start, az_stop = naoj_rot.calc_optimal_rotation(az1_start_deg,
                                                           az1_stop_deg,
                                                           az2_start_deg,
                                                           az2_stop_deg,
                                                           self.az_deg,
                                                           self.az_min_deg,
                                                           self.az_max_deg)

        el_start_deg = cres_start.alt_deg
        el_stop_deg = cres_stop.alt_deg
        self.tbl_dct[self.time_str] = dict(time=self.time_str, name=name,
                                           ra_str=ra_str, dec_str=dec_str,
                                           pa_deg=("%.1f" % self.pa_deg),
                                           # rot_cur_deg=("%.1f" % self.rot_deg),
                                           rot1_start_deg=("%.1f" % rot1_start_deg),
                                           rot1_stop_deg=("%.1f" % rot1_stop_deg),
                                           rot2_start_deg=("%.1f" % rot2_start_deg),
                                           rot2_stop_deg=("%.1f" % rot2_stop_deg),
                                           # min_rot_move=("%.1f" % min_rot_move),
                                           # max_rot_time=("%.1f" % max_rot_time),
                                           rot_chosen=("%.1f" % rot_start),
                                           el_start_deg=("%.1f" % el_start_deg),
                                           el_stop_deg=("%.1f" % el_stop_deg),
                                           # az_cur_deg=("%.1f" % self.az_deg),
                                           az1_start_deg=("%.1f" % az1_start_deg),
                                           az1_stop_deg=("%.1f" % az1_stop_deg),
                                           az2_start_deg=("%.1f" % az2_start_deg),
                                           az2_stop_deg=("%.1f" % az2_stop_deg),
                                           # alt_deg=("%.1f" % alt_start_deg),
                                           # min_az_move=("%.1f" % min_az_move),
                                           # max_az_time=("%.1f" % max_az_time),
                                           az_chosen=("%.1f" % az_start),
                                           )
        self.w.rot_tbl.set_tree(self.tbl_dct)
        #self.w.record.set_enabled(True)


    def target_selection_cb(self, cb, targets):
        if len(targets) == 0:
            return
        tgt = next(iter(targets))
        if self.gui_up:
            if self.tgt_locked:
                # target is locked
                self.logger.info("target is locked")
                return
            self.w.ra.set_text(wcs.ra_deg_to_str(tgt.ra))
            self.w.dec.set_text(wcs.dec_deg_to_str(tgt.dec))
            #self.w.equinox.set_text(str(tgt.equinox))
            self.w.tgt_name.set_text(tgt.name)

    # def send_target_cb(self, w):
    #     ra_deg = wcs.hmsStrToDeg(self.w.ra.get_text())
    #     dec_deg = wcs.dmsStrToDeg(self.w.dec.get_text())
    #     ra_soss = wcs.ra_deg_to_str(ra_deg, format='%02d%02d%02d.%03d')
    #     dec_soss = wcs.dec_deg_to_str(dec_deg, format='%s%02d%02d%02d.%02d')
    #     equinox = 2000.0
    #     status_dict = {"GEN2.SPOT.RA": ra_soss,
    #                    "GEN2.SPOT.DEC": dec_soss,
    #                    "GEN2.SPOT.EQUINOX": equinox}
    #     try:
    #         obj = self.fv.gpmon.get_plugin('Gen2Int')
    #         obj.send_status(status_dict)

    #     except Exception as e:
    #         errmsg = f"Failed to send status: {e}"
    #         self.fv.show_error(errmsg)
    #         self.logger.error(errmsg, exc_info=True)

    def site_changed_cb(self, cb, site_obj):
        self.logger.debug("site has changed")
        self.site = site_obj

    def time_changed_cb(self, cb, time_utc, cur_tz):
        self.dt_utc = time_utc
        self.cur_tz = cur_tz

        obj = self.channel.opmon.get_plugin('SiteSelector')
        status = obj.get_status()

        self.update_status(status)

    def update_status(self, status):
        self.rot_deg = status.rot_deg
        self.rot_cmd_deg = status.rot_cmd_deg
        self.az_deg = status.az_deg
        self.az_cmd_deg = status.az_cmd_deg

        if self.gui_up:
            self.w.cur_az.set_text("%.2f" % self.az_deg)
            self.w.cmd_az.set_text("%.2f" % self.az_cmd_deg)
            self.w.cur_rot.set_text("%.2f" % self.rot_deg)
            self.w.cmd_rot.set_text("%.2f" % self.rot_cmd_deg)

    def telpos_changed_cb(self, cb, status, target):
        self.fv.assert_gui_thread()
        if not self.gui_up or not self.w.follow_telescope.get_state():
            return
        tel_status = status.tel_status.lower()
        self.logger.info(f"telescope status is '{tel_status}'")
        if tel_status not in ['tracking', 'guiding']:
            # don't do anything unless telescope is stably tracking/guiding
            return

        self.logger.info(f"target is {target}")
        if target is None or target is self._cur_target:
            pass
        else:
            # <-- moved to a different known target
            # set target info
            self._cur_target = target
            self.set_pointing(target.ra, target.dec, target.equinox, target.name)

    def get_selected_target_cb(self, w):
        if self.w.follow_telescope.get_state():
            # target is following telescope
            self.fv.show_error("uncheck 'Follow telescope' to get selection")
            return

        selected = self.targets.get_selected_targets()
        if len(selected) != 1:
            self.fv.show_error("Please select exactly one target in the Targets table!")
            return
        tgt = list(selected)[0]
        self.set_pointing(tgt.ra, tgt.dec, tgt.equinox, tgt.name)

    def set_pointing(self, ra_deg, dec_deg, equinox, tgt_name):
        if not self.gui_up:
            return
        self.w.ra.set_text(wcs.ra_deg_to_str(ra_deg))
        self.w.dec.set_text(wcs.dec_deg_to_str(dec_deg))
        self.w.equinox.set_text(str(equinox))
        self.w.tgt_name.set_text(tgt_name)

    # def save_report(self, filepath):
    #     if len(self.tbl_dct) == 0:
    #         return

    #     try:
    #         import pandas as pd
    #     except ImportError:
    #         self.fv.show_error("Please install 'pandas' and "
    #                            "'openpyxl' to use this feature")
    #         return

    #     try:
    #         self.logger.info("writing table: {}".format(filepath))

    #         col_hdr = [colname for colname, key in self.columns]
    #         rows = [list(d.values()) for d in self.tbl_dct.values()]
    #         df = pd.DataFrame(rows, columns=col_hdr)

    #         if filepath.endswith('.csv'):
    #             df.to_csv(filepath, index=False, header=True)

    #         else:
    #             df.to_excel(filepath, index=False, header=True)

    #     except Exception as e:
    #         self.logger.error("Error writing table: {}".format(e),
    #                           exc_info=True)

    # def save_report_cb(self, w):
    #     filepath = self.w.filename.get_text().strip()
    #     if len(filepath) == 0:
    #         filepath = self.settings.get('default_report')
    #         self.w.filename.set_text(filepath)

    #     self.save_report(filepath)

    # def autosave_cb(self, w, tf):
    #     self._autosave = tf

    def __str__(self):
        return 'rotcalc'
