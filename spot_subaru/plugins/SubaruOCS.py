"""
SubaruOCS.py -- Get status information from Subaru Telescope

Requirements
============

naojsoft packages
-----------------
- g2cam

Usage
=====
Add the appropriate plugin_SubaruOCS.cfg file to $HOME/.spot to add the
appropriate status connection authentication.
"""
# stdlib
import threading
import queue as Queue

# ginga
from ginga import GingaPlugin

# g2cam
from g2cam.status.client import StatusClient
from g2cam.status.stream import StatusStream

# local
from spot.util import sites


class SubaruOCS(GingaPlugin.GlobalPlugin):
    """
    +++++++++
    SubaruOCS
    +++++++++

    SubaruOCS makes a connection to the Subaru Telescope Observation
    Control System so that the appropriate status items can be read
    periodically.  This enables the use of the TelescopePosition plugin.

    There is no UI associated with this plugin, and it will be started
    automatically when SPOT starts.
    """

    def __init__(self, fv):
        super().__init__(fv)

        # get SubaruOCS preferences
        prefs = self.fv.get_preferences()
        self.settings = prefs.create_category('plugin_SubaruOCS')
        self.settings.add_defaults(tel_fov_deg=1.5,
                                   status_client_host=None)
        self.settings.load(onError='silent')

        # Az, Alt/El current tel position and commanded position
        self.ev_quit = threading.Event()
        self.lock = threading.RLock()
        self.status_dict = {'STATS.AZ_DEG': None, 'STATS.EL_DEG': None,
                            'STATS.AZ_ADJ': None,
                            'STATS.AZ_DIF': None, 'STATS.EL_DIF': None,
                            'STATS.SLEWING_TIME': None,
                            'STATL.TELDRIVE': None,
                            'STATS.AZ_CMD': None, 'STATS.EL_CMD': None,
                            'STATS.RA_DEG': None, 'STATS.DEC_DEG': None,
                            'STATS.RA_CMD_DEG': None, 'STATS.DEC_CMD_DEG': None,
                            'FITS.SBR.INSROT': None, 'FITS.SBR.INSROT_CMD': None,
                            }

        # maps Subaru Telescope OCS status items to SPOT status
        self.status_map = dict(
            az_deg='STATS.AZ_DEG',
            az_cmd_deg='STATS.AZ_CMD',
            az_diff_deg='STATS.AZ_DIF',
            alt_deg='STATS.EL_DEG',
            alt_cmd_deg='STATS.EL_CMD',
            alt_diff_deg='STATS.EL_DIF',
            ra_deg='STATS.RA_DEG',
            ra_cmd_deg='STATS.RA_CMD_DEG',
            equinox='STATS.EQUINOX',
            dec_deg='STATS.DEC_DEG',
            dec_cmd_deg='STATS.DEC_CMD_DEG',
            cmd_equinox='STATS.EQUINOX',
            slew_time_sec='STATS.SLEWING_TIME',
            # tel_status='STATL.TELDRIVE',
            rot_deg='FITS.SBR.INSROT',
            rot_cmd_deg='FITS.SBR.INSROT_CMD',
        )

    def close(self):
        self.fv.stop_global_plugin(str(self))
        return True

    def start(self):
        self.ev_quit.clear()

        # set up the status stream interface
        status_host = self.settings.get('status_stream_host', None)
        self.st_client = None
        self.st_stream = None
        if status_host is not None:
            try:
                # initially populate status dict
                self.st_client = StatusClient(host=self.settings['status_client_host'],
                                              username=self.settings['status_client_user'],
                                              password=self.settings['status_client_pass'])
                self.st_client.connect()
                self.st_client.fetch(self.status_dict)

                self.st_stream = StatusStream(host=self.settings['status_stream_host'],
                                              username=self.settings['status_stream_user'],
                                              password=self.settings['status_stream_pass'],
                                              logger=self.logger)
                self.st_stream.connect()
            except Exception as e:
                self.logger.error(f"failed to connect to status stream: {e}",
                                  exc_info=True)
                return

            # intermediary queue
            status_q = Queue.Queue()

            # stream producer puts status updates on the queue
            self.fv.nongui_do(self.st_stream.subscribe_loop,
                              self.ev_quit, status_q)

            # stream consumer takes them and updates the local status
            self.fv.nongui_do(self.consume_stream, self.ev_quit, status_q)

    def stop(self):
        self.ev_quit.set()

    def update_status(self, status_dict):
        """This updates our local SPOT-specific site status items with values
        read from Subaru Telescope telemetry.
        """
        with self.lock:
            self.status_dict.update(status_dict)

            dct = {key: self.status_dict[alias]
                   for key, alias in self.status_map.items()
                   if alias in self.status_dict}

            # special handling
            tel_status = str(self.status_dict['STATL.TELDRIVE']).lower()
            if tel_status.startswith('guiding'):
                tel_status = 'guiding'
            dct['tel_status'] = tel_status

        # update the site status variables
        site_obj = None
        try:
            site_obj = sites.get_site('Subaru')
        except Exception:
            return
        site_obj.update_status(dct)

    def consume_stream(self, ev_quit, status_q):
        # consume and ingest the status stream
        while not ev_quit.is_set():
            try:
                envelope = status_q.get(block=True, timeout=1.0)
                status_dict = envelope['status']

                res_dct = {key: status_dict.get(key, self.status_dict[key])
                           for key in self.status_dict.keys()}
                self.logger.debug("status is: %s" % str(res_dct))

                self.update_status(res_dct)

            except Queue.Empty:
                continue

            except Exception as e:
                self.logger.error("Error processing status: {}".format(e))

    def __str__(self):
        return 'subaruocs'
