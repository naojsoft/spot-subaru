from collections import namedtuple
from datetime import datetime
import threading

import yaml
try:
    import sqlalchemy
    from sqlalchemy.orm import Session
    from sqlalchemy.sql import text
    have_sqldb = True
except ImportError:
    have_sqldb = False

from g2base import Bunch


class Collisions:

    def __init__(self, logger, ltcs_db_cfg_path):
        self.logger = logger

        # --------------------------------------------------------------
        # global variables for the state of the collisions with other
        # telescopes
        self.ltcs_status = 4
        self.ltcs_status_name_array = ['OPEN', 'COLLISIONS', 'PREDICTED',
                                       'DOWN', 'ERROR', 'UP']
        self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
        self.ltcs_list_collisions = []
        self.ltcs_time_start = 0.0
        self.ltcs_source = 'database_sim'

        self.ok_collisions = False
        self._lock = threading.RLock()
        self.boxes_duration = None

        self.coll_event = namedtuple('CollEvent',
                                     ['time_start_sse', 'time_stop_sse',
                                      'telescope_str', 'laser_has_priority'])

        # read config file and get LTCS db connection info
        self._conn = None
        with open(ltcs_db_cfg_path, 'r') as in_f:
            buf = in_f.read()
        self.ltcs_cfg_d = yaml.safe_load(buf)
        self.logger.info(f'ltcs_cfg_d {self.ltcs_cfg_d}')

        laser = self.ltcs_cfg_d['laser']
        self.query_dct1 = {'sys_health': "SELECT component, timestamp FROM system_health ORDER BY component;",
                           'collisions': f"SELECT * FROM collisions WHERE laser='{laser}' ORDER BY start_time;",
                           'sim_predict': f"SELECT * FROM sim_predictions  WHERE laser LIKE '%{laser}' ORDER BY start_time;",
                           'predict': f"SELECT laser,involved_scope,start_time,end_time,laser_has_priority FROM predictions WHERE laser='{laser}' ORDER BY start_time;",
                          }

        # the current status of the system
        self.status = Bunch.Bunch(dict(remain_str='',
                                       remain_sec=0.0,
                                       impact='',
                                       collisions_str='',
                                       collisions_list_str='',
                                       collisions_status='DOWN',
                                       ltcs_collisions=[],
                                       ltcs_status=3,
                                       ltcs_status_str='DOWN',
                                       ok_collisions=False))

    def connect_db(self):
        self._conn = None
        # Connect to the LTCS database
        ltcs_cfg_db = self.ltcs_cfg_d[self.ltcs_source]
        engine_args = dict(echo=ltcs_cfg_db['sql_echo'])
        if 'sqlite' in ltcs_cfg_db['driver']:
            db_url = f"{ltcs_cfg_db['driver']}:///{ltcs_cfg_db['filename']}"
        else:
            pool_size = 10
            engine_args['pool_size'] = pool_size
            db_url = sqlalchemy.engine.url.URL.create(ltcs_cfg_db['driver'],
                                                      host=ltcs_cfg_db['hostname'],
                                                      database=ltcs_cfg_db['dbname'],
                                                      username=ltcs_cfg_db['user'],
                                                      password=ltcs_cfg_db['passwd'])
        try:
            self.logger.info(f'LTCS database URL is {db_url} engine_args {engine_args}')
            self._conn = sqlalchemy.create_engine(db_url, **engine_args)
        except Exception as e:
            self.logger.error(f"Error connecting to LTCS database at {db_url}", exc_info=True)
            raise e

    def disconnect_db(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def get_pointings(self):
        results_d = dict()
        try:
            if self._conn is None:
                self.connect_db()

            # get telescope pointings
            query = "select * from pointing"
            with Session(self._conn) as session:
                results = session.execute(text(query)).fetchall()
                # TODO: is there a preferred way instead of _mapping?
                for res_d in [dict(row._mapping) for row in results]:
                    results_d[res_d['scope']] = dict(name=res_d['scope'],
                                                     # convert hours => deg
                                                     # TODO: there seems to be a
                                                     # problem if we just have float
                                                     # which should be interpreted
                                                     # as degrees
                                                     #ra=wcs.ra_deg_to_str(res_d['ra'] * 15.0),
                                                     #dec=wcs.dec_deg_to_str(res_d['decl']),
                                                     ra=float(res_d['ra'] * 15.0),
                                                     dec=float(res_d['decl']),
                                                     equinox=float(res_d['equinox']),
                                                     )

                # add laser impacted state
                query = "select * from laser_sensitivity"
                results = session.execute(text(query)).fetchall()
                # TODO: is there a preferred way instead of _mapping?
                for res_d in [dict(row._mapping) for row in results]:
                    results_d[res_d['scope']]['laser_impacted'] = res_d['laser_impacted']

                # add info about freshness of data
                query = "select * from url_states"
                results = session.execute(text(query)).fetchall()
                # TODO: is there a preferred way instead of _mapping?
                for res_d in [dict(row._mapping) for row in results]:
                    results_d[res_d['scope']]['state'] = res_d['state']

            #self.logger.debug(str(results_d))
            return results_d

        except Exception as e:
            self.logger.error(f"Error querying for telescope positions: {e}",
                              exc_info=True)
            self.disconnect_db()
            return None

    def fetch_ltcs(self):
        """Updates our knowledge of collisions by querying LTCS database
        """
        if self._conn is None:
            self.connect_db()
        # get the status of the Mauna Kea LTCS system
        q_res = {}
        with Session(self._conn) as session:
            for k, q in self.query_dct1.items():
                try:
                    self.logger.debug(f'Execute query for {k}')
                    q_res[k] = session.execute(text(q)).fetchall()
                    self.logger.debug(f'Query for {k} returned q_res {q_res[k]}')
                except Exception as e:
                    config_str = str(self.ltcs_cfg_d[self.ltcs_source])
                    self.logger.error(f"Error connecting to LTCS database using config: {config_str}", exc_info=True)
                    raise e
        return q_res

    def check_ltcs_proc(self, current_sse, q_res, stale_threshold_sec):
        ok_ltcs = True
        for r in q_res:
            component = r[0]
            timestamp = r[1]
            if current_sse - timestamp > stale_threshold_sec:
                ok_ltcs = False
                last_update = datetime.fromtimestamp(timestamp)
                self.logger.warning(f'Error: LTCS process {component} is stale. Last update was {last_update.isoformat()}')
                print(component, timestamp, current_sse)

        if ok_ltcs:
            self.ltcs_status = 5
            self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
        else:
            # UNDO!!!
            #self.ltcs_status = 3
            self.ltcs_status = 5
            self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
            self.logger.error("LTCS processes are down")

    def add_ltcs_collisions(self, q_res):
        for r in q_res:
            laser = r[0]
            involved_scope = r[1]
            time_start = float(r[2])
            time_end = float(r[3])
            priority = r[4]
            laser_has_priority = True if priority == 1 else False
            d = self.coll_event(time_start, time_end, involved_scope, laser_has_priority)
            self.ltcs_list_collisions.append(d)

    def check_ltcs(self, current_sse, q_res):

        self.check_ltcs_proc(current_sse, q_res['sys_health'],
                             self.ltcs_cfg_d['stale_threshold_sec'])

        if self.ltcs_status == 5:
            # Add the currently active collisions to the
            # self.ltcs_list_collisions list
            self.add_ltcs_collisions(q_res['collisions'])
            self.logger.debug("found collisions: {}".format(str(self.ltcs_list_collisions)))

            # Add the 'Laser "ON" Preview' predictions and collisions to
            # the self.ltcs_list_collisions list.
            # TODO: if the laser is only "ON" and not "ON-SKY", should
            # these be considered "real collisions" or should they fall in
            # the "predicted" category?
            self.add_ltcs_collisions(q_res['sim_predict'])
            self.logger.debug("found predicted collisions: {}".format(str(self.ltcs_list_collisions)))

            # Add collisions that are predicted to occur in the
            # future. Note that the laser has to be in the "ON-SKY" state
            # for the LTCS to report "predicted" collisions.
            self.add_ltcs_collisions(q_res['predict'])
            self.logger.info("all collisions: {}".format(str(self.ltcs_list_collisions)))

    def check_collisions(self, current_sse):
        self.logger.debug(f'self.ltcs_list_collisions {self.ltcs_list_collisions}')
        self.logger.debug(f'self.ltcs_status {self.ltcs_status} self.ltcs_status_str {self.ltcs_status_str}')

        collisions_remain = current_sse + 365 * 24 * 3600
        collisions_remain_str = ''
        collisions_str = ''
        collisions_list = ''
        collisions_impact = ''

        if self.ltcs_status == 3:
            self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
            self.ok_collisions = False

        elif len(self.ltcs_list_collisions) == 0:
            # no collisions
            self.ok_collisions = True
            self.ltcs_status = 0
            self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]

        else:
            self.logger.info(f'self.ltcs_list_collisions {self.ltcs_list_collisions}')

            collisions_start_min =  current_sse + 365 * 24 * 3600
            collisions_end_max =  current_sse - 365 * 24 * 3600
            collisions_list = ''
            within_coll = False
            # loop over all collisions in the list
            n = 0
            for curr_coll in self.ltcs_list_collisions:
                # only for collisions that are not in the past
                if curr_coll.time_stop_sse > current_sse:
                    n += 1
                    time_start_dt = datetime.fromtimestamp(curr_coll.time_start_sse)
                    time_stop_dt = datetime.fromtimestamp(curr_coll.time_stop_sse)
                    time_start_str = time_start_dt.strftime('%H:%M')
                    time_end_str = time_stop_dt.strftime('%H:%M')
                    duration = time_stop_dt - time_start_dt
                    time_duration_minutes = int(duration.total_seconds() / 60.0)
                    if n > 1:
                        collisions_list += ' // '
                    collisions_list += f'{time_start_str} -> {time_end_str} = {time_duration_minutes}min'

                    # check if this is the next collision
                    if curr_coll.time_start_sse < collisions_start_min and curr_coll.time_stop_sse > current_sse:
                        collisions_start_min = curr_coll.time_start_sse
                        collisions_impact = curr_coll.telescope_str

                    # within a collision?
                    if curr_coll.time_start_sse <= current_sse and curr_coll.time_stop_sse >= current_sse:
                        within_coll = True

                        # longest collision?
                        if curr_coll.time_stop_sse > collisions_end_max:
                            collisions_end_max = curr_coll.time_stop_sse
                            collisions_impact  = curr_coll.telescope_str

            # the time until the next collision / end of current collision
            if within_coll:
                collisions_remain = collisions_end_max - current_sse
                collisions_str = ' until '
                self.ok_collisions = False
                self.ltcs_status = 1
                self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
            else:
                collisions_remain = collisions_start_min - current_sse
                collisions_str = ' in '

                # check if collision in the future found
                if collisions_remain > 0 and collisions_remain < 24 * 3600:
                    self.ok_collisions = True
                    self.ltcs_status = 2
                    self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
                else:
                    self.ok_collisions = True
                    self.ltcs_status = 0
                    self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
                    collisions_str = ''
                    collisions_list = ''

            if collisions_remain > 0 and collisions_remain < 24 * 3600:
                th, rem = divmod(collisions_remain, 3600)
                tm, ts = divmod(rem, 60)
                th, tm, ts = int(th), int(tm), int(ts)
                collisions_remain_str = f"{th:02d}:{tm:02d}:{ts:02d}"
                collisions_str = collisions_str +  collisions_remain_str + ' with ' + collisions_impact
            else:
                collisions_str = ''

            self.logger.info(f'collisions_list {collisions_list}')
            self.logger.info(f'collisions_remain_str {collisions_remain_str}')
            self.logger.info(f'collisions_str {collisions_str}')

        # update the global variables
        self.status.update(dict(remain_str=collisions_remain_str,
                                remain_sec=collisions_remain,
                                impact=collisions_impact,
                                collisions_str=collisions_str,
                                collisions_list_str=collisions_list,
                                collisions_status=self.ltcs_status_str,
                                ltcs_collisions=list(self.ltcs_list_collisions),
                                ltcs_status=self.ltcs_status,
                                ltcs_status_str=self.ltcs_status_str,
                                ok_collisions=self.ok_collisions))

        self.logger.debug("collisions status: {}".format(str(self.status)))

    def update(self, dt):

        time_sse = dt.timestamp()

        with self._lock:
            self.ltcs_list_collisions = []

            try:
                q_res = self.fetch_ltcs()
                self.ltcs_status = 3
                self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
                # status will be updated in check_ltcs()
                self.check_ltcs(time_sse, q_res)

            except Exception as e:
                # error accessing LTCS DB
                self.logger.error(f"error accessing LTCS DB: {e}",
                                  exc_info=True)
                self.ltcs_status = 4
                self.ltcs_status_str = self.ltcs_status_name_array[self.ltcs_status]
                self.status.update(dict(ltcs_status=self.ltcs_status,
                                        ltcs_status_str=self.ltcs_status_str,
                                        ok_collisions=False))
                return

            try:
                self.check_collisions(time_sse)

            except Exception as e:
                self.logger.error(f'Error calling check_collisions: {str(e)}',
                                  exc_info=True)

    def get_status(self):
        with self._lock:
            return self.status.copy()
