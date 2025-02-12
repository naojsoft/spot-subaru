#
# HSCPlanner.py -- HSCPlanner plugin for Ginga reference viewer
#
# E. Jeschke
#
# This is open-source software licensed under a BSD license.
# Please see the file LICENSE.txt for details.
#
"""
Ginga plugin to visualize HSC detector array on a science field through
the dither.

Requires:
  * ginga (https://github.com/naojsoft/ginga.git)
  * naojutils (https://github.com/naojsoft/naojutils.git)
"""
import numpy

from ginga import GingaPlugin
from ginga.gw import Widgets
from ginga.util import wcs

from naoj.hsc import ccd_info, sdo

from spot.util.target import normalize_ra_dec_equinox


class HSCPlanner(GingaPlugin.LocalPlugin):
    """
    HSCPlanner works according to the following steps:

    A) establish the pointing of the telescope
    B) create a blank field or survey image field from the established pointing
    C) place one or more targets within the field
    D) set the acquisition parameters and visualize
    E) repeat D) or from earlier steps as needed or desired

    We will go over each of these steps in turn.

    A) Establishing Pointing

    The easiest way to establish pointing is to load the Targets plugin
    with your targets from an OPE or CSV file.  Then, select exactly one
    of the targets and then click the "From a target" button.  The RA,
    DEC, Equinox and Object fields should be filled out.

    If you prefer to set it manually, you can type RA and DEC coordinates
    into the corresponding boxes under the "Pointing" section of the GUI
    and click "Set manually". You can use sexigesimal format, with RA in
    H:M:S and DEC in D:M:S or decimal numbers interpreted as degrees.

    B) Create Field from Pointing

    Once pointing is established, we need to create a background field with
    correct WCS to do the correct overplotting to visualize the acquisition.
    We will generally do this with the FindImage (Finding Chart) plugin.
    Make sure that plugin is started, and then click the button
    "Send pointing to FindImage" to set the defined pointingin that plugin.

    In FindImage, create a blank field (by clicking "Create Blank"), or
    download a survey image of the field (select a survey and click
    "Find image"). If you'd prefer to use your own locally stored FITS
    image, you can load it using the "Load FITS" button.

    Note that a blank field is perfectly fine for visualizing where your
    targets will land on the detectors.

    C) Placing Targets within the Field

    To place targets within the field, select the desired targets in the
    Targets table and click "Add Targets".  To clear any existing targets
    click "Clear Targets".

    D) Set the Acquisition Parameters and Visualize

    Now we are finally ready to set the acquisition parameters and visualize
    the field throughout the dither. In the section labeled "Acquisition"
    you can set any of the parameters normally used for HSC acquisition.

    The parameters are:

    - Dither type: 1 for a single shot, 5 for a 5-point box pattern, and N
    for an N-point circular pattern

    - Dither steps: only settable for N-type dither, set it to the number
    of dither positions

    - Pos Angle: this parameter will set up the instrument rotator to set
    the rotation of the field on the CCD plane so that the desired position
    angle is achieved--see the instrument documentation for details.

    - RA Offset, DEC Offset: offsets in arc seconds from the pointing
    position in the center of the field

    - Dith1, Dith2 (Delta RA, Delta DEC or RDITH, TDITH): the names of
    these parameters change according to the dither type selected.
    For Dither Type 1 they are not used.  For Dither Type 5, these
    parameters specify the offsets in arc seconds for Delta RA and Delta DEC
    to accomplish the dither between positions.  For Dither Type N they
    specify the offset in arc seconds (RDITH) and the angle offset in
    degrees (TDITH) for the circular dither.  See the instrument documentation
    for more information.

    - Skip: the number of shots to skip from the beginning of a dither.
    Leave at the default for the full dither.

    - Stop: used to terminate a dither early after a certain number of shots.
    Leave at the default for the full dither.

    Once you have set the parameters as desired, click the "Update View"
    button to update the overlays. You can then use the "Dither Pos" control
    to step through your dither.  The mouse wheel can be used effectively
    with this control.

    HINTS

    It may be helpful to view the field first with the image zoomed out,
    and then later to pan to your target (hint: use Shift+click to set pan
    position) and zoom in to more closely watch the detailed positioning of
    the target(s) on the detector grid.

    E) Repeat as Desired

    You can go back to any step and repeat from there as needed.  It may be
    helpful when repositioning targets to press the "Clear Overlays" button,
    which will remove the detector and dither position overlays.  Pressing
    "Update Image" will bring them right back.
    """
    def __init__(self, fv, fitsimage):
        # superclass defines some variables for us, like logger
        super(HSCPlanner, self).__init__(fv, fitsimage)

        if not self.chname.endswith('_FIND'):
            return

        self.layertag = 'hscplanner-canvas'
        self.dither_types = ['1', '5', 'N']
        self.dither_type = '1'
        self.dither_steps = 5

        self.ctr_ra_deg = 0.0
        self.ctr_deg_deg = 0.0
        # TODO
        self.ctr_equinox = 2000.0
        self.targets = []
        #self.target_radius = 0.01
        self.target_radius = 20
        self.pa_deg = 0.0

        # default dra/ddec is 120"
        self.dra = 120.0
        self.ddec = 120.0
        self.tdith = 15.0
        self.rdith = 120.0

        self.dc = fv.get_draw_classes()
        canvas = self.dc.DrawingCanvas()
        canvas.set_callback('cursor-down', self.btn_down_cb)
        canvas.register_for_cursor_drawing(self.fitsimage)
        canvas.set_surface(self.fitsimage)
        self.canvas = canvas

        self.viewer = self.fitsimage
        self.viewer.add_callback('redraw', self.redraw_cb)
        self.gui_up = False

    def build_gui(self, container):

        if not self.chname.endswith('_FIND'):
            raise Exception(f"This plugin is not designed to run in channel {self.chname}")

        top = Widgets.VBox()
        top.set_border_width(4)

        vbox, sw, orientation = Widgets.get_oriented_box(container)
        vbox.set_border_width(4)
        vbox.set_spacing(2)

        fr = Widgets.Frame("Step A: Pointing")

        captions = (('RA:', 'label', 'RA', 'entry',
                     'DEC:', 'label', 'DEC', 'entry',),
                    ('Equinox:', 'label', 'Equinox', 'entry',
                     'Object:', 'label', 'Name', 'entry'),
                    ('__ph1', 'spacer', 'Set Manually', 'button',
                     '__ph2', 'spacer', 'From a target', 'button'),
                    ('Pointing:', 'label', 'pra', 'llabel',
                     '__ph3', 'spacer', 'pdec', 'llabel'),
                    )
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w = b

        b.pra.set_text('')
        b.pdec.set_text('')
        b.equinox.set_text("2000")
        # Currently assume J2000 targets--this will be fixed in future
        b.equinox.set_enabled(False)
        b.set_manually.add_callback('activated',
                                    lambda w: self.set_pointing_manually_cb())
        b.set_manually.set_tooltip("Set pointing from manually entered coordinates above")
        b.from_a_target.add_callback('activated',
                                     lambda w: self.set_pointing_from_a_target_cb())
        b.from_a_target.set_tooltip("Set pointing by a single selected target from the Targets table")

        fr.set_widget(w)
        vbox.add_widget(fr, stretch=0)

        fr = Widgets.Frame("Step B: Set Field")
        vb2 = Widgets.VBox()
        vb2.set_border_width(0)
        vb2.set_spacing(2)
        lbl = Widgets.TextArea(wrap=True, editable=False)
        lbl.set_text("Set an image with WCS for this pointing. Open the FindImage "
                     "(Finding Chart) plugin if it is not already open.  Click the "
                     "'Send Pointing to FindImage' button below to establish pointing there. "
                     "Then create a blank image, download a survey image or load your "
                     "own FITS image using the buttons there. Note that a blank image "
                     "is sufficient to see where the targets will land on the detectors "
                     "at each step.")
        vb2.add_widget(lbl, stretch=1)
        hb2 = Widgets.HBox()
        hb2.set_border_width(2)
        hb2.set_spacing(2)
        btn = Widgets.Button("Send Pointing to FindImage")
        btn.set_tooltip("Set the pointing in FindImage from this Step A pointing")
        btn.add_callback('activated', lambda w: self.send_pointing_cb())
        hb2.add_widget(btn, stretch=0)
        hb2.add_widget(Widgets.Label(''), stretch=1)
        vb2.add_widget(hb2, stretch=0)

        fr.set_widget(vb2)
        vbox.add_widget(fr, stretch=0)

        fr = Widgets.Frame("Step C: Targets")

        captions = (('Add Targets', 'button', 'Clear Targets', 'button'),
                    )
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w.update(b)

        b.add_targets.add_callback('activated', lambda w: self.add_targets_cb())
        b.add_targets.set_tooltip("Add selected targets in the Targets table")
        b.clear_targets.add_callback('activated', lambda w: self.clear_targets_cb())
        b.clear_targets.set_tooltip("Clear all currently added targets")

        fr.set_widget(w)
        vbox.add_widget(fr, stretch=0)

        fr = Widgets.Frame("Step D: Acquisition")

        vbox2 = Widgets.VBox()
        captions = (('Dither Type:', 'label', 'Dither Type', 'combobox',
                     'Dither Steps:', 'label', 'Dither Steps', 'spinbutton'),
                    ('INSROT_PA:', 'label', 'PA', 'entry'),
                    ('RA Offset:', 'label', 'RA Offset', 'entry',
                     'DEC Offset:', 'label', 'DEC Offset', 'entry',),
                    ('Dith1:', 'label', 'Dith1', 'entry',
                     'Dith2:', 'label', 'Dith2', 'entry',),
                    ('Skip:', 'label', 'Skip', 'spinbutton',
                     'Stop:', 'label', 'Stop', 'spinbutton'),
                    )
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w.update(b)

        combobox = b.dither_type
        for name in self.dither_types:
            combobox.append_text(name)
        index = self.dither_types.index(self.dither_type)
        combobox.set_index(index)
        combobox.add_callback('activated', lambda w, idx: self.set_dither_type_cb())
        combobox.set_tooltip("Set dither type")
        b.pa.set_text(str(self.pa_deg))
        b.dither_steps.set_limits(1, 20)
        b.dither_steps.add_callback('value-changed',
                                    lambda w, idx: self.set_dither_steps_cb(idx))
        b.dither_steps.set_tooltip("Number of dither steps")
        b.pa.set_tooltip("Position angle for exposures in deg")

        b.ra_offset.set_text(str(0.0))
        b.dec_offset.set_text(str(0.0))
        b.ra_offset.set_tooltip("RA offset from center of field in arcsec")
        b.dec_offset.set_tooltip("DEC offset from center of field in arcsec")
        b.skip.set_value(0)
        b.skip.set_tooltip("Skip over some dither steps")
        b.stop.set_value(1)
        b.stop.set_tooltip("Stop at a particular dither step")

        vbox2.add_widget(w)
        self.set_dither_type_cb()

        captions = (('_x3', 'spacer', '_x4', 'spacer',
                     '_x5', 'spacer'),
                    ('Update View', 'button', 'Clear Overlays', 'button'),
                    )
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w.update(b)

        b.update_view.add_callback('activated', lambda w: self.update_info_cb())
        b.update_view.set_tooltip("Update the overlays after changing acquisition parameters")
        b.clear_overlays.add_callback('activated', lambda w: self.clear_overlays())
        b.clear_overlays.set_tooltip("Clear some of the overlays")

        vbox2.add_widget(w)

        fr.set_widget(vbox2)
        vbox.add_widget(fr, stretch=0)

        captions = (("Dither Pos:", 'label', 'Show Step', 'spinbutton'),
                    )
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w.update(b)

        b.show_step.add_callback('value-changed',
                                 lambda w, idx: self.show_step_cb(idx))
        b.show_step.set_tooltip("Show position of detectors at dither step")

        vbox.add_widget(w, stretch=0)

        # spacer = Widgets.Label('')
        # vbox.add_widget(spacer, stretch=1)

        top.add_widget(sw, stretch=1)

        btns = Widgets.HBox()
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

    def set_dither_type_cb(self):
        index = self.w.dither_type.get_index()
        self.dither_type = self.dither_types[index]

        if self.dither_type == '1':
            self.w.dither_steps.set_value(1)
            self.w.dither_steps.set_enabled(False)
            ## self.w.show_step.set_limits(1, 1)
            self.w.dith1.set_enabled(False)
            self.w.dith2.set_enabled(False)
            self.w.dith1.set_text('')
            self.w.dith2.set_text('')
            self.w.skip.set_enabled(False)
            self.w.stop.set_enabled(False)

        elif self.dither_type == '5':
            self.w.dith1.set_text(str(self.dra))
            self.w.dith2.set_text(str(self.ddec))
            self.w.dither_steps.set_value(5)
            self.w.dither_steps.set_enabled(False)
            ## self.w.show_step.set_limits(1, 5)
            self.w.dith1.set_enabled(True)
            self.w.dith2.set_enabled(True)
            self.w.lbl_dith1.set_text("Delta RA:")
            self.w.lbl_dith2.set_text("Delta DEC:")
            self.w.skip.set_enabled(True)
            self.w.skip.set_limits(0, 4)
            self.w.skip.set_value(0)
            self.w.stop.set_enabled(True)
            self.w.stop.set_limits(1, 5)
            self.w.stop.set_value(5)

        else:
            N = self.dither_steps
            self.w.dith1.set_text(str(self.rdith))
            self.w.dith2.set_text(str(self.tdith))
            self.w.dither_steps.set_value(N)
            self.w.dither_steps.set_enabled(True)
            ## self.w.show_step.set_limits(1, N)
            self.w.dith1.set_enabled(True)
            self.w.dith2.set_enabled(True)
            self.w.lbl_dith1.set_text("RDITH:")
            self.w.lbl_dith2.set_text("TDITH:")
            self.w.skip.set_enabled(True)
            self.w.skip.set_limits(0, N - 1)
            self.w.skip.set_value(0)
            self.w.stop.set_enabled(True)
            self.w.stop.set_limits(1, N)
            self.w.stop.set_value(N)

        #self.fv.error_wrap(self.draw_dither_positions)
        #self.show_step(1)

        return True

    def update_info_cb(self):
        try:
            # calculate center and target coordinates
            ra_off_deg = float(self.w.ra_offset.get_text()) / 3600.0
            dec_off_deg = float(self.w.dec_offset.get_text()) / 3600.0
            self.ra_off_deg = ra_off_deg
            self.dec_off_deg = dec_off_deg

            self.pa_deg = float(self.w.pa.get_text())

            # save dither params
            if self.dither_type == '5':
                self.dra = float(self.w.dith1.get_text())
                self.ddec = float(self.w.dith2.get_text())

            elif self.dither_type == 'N':
                self.rdith = float(self.w.dith1.get_text())
                self.tdith = float(self.w.dith2.get_text())

            # create a blank image if we don't have one already
            image = self.fitsimage.get_image()
            if image is None:
                self.create_blank_image()

            # add targets to canvas
            #self.draw_targets()

            self.draw_dither_positions()

            self.fv.error_wrap(self.draw_ccds,
                               self.ctr_ra_deg, self.ctr_dec_deg)

            start = int(self.w.skip.get_value()) + 1
            stop = int(self.w.stop.get_value())
            self.w.show_step.set_limits(start, stop)
            self.w.show_step.set_value(start)

            self.show_step(start)

        except Exception as e:
            self.fv.show_error(str(e))
        return True

    def calc_dither1(self, n):
        ctr_ra, ctr_dec = wcs.add_offset_radec(
            self.ctr_ra_deg, self.ctr_dec_deg,
            self.ra_off_deg, self.dec_off_deg)
        return (ctr_ra, ctr_dec)

    def calc_dither5(self, n):
        idx = n - 1
        l = ((0.0, 0.0), (1.0, -2.0), (2.0, 1.0), (-1.0, 2.0), (-2.0, -1.0))
        mra, mdec = l[idx]

        dra = float(self.w.dith1.get_text()) / 3600.0
        ddec = float(self.w.dith2.get_text()) / 3600.0

        ctr_ra, ctr_dec = wcs.add_offset_radec(
            self.ctr_ra_deg, self.ctr_dec_deg,
            mra * dra + self.ra_off_deg, mdec * ddec + self.dec_off_deg)
        return (ctr_ra, ctr_dec)

    def calc_ditherN(self, n):
        n = n - 1
        rdith = float(self.w.dith1.get_text()) / 3600.0
        tdith = float(self.w.dith2.get_text())
        ndith = float(self.dither_steps)

        sin_res = numpy.sin(numpy.radians(n * 360.0 / ndith + tdith))
        cos_res = numpy.cos(numpy.radians(n * 360.0 / ndith + tdith))
        self.logger.debug("sin=%f cos=%f" % (sin_res, cos_res))

        ctr_ra, ctr_dec = wcs.add_offset_radec(
            self.ctr_ra_deg, self.ctr_dec_deg,
            cos_res * rdith + self.ra_off_deg,
            sin_res * rdith + self.dec_off_deg)
        return (ctr_ra, ctr_dec)

    def calc_dither(self, n):
        dith_type = self.dither_type
        if dith_type == '1':
            ra, dec = self.calc_dither1(n)
        elif dith_type == '5':
            ra, dec = self.calc_dither5(n)
        elif dith_type == 'N':
            ra, dec = self.calc_ditherN(n)
        return (ra, dec)

    def get_dither_positions(self):
        dith_type = self.dither_type
        skip = self.w.skip.get_value()
        stop = self.w.stop.get_value()

        if dith_type == '1':
            return 1, 1, [self.calc_dither1(n) for n in range(1, 2)]
        elif dith_type == '5':
            return skip + 1, stop, [self.calc_dither5(n)
                                    for n in range(skip + 1, stop + 1)]
        elif dith_type == 'N':
            #N = self.dither_steps
            return skip + 1, stop, [self.calc_ditherN(n)
                                    for n in range(skip + 1, stop + 1)]

    def set_dither_steps_cb(self, n):
        self.dither_steps = n
        ## self.w.show_step.set_limits(1, n)
        self.w.skip.set_limits(0, n - 1)
        self.w.skip.set_value(0)
        self.w.stop.set_limits(1, n)
        self.w.stop.set_value(n)
        #self.fv.error_wrap(self.draw_dither_positions)
        return True

    def _show_step(self, n):
        self.logger.info("moving to step %d" % (n))
        ra, dec = self.calc_dither(n)
        image = self.fitsimage.get_image()
        data_x, data_y = image.radectopix(ra, dec)
        delta_x = data_x - self.ctr_pt.x
        delta_y = data_y - self.ctr_pt.y
        self.ccd_overlay.move_delta_pt((delta_x, delta_y))
        self.canvas.update_canvas()
        return True

    def show_step(self, n):
        #self.w.show_step.set_text(str(n))
        self.w.show_step.set_value(n)
        self._show_step(n)

    def show_step_cb(self, n):
        #n = int(strip(self.w.show_step.get_text()))
        self.logger.info("step %d!" % (n))
        self.fv.error_wrap(self._show_step, n)
        return True

    def close(self):
        chname = self.fv.get_channel_name(self.fitsimage)
        self.fv.stop_local_plugin(chname, str(self))
        return True

    def help(self):
        name = str(self).capitalize()
        self.fv.help_text(name, self.__doc__, trim_pfx=4)

    def start(self):
        # start operation
        p_canvas = self.fitsimage.get_canvas()
        try:
            obj = p_canvas.get_object_by_tag(self.layertag)

        except KeyError:
            # Add our layer
            p_canvas.add(self.canvas, tag=self.layertag)
        self.canvas.ui_set_active(True)

    def stop(self):
        # remove the canvas from the image
        p_canvas = self.fitsimage.get_canvas()
        try:
            p_canvas.delete_object_by_tag(self.layertag)
        except Exception:
            pass
        self.canvas.ui_set_active(False)
        self.gui_up = False
        self.fv.show_status("")

    def redo(self):
        # check pan location
        pos = self.viewer.get_pan(coord='data')[:2]
        data_x, data_y = pos[:2]
        image = self.viewer.get_image()
        if image is None:
            return
        # set pointing
        ra_deg, dec_deg = image.pixtoradec(data_x, data_y)
        self.ctr_ra_deg, self.ctr_dec_deg = ra_deg, dec_deg

        if self.gui_up:
            ra, dec = self._set_radec(ra_deg, dec_deg)
            self.w.pra.set_text(ra)
            self.w.pdec.set_text(dec)
            header = image.get_header()
            self.w.equinox.set_text(str(header.get('EQUINOX', '')))

    def _ccd_in_dither(self, dither, polygon):
        x_arr, y_arr = dither.T[0], dither.T[1]
        res = polygon.contains_arr(x_arr, y_arr)
        res = numpy.any(res)
        return res

    def get_paths(self, ctr_ra, ctr_dec, info):
        paths = []
        keys = list(info.keys())
        keys.sort()
        for key in keys:
            path = [wcs.add_offset_radec(ctr_ra, ctr_dec, dra, ddec)
                    for dra, ddec in info[key]['polygon']]
            paths.append((key, path))
        return paths

    def draw_ccds(self, ctr_ra_deg, ctr_dec_deg):
        image = self.fitsimage.get_image()
        if image is None:
            return

        ctr_x, ctr_y = image.radectopix(ctr_ra_deg, ctr_dec_deg)
        self.ctr_x, self.ctr_y = ctr_x, ctr_y

        l = []

        ctr_pt = self.dc.Point(ctr_x, ctr_y, 50,
                               linewidth=4, color='orange',
                               style='plus', coord='data')
        l.append(ctr_pt)
        self.ctr_pt = ctr_pt

        info = ccd_info.info
        paths = self.get_paths(ctr_ra_deg, ctr_dec_deg, info)

        start, stop, dither_positions = self.get_dither_positions()
        dither = [image.radectopix(pt[0], pt[1]) for pt in dither_positions]
        dither = numpy.array(dither)

        ## only_ccds_in_dithers = self.w.only_dithers.get_state()

        crdmap = self.fitsimage.get_coordmap('data')
        for key, path in paths:
            points = [image.radectopix(pt[0], pt[1]) for pt in path]
            points = numpy.array(points)

            showfill = False
            if 'color' in info[key]:
                color = info[key]['color']
            else:
                color = 'lightgreen'
            if color == 'red':
                showfill = True
            p = self.dc.Polygon(points, color=color, fill=showfill,
                                fillcolor='red', fillalpha=0.4,
                                showcap=False)

            # hack to be able to check containment before object is on
            # the canvas
            p.crdmap = crdmap

            ## # exclude this ccd if not involved in the dither
            ## if only_ccds_in_dithers and not self._ccd_in_dither(dither, p):
            ##     continue

            # annotate with the CCD name
            pcx, pcy = p.get_center_pt()
            name = sdo.sdo_map[key]
            t = self.dc.Text(pcx, pcy, text=name, color=color, fontsize=12,
                             coord='data')

            l.append(self.dc.CompoundObject(p, t))
            #l.append(p)

        obj = self.dc.CompoundObject(*l)
        obj.opaque = True
        obj.editable = False

        self.canvas.delete_object_by_tag('ccd_overlay')
        self.canvas.add(obj, tag='ccd_overlay', redraw=False)

        # rotate for pa
        obj.rotate_deg([-self.pa_deg], (ctr_x, ctr_y))
        self.ccd_overlay = obj

        self.canvas.update_canvas()
        self.logger.debug("canvas rotated")

    def draw_dither_positions(self):
        Text = self.canvas.get_draw_class('text')
        Point = self.canvas.get_draw_class('point')
        CompoundObject = self.canvas.get_draw_class('compoundobject')

        image = self.fitsimage.get_image()

        l = []
        start, stop, posns = self.get_dither_positions()
        i = start
        for ra_deg, dec_deg in posns:
            x, y = image.radectopix(ra_deg, dec_deg)
            l.append(Text(x, y, text="%d" % i, color='yellow',
                          fontscale=True, fontsize_min=14, fontsize_max=18,
                          coord='data'))
            l.append(Point(x, y, self.target_radius, color='yellow',
                           linewidth=2, style='plus', coord='data'))
            i += 1
        obj = CompoundObject(*l)
        obj.opaque = True
        obj.editable = False

        self.canvas.delete_object_by_tag('dither_positions')
        self.canvas.add(obj, tag='dither_positions')

    def draw_targets(self):
        tgts = list(self.canvas.get_objects_by_tag_pfx('target'))
        if len(tgts) > 0:
            self.canvas.delete_objects(tgts)

        for i, obj in enumerate(self.targets):
            self.canvas.add(obj, tag='target%d' % (i))

    def make_target(self, name, ra_deg, dec_deg, equinox):

        Point = self.canvas.get_draw_class('point')
        Circle = self.canvas.get_draw_class('circle')
        Text = self.canvas.get_draw_class('text')
        CompoundObject = self.canvas.get_draw_class('compoundobject')

        image = self.fitsimage.get_image()

        x, y = image.radectopix(ra_deg, dec_deg)
        obj = CompoundObject(
            Point(x, y, self.target_radius,
                  linewidth=1, color='seagreen2', coord='data'),
            Circle(x, y, self.target_radius,
                   linewidth=4, color='seagreen2', coord='data'),
            Text(x, y, text=name, color='seagreen2', coord='data',
                 fontscale=True, fontsize_min=10, fontsize_max=18))
        obj.objects[0].editable = False
        obj.objects[1].editable = False
        obj.objects[2].editable = False
        obj.opaque = True
        obj.editable = True
        self.targets.append(obj)
        i = len(self.targets)
        self.canvas.add(obj, tag='target%d' % (i))
        return obj

    def clear_overlays(self):
        self.canvas.delete_object_by_tag('dither_positions')
        self.canvas.delete_object_by_tag('ccd_overlay')

    def _set_radec(self, ra_deg, dec_deg):
        ra_txt = wcs.ra_deg_to_str(ra_deg)
        self.w.ra.set_text(ra_txt)
        dec_txt = wcs.dec_deg_to_str(dec_deg)
        self.w.dec.set_text(dec_txt)
        return ra_txt, dec_txt

    def set_pointing_manually_cb(self):
        try:
            ra = self.w.ra.get_text()
            dec = self.w.dec.get_text()

            ra_deg, dec_deg, eq = normalize_ra_dec_equinox(ra, dec, 2000.0)
            self.ctr_ra_deg, self.ctr_dec_deg = ra_deg, dec_deg

            self.w.pra.set_text(ra)
            self.w.pdec.set_text(dec)
        except Exception as e:
            self.fv.show_error(str(e))

    def set_pointing_from_a_target_cb(self):
        wsname, _ = self.channel.name.split('_')
        channel = self.fv.get_channel(wsname + '_TGTS')
        obj = channel.opmon.get_plugin('Targets')
        selected = list(obj.get_selected_targets())
        if len(selected) != 1:
            self.fv.show_error("Please select exactly one target in the Targets table!")
            return

        tgt = selected[0]
        try:
            ra_deg, dec_deg, eq = normalize_ra_dec_equinox(tgt.ra, tgt.dec, tgt.equinox)
            self.ctr_ra_deg, self.ctr_dec_deg = ra_deg, dec_deg

            self.w.name.set_text(tgt.name)
            ra_str = wcs.ra_deg_to_str(ra_deg)
            dec_str = wcs.dec_deg_to_str(dec_deg)
            self.w.ra.set_text(ra_str)
            self.w.dec.set_text(dec_str)
            self.w.equinox.set_text(str(eq))

            self.w.pra.set_text(ra_str)
            self.w.pdec.set_text(dec_str)
        except Exception as e:
            errmsg = f"Failed to process target: {e}"
            self.fv.show_error(errmsg)
            self.logger.error(errmsg, exc_info=True)

    def add_targets_cb(self):
        wsname, _ = self.channel.name.split('_')
        channel = self.fv.get_channel(wsname + '_TGTS')
        obj = channel.opmon.get_plugin('Targets')
        selected = list(obj.get_selected_targets())
        for tgt in selected:
            try:
                self.make_target(tgt.name, tgt.ra, tgt.dec, tgt.equinox)

            except Exception as e:
                errmsg = "Failed to process target: %s" % (str(e))
                self.fv.show_error(errmsg)
                self.logger.error(errmsg, exc_info=True)

    def clear_targets_cb(self):
        self.targets = []

        self.draw_targets()
        return True

    def btn_down_cb(self, canvas, event, data_x, data_y):
        self.logger.debug("cursor at %f,%f" % (data_x, data_y))
        image = self.fitsimage.get_image()
        ra_deg, dec_deg = image.pixtoradec(data_x, data_y)
        self._set_radec(ra_deg, dec_deg)
        self.logger.debug("cursor callback done")
        return True

    def redraw_cb(self, viewer, whence):
        if not self.gui_up or whence >= 3:
            return
        # user may have changed pan position--pick up new pointing
        self.redo()

    def send_pointing_cb(self):
        obj = self.channel.opmon.get_plugin('FindImage')
        obj.set_pointing(self.ctr_ra_deg, self.ctr_dec_deg,
                         self.ctr_equinox, 'none')

    def __str__(self):
        return 'hscplanner'
