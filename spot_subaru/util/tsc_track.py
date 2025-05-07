from datetime import UTC
from dateutil.parser import parse as parse_date

from astropy.time import Time
from astropy.table import Table
import astropy.units as u

from ginga.util import wcs

from spot.util import target as spot_target


class TSCTrack:
    """Class to handle Subaru Telescope non-sidereal target tracking files.

    This class can read and write the unique format of Subaru Telescope's
    non-sidereal tracking files and transform it into SPOT's internal
    representation for non-sidereal targets.
    """
    def __init__(self):
        self.name = ''
        self.pm_ra = 0.0 * u.arcsec / u.year
        self.pm_dec = 0.0 * u.arcsec / u.year
        self.parallax = 0.0 * u.arcsec
        self.tz = UTC
        self.track_tbl = None
        # maximum number of points TSC allows
        self.max_points = 6000

    def write_io(self, out_f):
        """Write contents to an open I/O object."""
        # HEADER
        # 1. Comment line, including name of target (if possible)
        out_f.write(f"# {self.name}\n")
        # 2. RA, DEC proper motion (arcsec/year), E-term, annual parallax
        pm_ra = self.pm_ra.value
        pm_dec = self.pm_dec.value
        parallax = self.parallax.value
        out_f.write(f"{pm_ra:+08.4f} {pm_dec:+08.4f} ON% {parallax:+06.3f}\n")
        # 3. Time scale (UTC/TDT)
        out_f.write("UTC Geocentric Equatorial Mean Polar Geocentric\n")
        # 4. ABS/REL
        out_f.write("ABS\n")
        # 5. Flag for Az drive direction (+/-/TSC)
        out_f.write("TSC\n")
        # 6. Number of coordinate points
        out_f.write("{}\n".format(len(self.track_tbl)))

        # BODY. Each line is
        # (datetime, ra, dec, delta, equinox) in their special formats
        for i in range(min(len(self.track_tbl), self.max_points)):
            dt = self.track_tbl['DateTime'][i]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            else:
                dt = dt.astimezone(UTC)
            # datetime (format: YYYYMMDDHHMMSS.SSS)
            dt_tsc = '.'.join([dt.strftime('%Y%m%d%H%M%S'),
                               '{%03d}'.format(int(dt.microsecond / 1000))])
            # ra in "funky SOSS format" (HHMMSS.SSS)
            ra_fsf = wcs.ra_deg_to_str(self.track_tbl['RA'][i],
                                       format='%02d%02d%02d.%03d',
                                       precision=3)
            # dec in "funky SOSS format" ([+-]DDMMSS.SS)
            dec_fsf = wcs.dec_deg_to_str(self.track_tbl['DEC'][i],
                                         format='%s%02d%02d%02d.%02d',
                                         precision=2)
            # delta between points
            delta = self.track_tbl['delta'][i]
            # equinox
            equinox = 2000.0
            out_f.write(f"{dt_tsc} {ra_fsf} {dec_fsf} {delta:13.9f} {equinox:9.4f}\n")

    def write_file(self, out_path):
        """Write contents to a file."""
        with open(out_path, 'w') as out_f:
            self.write_io(out_f)

    def read_io(self, in_f):
        """Read contents from an open I/O object."""
        lines = in_f.read().split('\n')
        header = lines[:6]
        lines = lines[6:]

        # process header
        i = header[0].index('#')
        self.name = header[0][i + 1:]
        pm_ra, pm_dec, term, parallax = header[1].split()
        self.pm_ra = float(pm_ra) * u.arcsec / u.year
        self.pm_dec = float(pm_dec) * u.arcsec / u.year
        self.parallax = float(parallax) * u.arcsec

        num_coords = int(header[5])

        # process body into tracking table
        ras = []
        decs = []
        eqs = []
        deltas = []
        dts = []
        for line in lines[:num_coords]:
            dt_s, ra_fsf, dec_fsf, delta, equinox = line.split()
            dt = parse_date("{}T{}".format(dt_s[:8], dt_s[8:]))
            dt = dt.replace(tzinfo=UTC)
            dts.append(dt)
            ra_deg, dec_deg, eq = spot_target.normalize_ra_dec_equinox(ra_fsf,
                                                                       dec_fsf,
                                                                       equinox)
            ras.append(ra_deg)
            decs.append(dec_deg)
            eqs.append(eq)
            deltas.append(float(delta))

        t = Time(dts)
        dt_jds = t.jd

        # create table of relevant data
        self.track_tbl = Table(data=[dt_jds, dts, ras, decs, deltas],
                               names=['datetime_jd', 'DateTime', 'RA', 'DEC',
                                      'delta'])

    def read_file(self, in_path):
        """Read contents from a file."""
        with open(in_path, 'r') as in_f:
            self.read_io(in_f)

    def import_target(self, target):
        """Import contents from a SPOT non-sidereal target."""
        if not target.get('nonsidereal', False):
            raise ValueError("not a nonsidereal target")

        track_tbl = target.get('track', None)
        if track_tbl is None:
            raise ValueError("nonsidereal target does not contain a tracking table")

        self.name = target.name
        self.pm_ra = float(target.get('pm_ra', 0.0)) * u.arcsec / u.year
        self.pm_dec = float(target.get('pm_dec', 0.0)) * u.arcsec / u.year
        self.parallax = float(target.get('parallax', 0.0)) * u.arcsec

        self.track_tbl = target.get('track', None)

    @classmethod
    def from_target(cls, target):
        trk = cls()
        trk.import_target(target)
        return trk

    def to_target(self, dt=None, category='Non-sidereal'):
        """Export contents to a SPOT non-sidereal target."""
        ra_deg, dec_deg = self.track_tbl['RA'][0], self.track_tbl['DEC'][0]
        target = spot_target.Target(name=self.name, ra=ra_deg, dec=dec_deg,
                                    equinox=2000.0, category=category)
        target.set(nonsidereal=True, track=self.track_tbl,
                   pm_ra=self.pm_ra, pm_dec=self.pm_dec, parallax=self.parallax)

        if dt is not None:
            spot_target.update_nonsidereal_targets([target], dt)

        return target
